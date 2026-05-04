"""GCO analytics environment command group.

Provides the ``gco analytics`` sub-commands:

* ``enable`` / ``disable`` / ``status`` — flip the
  ``analytics_environment.enabled`` toggle in ``cdk.json``.
* ``users add`` / ``users list`` / ``users remove`` — manage Cognito
  users against the auto-discovered pool id from ``gco-analytics``.
* ``studio login`` — SRP-authenticate against Cognito and fetch a
  SageMaker Studio presigned URL from ``/studio/login`` on the
  existing ``gco-api-gateway``.
* ``doctor`` — pre-flight checks before ``gco stacks deploy
  gco-analytics``.

The Click wiring mirrors ``stacks_cmd.py::fsx_cmd`` exactly. Every
command delegates to helpers in :mod:`cli.analytics_user_mgmt` so the
command layer stays thin and testable via ``click.testing.CliRunner``.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from typing import Any

import click

from ..config import GCOConfig
from ..output import get_output_formatter

pass_config = click.make_pass_decorator(GCOConfig, ensure=True)


_STACK_MISSING_MESSAGE = (
    "gco-analytics stack not deployed — run `gco analytics enable` then "
    "`gco stacks deploy gco-analytics`"
)


@click.group()
@pass_config
def analytics(config: Any) -> None:
    """Manage the GCO analytics (SageMaker Studio + EMR) environment."""


# ---------------------------------------------------------------------------
# Toggle commands — enable / disable / status (Task 14.1)
# ---------------------------------------------------------------------------


@analytics.command("status")
@pass_config
def analytics_status(config: Any) -> None:
    """Show the current analytics environment toggle state from cdk.json."""
    from ..stacks import get_analytics_config

    formatter = get_output_formatter(config)
    try:
        current = get_analytics_config()
        formatter.print_info("Analytics environment config:")
        formatter.print(current)
    except Exception as exc:  # noqa: BLE001 — surface every loader error
        formatter.print_error(f"Failed to read analytics config: {exc}")
        sys.exit(1)


@analytics.command("enable")
@click.option("--hyperpod", is_flag=True, help="Also enable SageMaker HyperPod job submission.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@pass_config
def analytics_enable(config: Any, hyperpod: bool, yes: bool) -> None:
    """Enable the analytics environment in cdk.json.

    Flips ``analytics_environment.enabled`` to ``true``; ``--hyperpod``
    additionally flips ``analytics_environment.hyperpod.enabled``.
    Prints the follow-up ``gco stacks deploy gco-analytics`` command —
    does not deploy automatically.
    """
    from ..stacks import get_analytics_config, update_analytics_config

    formatter = get_output_formatter(config)

    if not yes:
        formatter.print_info("Analytics environment will be enabled in cdk.json.")
        if hyperpod:
            formatter.print_info("  Hyperpod sub-toggle will also be enabled.")
        click.confirm("\nEnable the analytics environment?", abort=True)

    try:
        current = get_analytics_config()
        # Preserve everything the operator has set under ``hyperpod`` —
        # the underlying helper replaces nested blocks wholesale, so we
        # have to rebuild the sub-dict with only the field we own.
        hyperpod_block = dict(current.get("hyperpod") or {})
        if hyperpod:
            hyperpod_block["enabled"] = True
        hyperpod_block.setdefault("enabled", False)

        update_analytics_config({"enabled": True, "hyperpod": hyperpod_block})
        formatter.print_success("Analytics environment enabled in cdk.json")
        formatter.print_info("Run `gco stacks deploy gco-analytics` to apply changes")
    except Exception as exc:  # noqa: BLE001 — user-facing error from file I/O
        formatter.print_error(f"Failed to enable analytics environment: {exc}")
        sys.exit(1)


@analytics.command("disable")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@pass_config
def analytics_disable(config: Any, yes: bool) -> None:
    """Disable the analytics environment in cdk.json.

    Only flips ``analytics_environment.enabled`` to ``false``; the
    ``hyperpod`` / ``cognito`` / ``efs`` sub-blocks are left untouched
    so the operator's existing preferences survive a disable/enable
    cycle.
    """
    from ..stacks import update_analytics_config

    formatter = get_output_formatter(config)

    if not yes:
        formatter.print_warning("This will disable the analytics environment.")
        formatter.print_warning(
            "Existing SageMaker Studio / Cognito / EMR resources will be destroyed on next deploy."
        )
        click.confirm("Are you sure?", abort=True)

    try:
        update_analytics_config({"enabled": False})
        formatter.print_success("Analytics environment disabled in cdk.json")
        formatter.print_info("Run `gco stacks destroy gco-analytics` to tear down resources")
    except Exception as exc:  # noqa: BLE001 — user-facing error from file I/O
        formatter.print_error(f"Failed to disable analytics environment: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Users subgroup (Task 14.2)
# ---------------------------------------------------------------------------


@analytics.group("users")
@pass_config
def users_cmd(config: Any) -> None:
    """Manage Cognito users who can sign in to SageMaker Studio."""


def _require_cognito_pool_id(config: Any) -> tuple[str, str]:
    """Return ``(pool_id, region)`` or exit with the documented error message."""
    from ..analytics_user_mgmt import discover_cognito_pool_id

    formatter = get_output_formatter(config)
    region = config.api_gateway_region
    pool_id = discover_cognito_pool_id(region, config.project_name)
    if not pool_id:
        formatter.print_error(_STACK_MISSING_MESSAGE)
        sys.exit(1)
    return pool_id, region


@users_cmd.command("add")
@click.option("--username", required=True, help="Cognito username to create.")
@click.option("--email", help="Email address for the new user (optional).")
@click.option(
    "--no-email",
    is_flag=True,
    help="Suppress the Cognito welcome email (MessageAction=SUPPRESS).",
)
@pass_config
def users_add(config: Any, username: str, email: str | None, no_email: bool) -> None:
    """Create a Cognito user and print the temporary password exactly once."""
    from botocore.exceptions import ClientError

    from ..analytics_user_mgmt import admin_create_user

    formatter = get_output_formatter(config)
    pool_id, region = _require_cognito_pool_id(config)

    try:
        _, temporary_password = admin_create_user(
            pool_id=pool_id,
            region=region,
            username=username,
            email=email,
            suppress_email=no_email,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        formatter.print_error(f"Failed to create user {username}: {error_code}")
        sys.exit(1)

    formatter.print_success(f"Created Cognito user: {username}")
    if temporary_password:
        formatter.print_info(f"Temporary password (printed exactly once): {temporary_password}")
    else:
        formatter.print_info(
            "Cognito did not return a temporary password. "
            "If --no-email was passed, set one via "
            "`aws cognito-idp admin-set-user-password`."
        )


@users_cmd.command("list")
@click.option("--as-json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
@pass_config
def users_list(config: Any, as_json: bool) -> None:
    """List Cognito users in the analytics user pool."""
    from botocore.exceptions import ClientError

    from ..analytics_user_mgmt import list_users as _list_users

    formatter = get_output_formatter(config)
    pool_id, region = _require_cognito_pool_id(config)

    try:
        users = _list_users(pool_id, region)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        formatter.print_error(f"Failed to list users: {error_code}")
        sys.exit(1)

    if as_json:
        print(json.dumps(users, indent=2))
        return
    formatter.print(users)


@users_cmd.command("remove")
@click.option("--username", required=True, help="Cognito username to remove.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@pass_config
def users_remove(config: Any, username: str, yes: bool) -> None:
    """Delete a Cognito user from the analytics user pool."""
    from botocore.exceptions import ClientError

    from ..analytics_user_mgmt import admin_delete_user

    formatter = get_output_formatter(config)
    pool_id, region = _require_cognito_pool_id(config)

    if not yes:
        click.confirm(f"Delete Cognito user '{username}'?", abort=True)

    try:
        admin_delete_user(pool_id, region, username)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        formatter.print_error(f"Failed to delete user {username}: {error_code}")
        sys.exit(1)

    formatter.print_success(f"Deleted Cognito user: {username}")


# ---------------------------------------------------------------------------
# Studio login subgroup (Task 14.3)
# ---------------------------------------------------------------------------


@analytics.group("studio")
@pass_config
def studio_cmd(config: Any) -> None:
    """SageMaker Studio helpers (login, etc.)."""


@studio_cmd.command("login")
@click.option("--username", required=True, help="Cognito username to sign in with.")
@click.option(
    "--password",
    envvar="GCO_STUDIO_PASSWORD",
    help="Password (also read from $GCO_STUDIO_PASSWORD; prompted otherwise).",
)
@click.option("--api-url", help="Override the API Gateway base URL (otherwise auto-discovered).")
@click.option("--open", "open_browser", is_flag=True, help="Open the URL in the default browser.")
@pass_config
def studio_login(
    config: Any,
    username: str,
    password: str | None,
    api_url: str | None,
    open_browser: bool,
) -> None:
    """Sign in to SageMaker Studio via Cognito SRP and print the presigned URL."""
    from botocore.exceptions import ClientError

    from ..analytics_user_mgmt import (
        discover_api_endpoint,
        discover_cognito_client_id,
        discover_cognito_pool_id,
        fetch_studio_url,
        srp_authenticate,
    )

    formatter = get_output_formatter(config)
    region = config.api_gateway_region
    project_name = config.project_name

    pool_id = discover_cognito_pool_id(region, project_name)
    client_id = discover_cognito_client_id(region, project_name)
    if not pool_id or not client_id:
        formatter.print_error(_STACK_MISSING_MESSAGE)
        sys.exit(1)

    api_base = (
        api_url
        or discover_api_endpoint(region, project_name)
        or os.environ.get("GCO_API_GATEWAY_URL")
    )
    if not api_base:
        formatter.print_error(
            "Could not resolve API Gateway endpoint — pass --api-url or deploy gco-api-gateway."
        )
        sys.exit(1)

    if password is None:
        password = click.prompt("Password", hide_input=True)

    try:
        tokens = srp_authenticate(
            pool_id=pool_id,
            client_id=client_id,
            username=username,
            password=password,
            region=region,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        formatter.print_error(f"Cognito authentication failed: {error_code}")
        sys.exit(1)

    id_token = tokens.get("IdToken")
    if not id_token:
        formatter.print_error("Cognito authentication failed: no IdToken returned")
        sys.exit(1)

    try:
        url, _, _ = fetch_studio_url(api_base, id_token)
    except urllib.error.HTTPError as exc:
        correlation_id = exc.headers.get("x-amzn-RequestId") if exc.headers else "N/A"
        formatter.print_error(
            f"login failed: HTTP {exc.code}, correlation_id={correlation_id or 'N/A'}"
        )
        sys.exit(2)
    except urllib.error.URLError as exc:
        formatter.print_error(f"login failed: network error: {exc.reason!r}")
        sys.exit(2)
    except ValueError as exc:
        formatter.print_error(f"login failed: {exc}")
        sys.exit(2)

    # Print the URL on its own line for pipe-friendliness.
    click.echo(url)
    if open_browser:
        click.launch(url)


# ---------------------------------------------------------------------------
# Doctor subcommand (Task 14.5)
# ---------------------------------------------------------------------------


@analytics.command("doctor")
@pass_config
def analytics_doctor(config: Any) -> None:
    """Run pre-flight checks before `gco stacks deploy gco-analytics`.

    Exits non-zero on any failing check. Each check prints ``✓``/``✗``
    plus a short remediation line so the operator knows exactly what
    to fix.
    """
    from ..analytics_user_mgmt import (
        check_ssm_parameter,
        check_stack_complete,
        scan_orphan_analytics_resources,
    )
    from ..config import _load_cdk_json
    from ..stacks import _find_cdk_json

    formatter = get_output_formatter(config)
    any_failed = False

    def _emit(name: str, ok: bool, remediation: str) -> None:
        nonlocal any_failed
        if ok:
            click.echo(f"  ✓ {name}")
        else:
            any_failed = True
            click.echo(f"  ✗ {name}")
            if remediation:
                click.echo(f"    → {remediation}")

    # 1. cdk.json parses
    cdk_json_path = _find_cdk_json()
    if cdk_json_path is None:
        _emit(
            "cdk.json present",
            False,
            "run `gco analytics doctor` from the project root (cdk.json not found).",
        )
    else:
        try:
            with open(cdk_json_path, encoding="utf-8") as fh:
                json.load(fh)
            _emit("cdk.json parses as JSON", True, "")
        except json.JSONDecodeError as exc:
            _emit(
                "cdk.json parses as JSON",
                False,
                f"fix malformed JSON at {cdk_json_path}: {exc.msg} (line {exc.lineno})",
            )

    # 2. Prerequisite stacks healthy
    for region, stack_name in (
        (config.global_region, f"{config.project_name}-global"),
        (config.api_gateway_region, f"{config.project_name}-api-gateway"),
    ):
        ok, remediation = check_stack_complete(region, stack_name)
        _emit(
            f"{stack_name} is CREATE_COMPLETE",
            ok,
            remediation or f"deploy with `gco stacks deploy {stack_name}`",
        )

    cdk_regions = _load_cdk_json()
    regional_regions = cdk_regions.get("regional", []) if isinstance(cdk_regions, dict) else []
    for region in regional_regions:
        stack_name = f"{config.project_name}-{region}"
        ok, remediation = check_stack_complete(region, stack_name)
        _emit(
            f"{stack_name} is CREATE_COMPLETE",
            ok,
            remediation or f"deploy with `gco stacks deploy {stack_name}`",
        )

    # 3. SSM cluster-shared-bucket parameters exist
    ssm_prefix = "/gco/cluster-shared-bucket"
    for suffix in ("name", "arn", "region"):
        param = f"{ssm_prefix}/{suffix}"
        ok, remediation = check_ssm_parameter(config.global_region, param)
        _emit(
            f"SSM parameter {param} exists",
            ok,
            remediation and f"deploy {config.project_name}-global first ({remediation})",
        )

    # 4. No orphaned retained analytics resources
    orphan_cmds = scan_orphan_analytics_resources(config.api_gateway_region)
    _emit(
        "no orphaned retained analytics resources",
        not orphan_cmds,
        "; ".join(orphan_cmds) if orphan_cmds else "",
    )

    if any_failed:
        formatter.print_error("Doctor checks failed — see remediation lines above.")
        sys.exit(1)
    formatter.print_success("All pre-flight checks passed.")


# ---------------------------------------------------------------------------
# Iterate subcommand (Task 14.6)
# ---------------------------------------------------------------------------


@analytics.command(name="iterate")
@click.argument(
    "phase",
    type=click.Choice(["status", "deploy", "test", "destroy", "verify-clean", "all"]),
)
@click.option(
    "-r",
    "--region",
    default=None,
    help="AWS region (default: api_gateway region from cdk.json).",
)
@click.option("--dry-run", is_flag=True, help="Print the planned action without executing.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def iterate(
    ctx: click.Context,
    phase: str,
    region: str | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Drive the analytics deploy→test→destroy iteration loop.

    Thin wrapper around scripts/test_analytics_lifecycle.py. Exits with
    the script's return code.
    """
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "test_analytics_lifecycle.py"
    if not script.exists():
        click.echo(f"error: lifecycle script not found at {script}", err=True)
        ctx.exit(1)
    argv = [sys.executable, str(script), phase]
    if region:
        argv.extend(["--region", region])
    if dry_run:
        argv.append("--dry-run")
    if json_output:
        argv.append("--json")
    result = subprocess.run(argv, check=False)
    ctx.exit(result.returncode)
