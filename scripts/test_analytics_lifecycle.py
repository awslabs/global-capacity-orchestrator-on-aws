#!/usr/bin/env python3
"""Drive the analytics-environment deploy/test/destroy iteration loop.

This script is the programmatic half of the analytics-environment
``Lifecycle_Script``.
It probes AWS for the current state of the analytics-environment stacks, decides
the next action to take, and optionally shells out to ``cdk`` to execute it.
The decision logic is kept in pure functions (``next_step``,
``format_remediation``) so they can be unit-tested without any AWS calls at all;
the only effectful piece is ``detect_state`` (boto3 read-only calls) and the
``cdk`` subprocess invocation inside ``main``.

Design invariants:

* ``detect_state`` is idempotent and read-only. Two calls against the same AWS
  state produce equal ``LifecycleState`` values.
* ``next_step`` is a pure total function of ``(state, phase)``. No AWS calls.
* ``format_remediation`` is a pure total function of ``state``. No AWS calls.
* ``main`` is the only effectful entry point. Its ``--dry-run`` flag makes it
  effect-free too — the subprocess call to ``cdk`` is the only side effect that
  distinguishes a non-dry run, and it is skipped under ``--dry-run``.

Typical invocation::

    python3 scripts/test_analytics_lifecycle.py status --json
    python3 scripts/test_analytics_lifecycle.py deploy --dry-run
    python3 scripts/test_analytics_lifecycle.py all
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "LifecycleState",
    "detect_state",
    "format_remediation",
    "main",
    "next_step",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sentinel returned in ``LifecycleState.stacks`` when a stack is not present in
# CloudFormation. Deliberately distinct from any real CFN status so callers can
# branch on it cleanly.
STACK_ABSENT = "DOES_NOT_EXIST"

# The three SSM parameters published by ``GCOGlobalStack`` for the always-on
# cluster-shared bucket. Stored as a tuple so the order is stable for
# deterministic output.
SSM_PARAM_SUFFIXES: tuple[str, ...] = ("name", "arn", "region")
SSM_PARAM_PREFIX = "/gco/cluster-shared-bucket"

# CloudFormation status strings the script treats as "healthy".
HEALTHY_CFN_STATUSES = frozenset(
    {
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
        "IMPORT_COMPLETE",
        "UPDATE_ROLLBACK_COMPLETE",  # rollback still leaves stack usable
    }
)

# Stack statuses that indicate the stack exists but is stuck.
STUCK_CFN_STATUSES = frozenset(
    {
        "CREATE_FAILED",
        "ROLLBACK_COMPLETE",
        "ROLLBACK_FAILED",
        "ROLLBACK_IN_PROGRESS",
        "UPDATE_ROLLBACK_FAILED",
        "UPDATE_ROLLBACK_IN_PROGRESS",
        "DELETE_FAILED",
    }
)

# Name-tag prefixes used to identify analytics-owned orphans.
EFS_NAME_TAG_PREFIX = "gco-analytics-studio-efs"
COGNITO_POOL_NAME_PREFIX = "gco-studio"

# Valid phase names accepted by ``next_step`` and the CLI subcommands.
VALID_PHASES: tuple[str, ...] = ("deploy", "test", "destroy", "verify-clean")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleState:
    """Snapshot of the analytics-environment lifecycle state.

    Frozen so two ``detect_state`` calls can be compared for equality with
    ``==`` directly — the idempotence contract.
    """

    region: str
    project_name: str
    stacks: dict[str, str] = field(default_factory=dict)
    ssm_params: dict[str, bool] = field(default_factory=dict)
    retained_efs_count: int = 0
    retained_cognito_pool_count: int = 0
    analytics_enabled: bool = False
    hyperpod_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view for machine-readable output."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers (pure)
# ---------------------------------------------------------------------------


def _expected_stack_names(project_name: str) -> list[str]:
    """Return the ordered list of stack names the analytics loop cares about.

    Ordering matters for deploy/destroy phase decisions — ``detect_state``
    iterates this list to populate ``LifecycleState.stacks``.

    The analytics environment lives in the API gateway region and does not
    depend on any regional EKS cluster, so only the three collocated stacks
    are queried.
    """
    return [
        f"{project_name}-global",
        f"{project_name}-api-gateway",
        f"{project_name}-analytics",
    ]


def _expected_ssm_param_names() -> list[str]:
    """Return the fully-qualified SSM parameter names owned by the shared bucket."""
    return [f"{SSM_PARAM_PREFIX}/{suffix}" for suffix in SSM_PARAM_SUFFIXES]


def _load_cdk_context(cdk_json_path: str) -> dict[str, Any]:
    """Load and return the ``context`` block of ``cdk.json``, or ``{}``."""
    path = Path(cdk_json_path)
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    context = payload.get("context", {})
    return context if isinstance(context, dict) else {}


def _read_analytics_toggles(cdk_context: dict[str, Any]) -> tuple[bool, bool]:
    """Extract ``(analytics_enabled, hyperpod_enabled)`` from ``cdk.json`` context."""
    analytics_block = cdk_context.get("analytics_environment") or {}
    analytics_enabled = bool(analytics_block.get("enabled", False))
    hyperpod_block = analytics_block.get("hyperpod") or {}
    hyperpod_enabled = bool(hyperpod_block.get("enabled", False))
    return analytics_enabled, hyperpod_enabled


def _default_region(cdk_context: dict[str, Any]) -> str | None:
    """Return the api-gateway region from ``cdk.json`` or ``None``."""
    regions = cdk_context.get("deployment_regions") or {}
    value = regions.get("api_gateway")
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------
# detect_state (effectful but read-only)
# ---------------------------------------------------------------------------


def detect_state(
    region: str,
    project_name: str,
    boto3_session: Any | None = None,
    cdk_json_path: str = "cdk.json",
) -> LifecycleState:
    """Probe AWS + ``cdk.json`` and return the current ``LifecycleState``.

    This function has no mutation effects on AWS — only read-only
    ``describe_*`` / ``get_parameter`` / ``list_*`` calls. Missing resources
    are folded into :data:`STACK_ABSENT` / ``False`` values rather than raised
    as exceptions so callers can pipe the result straight into
    :func:`next_step`.

    Args:
        region: AWS region to query (api-gateway region by convention).
        project_name: GCO project name used as stack-name prefix.
        boto3_session: Optional ``boto3.Session`` for test injection. When
            ``None`` a fresh session is created.
        cdk_json_path: Path to ``cdk.json``. Relative paths are resolved
            against the process CWD.

    Returns:
        A frozen :class:`LifecycleState` reflecting the observed state.
    """
    # Local imports so the module can be imported (and `--help` run) on
    # machines without boto3 configured.
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    session = boto3_session or boto3.Session()

    # --- Stacks ------------------------------------------------------------
    cfn = session.client("cloudformation", region_name=region)
    stacks: dict[str, str] = {}
    for stack_name in _expected_stack_names(project_name):
        try:
            resp = cfn.describe_stacks(StackName=stack_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            message = str(exc.response.get("Error", {}).get("Message", ""))
            if code == "ValidationError" and "does not exist" in message:
                stacks[stack_name] = STACK_ABSENT
                continue
            # Any other ClientError is surfaced via the status string so
            # operators see it in `format_remediation` output.
            stacks[stack_name] = f"ERROR:{code}"
            continue
        except BotoCoreError as exc:
            stacks[stack_name] = f"ERROR:BotoCoreError:{type(exc).__name__}"
            continue
        stack_list = resp.get("Stacks") or []
        if not stack_list:
            stacks[stack_name] = STACK_ABSENT
        else:
            stacks[stack_name] = str(stack_list[0].get("StackStatus", "UNKNOWN"))

    # --- SSM parameters ----------------------------------------------------
    ssm = session.client("ssm", region_name=region)
    ssm_params: dict[str, bool] = {}
    for param_name in _expected_ssm_param_names():
        try:
            ssm.get_parameter(Name=param_name)
            ssm_params[param_name] = True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ParameterNotFound":
                ssm_params[param_name] = False
            else:
                ssm_params[param_name] = False
        except BotoCoreError:
            ssm_params[param_name] = False

    # --- Retained EFS orphans ---------------------------------------------
    retained_efs = 0
    try:
        efs = session.client("efs", region_name=region)
        resp = efs.describe_file_systems()
        for fs in resp.get("FileSystems") or []:
            name = str(fs.get("Name") or "")
            if name.startswith(EFS_NAME_TAG_PREFIX):
                retained_efs += 1
    except (ClientError, BotoCoreError):
        # Leave retained_efs at 0 — operator will see the stack ERROR entry
        # instead.
        pass

    # --- Retained Cognito orphans -----------------------------------------
    retained_pools = 0
    try:
        cognito = session.client("cognito-idp", region_name=region)
        resp = cognito.list_user_pools(MaxResults=60)
        for pool in resp.get("UserPools") or []:
            name = str(pool.get("Name") or "")
            if name.startswith(COGNITO_POOL_NAME_PREFIX):
                retained_pools += 1
    except (ClientError, BotoCoreError):
        # Leave retained_pools at 0 — the operator already sees the
        # gco-analytics stack ERROR entry (logged by the stack loop
        # above) and can decide whether the orphan scan failure is
        # material. Re-raising here would mask that primary signal
        # behind a generic traceback.
        pass

    # --- cdk.json toggles -------------------------------------------------
    cdk_context = _load_cdk_context(cdk_json_path)
    analytics_enabled, hyperpod_enabled = _read_analytics_toggles(cdk_context)

    return LifecycleState(
        region=region,
        project_name=project_name,
        stacks=stacks,
        ssm_params=ssm_params,
        retained_efs_count=retained_efs,
        retained_cognito_pool_count=retained_pools,
        analytics_enabled=analytics_enabled,
        hyperpod_enabled=hyperpod_enabled,
    )


# ---------------------------------------------------------------------------
# next_step (pure)
# ---------------------------------------------------------------------------


def _stack_present(state: LifecycleState, stack_name: str) -> bool:
    """Return ``True`` iff ``stack_name`` exists in AWS (any status)."""
    status = state.stacks.get(stack_name, STACK_ABSENT)
    return status != STACK_ABSENT and not status.startswith("ERROR:")


def _stack_healthy(state: LifecycleState, stack_name: str) -> bool:
    """Return ``True`` iff ``stack_name`` is in a ``*_COMPLETE`` state."""
    return state.stacks.get(stack_name, STACK_ABSENT) in HEALTHY_CFN_STATUSES


def _orphan_reason(state: LifecycleState) -> str:
    """Render a short orphan-count summary for inclusion in reason strings."""
    parts = []
    if state.retained_efs_count > 0:
        parts.append(f"{state.retained_efs_count} EFS file system(s)")
    if state.retained_cognito_pool_count > 0:
        parts.append(f"{state.retained_cognito_pool_count} Cognito user pool(s)")
    if not parts:
        return ""
    return (
        " Consider setting analytics_environment.removal_policy=destroy to "
        f"clean up orphans ({', '.join(parts)})."
    )


def next_step(state: LifecycleState, phase: str) -> dict[str, Any]:
    """Return the next action to take for ``phase`` given the current ``state``.

    Pure total function. No AWS calls, no file I/O.

    Args:
        state: Result of :func:`detect_state`.
        phase: One of ``"deploy"``, ``"test"``, ``"destroy"``, ``"verify-clean"``.

    Returns:
        A dict with keys ``action`` (short verb), ``command`` (copy-paste
        shell invocation), ``reason`` (human-readable explanation), and
        ``done`` (``bool`` — ``True`` when the phase has no remaining work).
    """
    project = state.project_name
    global_name = f"{project}-global"
    apigw_name = f"{project}-api-gateway"
    analytics_name = f"{project}-analytics"

    def _plan(action: str, command: str, reason: str, done: bool) -> dict[str, Any]:
        return {"action": action, "command": command, "reason": reason, "done": done}

    if phase == "deploy":
        if not _stack_present(state, global_name):
            return _plan(
                "deploy-gco-global",
                f"cdk deploy {global_name}",
                f"{global_name} is missing — deploy it first so the "
                "cluster-shared bucket SSM parameters exist.",
                False,
            )
        if not _stack_present(state, apigw_name):
            return _plan(
                "deploy-api-gateway",
                f"cdk deploy {apigw_name}",
                f"{apigw_name} is missing — deploy it before analytics so "
                "the /studio/* routes can be wired in.",
                False,
            )
        if state.analytics_enabled and not _stack_present(state, analytics_name):
            return _plan(
                "deploy-analytics",
                f"cdk deploy {analytics_name}",
                f"{analytics_name} is missing but analytics_environment."
                "enabled=true in cdk.json.",
                False,
            )
        return _plan("noop", "", "All required stacks already present.", True)

    if phase == "test":
        required = [global_name, apigw_name]
        if state.analytics_enabled:
            required.append(analytics_name)
        for stack in required:
            if not _stack_healthy(state, stack):
                status = state.stacks.get(stack, STACK_ABSENT)
                return _plan(
                    "wait",
                    "",
                    f"Cannot run smoke tests: {stack} is in status {status!r}, "
                    "expected *_COMPLETE.",
                    False,
                )
        missing_ssm = [name for name, present in state.ssm_params.items() if not present]
        if missing_ssm:
            return _plan(
                "wait",
                "",
                "SSM parameters missing: "
                + ", ".join(missing_ssm)
                + ". Redeploy gco-global to publish them.",
                False,
            )
        return _plan(
            "run-smoke-tests",
            "pytest tests/test_analytics_stack.py tests/test_cluster_shared_bucket.py -v",
            "All stacks healthy and SSM parameters present.",
            True,
        )

    if phase == "destroy":
        remediation = _orphan_reason(state)
        if _stack_present(state, analytics_name):
            return _plan(
                "destroy-analytics",
                f"cdk destroy {analytics_name} --force",
                f"{analytics_name} exists — tear it down first.{remediation}",
                False,
            )
        if _stack_present(state, global_name):
            return _plan(
                "destroy-global",
                f"cdk destroy {global_name} --force",
                f"{global_name} is the last remaining stack.{remediation}",
                False,
            )
        return _plan("noop", "", f"All stacks already destroyed.{remediation}", True)

    if phase == "verify-clean":
        if state.retained_efs_count > 0:
            return _plan(
                "cleanup-efs",
                "aws efs describe-file-systems",
                f"{state.retained_efs_count} orphaned EFS file system(s) still "
                "carry the gco-analytics-studio-efs name prefix.",
                False,
            )
        if state.retained_cognito_pool_count > 0:
            return _plan(
                "cleanup-cognito",
                "aws cognito-idp list-user-pools --max-results 60",
                f"{state.retained_cognito_pool_count} orphaned Cognito user "
                "pool(s) still carry the gco-studio name prefix.",
                False,
            )
        stale_ssm = [name for name, present in state.ssm_params.items() if present]
        # SSM params are expected to be present when gco-global is deployed,
        # so they only count as "stale" if gco-global has been destroyed.
        if stale_ssm and not _stack_present(state, f"{project}-global"):
            return _plan(
                "cleanup-ssm",
                "aws ssm delete-parameters --names " + " ".join(stale_ssm),
                "Cluster-shared-bucket SSM parameters remain but "
                f"{project}-global is gone: " + ", ".join(stale_ssm),
                False,
            )
        return _plan("clean", "", "No retained orphans or stale SSM parameters detected.", True)

    # Unknown phase — callers should have validated before calling.
    return _plan("error", "", f"Unknown phase: {phase!r}", True)


# ---------------------------------------------------------------------------
# format_remediation (pure)
# ---------------------------------------------------------------------------


def format_remediation(state: LifecycleState) -> str:
    """Return a human-readable remediation summary for ``state``.

    Flags: missing SSM parameters, stuck stacks, retained orphans, and
    ``cdk.json`` toggle / real-world mismatches. Returns the sentinel
    ``"No remediation needed — state is clean."`` when nothing is wrong.
    """
    lines: list[str] = []

    # Stuck / errored stacks.
    for name, status in state.stacks.items():
        if status in STUCK_CFN_STATUSES:
            lines.append(
                f"Stack {name} is stuck in {status}. "
                f"Consider `aws cloudformation delete-stack --stack-name {name} "
                f"--region {state.region}`."
            )
        elif status.startswith("ERROR:"):
            lines.append(
                f"Stack {name} could not be described ({status}). "
                "Check AWS credentials and region."
            )

    # Missing SSM params when gco-global is present (a deployment regression).
    global_name = f"{state.project_name}-global"
    global_present = _stack_present(state, global_name)
    if global_present:
        missing = [name for name, present in state.ssm_params.items() if not present]
        if missing:
            lines.append(
                f"{global_name} exists but these SSM parameters are missing: "
                + ", ".join(missing)
                + ". Re-deploy gco-global to republish them."
            )

    # Orphans.
    if state.retained_efs_count > 0:
        lines.append(
            f"{state.retained_efs_count} orphaned EFS file system(s) carry the "
            "gco-analytics-studio-efs name prefix. Set "
            'analytics_environment.efs.removal_policy="destroy" and rerun '
            "destroy, or delete them manually via `aws efs delete-file-system`."
        )
    if state.retained_cognito_pool_count > 0:
        lines.append(
            f"{state.retained_cognito_pool_count} orphaned Cognito user pool(s) "
            "carry the gco-studio name prefix. Set "
            'analytics_environment.cognito.removal_policy="destroy" and '
            "rerun destroy, or delete them via "
            "`aws cognito-idp delete-user-pool`."
        )

    # Toggle / real-world mismatch.
    analytics_name = f"{state.project_name}-analytics"
    analytics_present = _stack_present(state, analytics_name)
    if state.analytics_enabled and not analytics_present:
        lines.append(
            "cdk.json has analytics_environment.enabled=true but "
            f"{analytics_name} is not deployed. Run "
            f"`cdk deploy {analytics_name}` to reconcile."
        )
    if not state.analytics_enabled and analytics_present:
        lines.append(
            f"{analytics_name} is deployed but cdk.json has "
            "analytics_environment.enabled=false. Either flip the toggle or "
            f"run `cdk destroy {analytics_name}`."
        )

    if not lines:
        return "No remediation needed — state is clean."
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main (effectful)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Kept separate for testability.

    Global flags (``--region``, ``--dry-run``, ``--json``, ``--cdk-json-path``,
    ``--project-name``) are declared on both the top-level parser and every
    subparser so operators can write either ``status --dry-run --json`` or
    ``--dry-run --json status`` — argparse's default behaviour requires global
    flags *before* the subcommand, which is surprising on a script that reads
    like a phase-first verb.
    """

    def _add_global_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--region", default=None, help="AWS region (default: cdk.json)")
        p.add_argument(
            "--project-name",
            default="gco",
            help="Project name / stack prefix (default: gco)",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the planned action without executing it.",
        )
        p.add_argument(
            "--json",
            dest="json_output",
            action="store_true",
            help="Emit machine-readable JSON on stdout.",
        )
        p.add_argument(
            "--cdk-json-path",
            default="cdk.json",
            help="Path to cdk.json (default: cdk.json).",
        )

    parser = argparse.ArgumentParser(
        prog="test_analytics_lifecycle",
        description=("Drive the analytics-environment deploy/test/destroy iteration " "loop."),
    )
    _add_global_flags(parser)
    sub = parser.add_subparsers(dest="phase", required=True, metavar="PHASE")
    for name in ("status", "deploy", "test", "destroy", "verify-clean", "all"):
        child = sub.add_parser(name, help=f"Run the {name!r} phase.")
        _add_global_flags(child)
    return parser


def _emit(payload: dict[str, Any], json_output: bool) -> None:
    """Print ``payload`` in either JSON or human-readable form."""
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _run_phase(
    phase: str,
    state: LifecycleState,
    dry_run: bool,
    json_output: bool,
) -> int:
    """Execute a single phase. Returns the exit code to propagate."""
    plan = next_step(state, phase)
    plan_payload = {
        "phase": phase,
        "action": plan["action"],
        "command": plan["command"],
        "reason": plan["reason"],
        "done": plan["done"],
    }
    _emit(plan_payload, json_output)

    if dry_run or not plan["command"] or plan["action"] in {"noop", "wait", "clean"}:
        return 0
    if plan["action"].startswith("cleanup-"):
        # Cleanup actions are advisory only — don't run them automatically.
        return 0

    # Only deploy/destroy actions actually shell out to cdk.
    if plan["action"].startswith("deploy-") or plan["action"].startswith("destroy-"):
        # ``plan["command"]`` is composed entirely from constants we ship
        # (``next_step`` only emits strings of the form ``cdk {deploy,destroy}
        # <project-name>-<suffix>`` with no user input in the tail), so
        # ``shlex.split`` + ``shell=False`` is both safer and removes the
        # semgrep ``subprocess-shell-true`` finding. If a future caller adds
        # a variable tail to these commands, keep it inside ``_plan`` and
        # pass the argv list through this helper unchanged.
        argv = shlex.split(plan["command"])
        try:
            result = subprocess.run(argv, check=False)
        except OSError as exc:
            print(f"error: failed to invoke cdk: {exc}", file=sys.stderr)
            return 1
        return int(result.returncode != 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. See module docstring for CLI usage.

    Returns ``0`` on success, ``1`` on any runtime failure (including stuck
    stacks surfaced by ``format_remediation``), and ``2`` on invalid
    arguments.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits with 2 on invalid arguments — preserve that.
        return int(exc.code) if isinstance(exc.code, int) else 2

    # Resolve region from cdk.json if not provided.
    region = args.region
    if region is None:
        cdk_context = _load_cdk_context(args.cdk_json_path)
        region = _default_region(cdk_context)
    if not region:
        print(
            "error: --region not provided and deployment_regions.api_gateway "
            "missing from cdk.json",
            file=sys.stderr,
        )
        return 2

    # Detect state. Trap AWS-credential / network errors so the user sees a
    # readable message rather than a stacktrace.
    try:
        state = detect_state(
            region=region,
            project_name=args.project_name,
            cdk_json_path=args.cdk_json_path,
        )
    except Exception as exc:  # noqa: BLE001 — user-facing error
        print(f"error: failed to probe AWS state: {exc}", file=sys.stderr)
        return 1

    phase = args.phase

    if phase == "status":
        payload = {
            "state": state.to_dict(),
            "remediation": format_remediation(state),
        }
        _emit(payload, args.json_output)
        # Exit 1 if any stack is stuck so CI gates can fail fast.
        any_stuck = any(s in STUCK_CFN_STATUSES for s in state.stacks.values())
        any_errored = any(s.startswith("ERROR:") for s in state.stacks.values())
        return 1 if (any_stuck or any_errored) else 0

    if phase in VALID_PHASES:
        return _run_phase(phase, state, args.dry_run, args.json_output)

    if phase == "all":
        exit_code = 0
        for sub_phase in VALID_PHASES:
            # Re-detect between phases so "test" sees the post-deploy state.
            sub_state = (
                state
                if sub_phase == "deploy"
                else detect_state(
                    region=region,
                    project_name=args.project_name,
                    cdk_json_path=args.cdk_json_path,
                )
            )
            rc = _run_phase(sub_phase, sub_state, args.dry_run, args.json_output)
            if rc != 0:
                exit_code = rc
                break
        return exit_code

    # Unknown phase — argparse should have already caught this.
    print(f"error: unknown phase {phase!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
