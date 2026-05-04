"""Tests for ``scripts/test_analytics_lifecycle.py`` (Task 15.3).

Covers:

* ``detect_state`` against moto-backed CloudFormation / SSM / EFS /
  cognito-idp, including empty, partial, and full-deployment fixtures.
* ``next_step`` pure-function tests parameterized across the deploy /
  destroy phases.
* ``format_remediation`` on clean state, orphans, toggle mismatches.
* The argparse ``main`` entry point — status / JSON output, invalid
  phase, exit codes on stuck stacks.

Tests avoid global ``boto3.client`` patches; they inject a
``boto3.Session`` into ``detect_state`` so parallel tests stay isolated.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Module-under-test import
# ---------------------------------------------------------------------------
#
# ``scripts/`` is not a package, so the script has to be loaded via
# ``importlib.util``. Caching the loaded module as a module-level constant
# keeps the fixture boilerplate out of every test.

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "test_analytics_lifecycle.py"
_SPEC = importlib.util.spec_from_file_location("analytics_lifecycle", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
lifecycle = importlib.util.module_from_spec(_SPEC)
# Register the module so ``main`` can re-enter argparse without re-import issues.
sys.modules.setdefault("analytics_lifecycle", lifecycle)
_SPEC.loader.exec_module(lifecycle)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_creds_env():
    """Inject deterministic moto-friendly AWS credentials."""
    previous = {
        "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY"),
        "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION"),
    }
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-2"
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def fake_cdk_json(tmp_path):
    """Build a minimal ``cdk.json`` in ``tmp_path`` and return its path as str."""
    path = tmp_path / "cdk.json"
    path.write_text(
        json.dumps(
            {
                "context": {
                    "deployment_regions": {
                        "global": "us-east-2",
                        "api_gateway": "us-east-2",
                        "regional": ["us-east-1"],
                    },
                    "analytics_environment": {
                        "enabled": False,
                        "hyperpod": {"enabled": False},
                    },
                }
            }
        )
    )
    return str(path)


def _noop_template() -> str:
    """Return a minimal valid CloudFormation template as JSON."""
    return json.dumps(
        {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Resources": {"Topic": {"Type": "AWS::SNS::Topic"}},
        }
    )


def _seed_stack(region: str, name: str) -> None:
    boto3.client("cloudformation", region_name=region).create_stack(
        StackName=name, TemplateBody=_noop_template()
    )


def _seed_ssm_params(region: str) -> None:
    ssm = boto3.client("ssm", region_name=region)
    ssm.put_parameter(
        Name="/gco/cluster-shared-bucket/name",
        Value="gco-cluster-shared-000-us-east-2",
        Type="String",
    )
    ssm.put_parameter(
        Name="/gco/cluster-shared-bucket/arn",
        Value="arn:aws:s3:::gco-cluster-shared-000-us-east-2",
        Type="String",
    )
    ssm.put_parameter(Name="/gco/cluster-shared-bucket/region", Value=region, Type="String")


# ---------------------------------------------------------------------------
# detect_state
# ---------------------------------------------------------------------------


class TestDetectState:
    def test_empty_environment(self, aws_creds_env, fake_cdk_json):
        """1. No stacks, no SSM — everything resolves to DOES_NOT_EXIST / False."""
        with mock_aws():
            session = boto3.Session()
            state = lifecycle.detect_state(
                region="us-east-2",
                project_name="gco",
                boto3_session=session,
                cdk_json_path=fake_cdk_json,
            )

        assert state.region == "us-east-2"
        assert state.project_name == "gco"
        assert all(status == lifecycle.STACK_ABSENT for status in state.stacks.values())
        assert set(state.stacks) == {
            "gco-global",
            "gco-api-gateway",
            "gco-analytics",
            "gco-us-east-2",
        }
        assert all(present is False for present in state.ssm_params.values())
        assert state.retained_efs_count == 0
        assert state.retained_cognito_pool_count == 0
        assert state.analytics_enabled is False
        assert state.hyperpod_enabled is False

    def test_post_global_only(self, aws_creds_env, fake_cdk_json):
        """2. After ``gco-global`` is created the other stacks stay absent."""
        with mock_aws():
            _seed_stack("us-east-2", "gco-global")
            session = boto3.Session()
            state = lifecycle.detect_state(
                region="us-east-2",
                project_name="gco",
                boto3_session=session,
                cdk_json_path=fake_cdk_json,
            )

        assert state.stacks["gco-global"] == "CREATE_COMPLETE"
        assert state.stacks["gco-api-gateway"] == lifecycle.STACK_ABSENT
        assert state.stacks["gco-analytics"] == lifecycle.STACK_ABSENT

    def test_full_deployment(self, aws_creds_env, tmp_path):
        """3. All stacks + SSM present → every key populated correctly."""
        cdk_json = tmp_path / "cdk.json"
        cdk_json.write_text(
            json.dumps(
                {
                    "context": {
                        "analytics_environment": {
                            "enabled": True,
                            "hyperpod": {"enabled": True},
                        }
                    }
                }
            )
        )

        with mock_aws():
            for stack in ("gco-global", "gco-api-gateway", "gco-analytics", "gco-us-east-2"):
                _seed_stack("us-east-2", stack)
            _seed_ssm_params("us-east-2")

            session = boto3.Session()
            state = lifecycle.detect_state(
                region="us-east-2",
                project_name="gco",
                boto3_session=session,
                cdk_json_path=str(cdk_json),
            )

        assert all(status == "CREATE_COMPLETE" for status in state.stacks.values())
        assert all(present is True for present in state.ssm_params.values())
        assert state.analytics_enabled is True
        assert state.hyperpod_enabled is True

    def test_idempotence(self, aws_creds_env, fake_cdk_json):
        """6. Two calls against the same state produce equal dataclasses."""
        with mock_aws():
            _seed_stack("us-east-2", "gco-global")
            _seed_stack("us-east-2", "gco-api-gateway")
            _seed_ssm_params("us-east-2")

            session = boto3.Session()
            first = lifecycle.detect_state(
                region="us-east-2",
                project_name="gco",
                boto3_session=session,
                cdk_json_path=fake_cdk_json,
            )
            second = lifecycle.detect_state(
                region="us-east-2",
                project_name="gco",
                boto3_session=session,
                cdk_json_path=fake_cdk_json,
            )

        assert first == second

    def test_cognito_orphan_scan_error_is_swallowed(self, aws_creds_env, fake_cdk_json):
        """Cognito ``list_user_pools`` failures must not abort ``detect_state``.

        The scan is a diagnostic for the ``verify-clean`` phase, not a
        fatal precondition. A throttle or permission error on
        ``cognito-idp:ListUserPools`` should leave ``retained_pools=0``
        and let the rest of the probe continue — the operator still
        sees the primary signal (stack statuses, SSM params, cdk.json
        toggles). Pins the ``except (ClientError, BotoCoreError): pass``
        branch the CodeQL ``py/empty-except`` rule flagged; a future
        cleanup that re-raises instead of passing would break this
        test, which is exactly the signal we want.
        """
        from unittest.mock import MagicMock

        from botocore.exceptions import ClientError

        # Build a session where cognito-idp.list_user_pools raises, while
        # every other service call still lands on moto. We only need to
        # stub the cognito client — CloudFormation / SSM / EFS keep
        # using the real moto-backed session because detect_state reads
        # them via the same Session object.
        with mock_aws():
            real_session = boto3.Session()
            orig_client = real_session.client

            def _client(service_name, **kwargs):
                if service_name == "cognito-idp":
                    mocked = MagicMock()
                    mocked.list_user_pools.side_effect = ClientError(
                        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
                        "ListUserPools",
                    )
                    return mocked
                return orig_client(service_name, **kwargs)

            fake_session = MagicMock(wraps=real_session)
            fake_session.client.side_effect = _client

            state = lifecycle.detect_state(
                region="us-east-2",
                project_name="gco",
                boto3_session=fake_session,
                cdk_json_path=fake_cdk_json,
            )

        # Cognito branch swallowed → count stays zero, rest of the
        # state is populated normally.
        assert state.retained_cognito_pool_count == 0
        assert state.region == "us-east-2"
        # EFS scan used the real moto session so that count should also
        # be zero (no file systems seeded), confirming detect_state did
        # reach the blocks after the Cognito swallow.
        assert state.retained_efs_count == 0


# ---------------------------------------------------------------------------
# next_step — deploy phase
# ---------------------------------------------------------------------------


def _state_with_stacks(present_stacks: set[str], *, analytics_enabled: bool = False):
    """Build a ``LifecycleState`` where only ``present_stacks`` are CREATE_COMPLETE."""
    all_stacks = {"gco-global", "gco-api-gateway", "gco-analytics", "gco-us-east-2"}
    stacks = {
        name: ("CREATE_COMPLETE" if name in present_stacks else lifecycle.STACK_ABSENT)
        for name in all_stacks
    }
    return lifecycle.LifecycleState(
        region="us-east-2",
        project_name="gco",
        stacks=stacks,
        ssm_params={
            "/gco/cluster-shared-bucket/name": "gco-global" in present_stacks,
            "/gco/cluster-shared-bucket/arn": "gco-global" in present_stacks,
            "/gco/cluster-shared-bucket/region": "gco-global" in present_stacks,
        },
        analytics_enabled=analytics_enabled,
    )


class TestNextStepDeploy:
    @pytest.mark.parametrize(
        "present,expected_action",
        [
            (set(), "deploy-gco-global"),
            ({"gco-global"}, "deploy-regional"),
            ({"gco-global", "gco-us-east-2"}, "deploy-analytics"),
        ],
    )
    def test_deploy_phase_walks_in_order(self, present, expected_action):
        state = _state_with_stacks(present, analytics_enabled=True)
        plan = lifecycle.next_step(state, "deploy")
        assert plan["action"] == expected_action
        assert plan["done"] is False

    def test_deploy_phase_noop_when_complete(self):
        state = _state_with_stacks(
            {"gco-global", "gco-api-gateway", "gco-analytics", "gco-us-east-2"},
            analytics_enabled=True,
        )
        plan = lifecycle.next_step(state, "deploy")
        assert plan["action"] == "noop"
        assert plan["done"] is True

    def test_deploy_phase_skips_analytics_when_toggle_off(self):
        state = _state_with_stacks({"gco-global", "gco-us-east-2"}, analytics_enabled=False)
        plan = lifecycle.next_step(state, "deploy")
        assert plan["action"] == "noop"
        assert plan["done"] is True


# ---------------------------------------------------------------------------
# next_step — destroy phase
# ---------------------------------------------------------------------------


class TestNextStepDestroy:
    @pytest.mark.parametrize(
        "present,expected_action",
        [
            (
                {"gco-global", "gco-api-gateway", "gco-analytics", "gco-us-east-2"},
                "destroy-analytics",
            ),
            (
                {"gco-global", "gco-api-gateway", "gco-us-east-2"},
                "destroy-regional",
            ),
            ({"gco-global", "gco-api-gateway"}, "destroy-global"),
        ],
    )
    def test_destroy_phase_walks_in_reverse(self, present, expected_action):
        state = _state_with_stacks(present, analytics_enabled=True)
        plan = lifecycle.next_step(state, "destroy")
        assert plan["action"] == expected_action
        assert plan["done"] is False

    def test_destroy_phase_noop_when_empty(self):
        state = _state_with_stacks(set())
        plan = lifecycle.next_step(state, "destroy")
        assert plan["action"] == "noop"
        assert plan["done"] is True


# ---------------------------------------------------------------------------
# next_step — test / verify-clean phases
# ---------------------------------------------------------------------------


class TestNextStepTestPhase:
    def test_test_phase_happy(self):
        state = _state_with_stacks(
            {"gco-global", "gco-api-gateway", "gco-analytics", "gco-us-east-2"},
            analytics_enabled=True,
        )
        plan = lifecycle.next_step(state, "test")
        assert plan["action"] == "run-smoke-tests"
        assert "pytest" in plan["command"]
        assert plan["done"] is True

    def test_test_phase_blocked_on_stuck_stack(self):
        state = _state_with_stacks(
            {"gco-global", "gco-api-gateway", "gco-us-east-2"}, analytics_enabled=True
        )
        # Replace gco-analytics with a stuck status.
        stacks = dict(state.stacks)
        stacks["gco-analytics"] = "ROLLBACK_COMPLETE"
        stuck = lifecycle.LifecycleState(
            region=state.region,
            project_name=state.project_name,
            stacks=stacks,
            ssm_params=state.ssm_params,
            analytics_enabled=True,
        )
        plan = lifecycle.next_step(stuck, "test")
        assert plan["action"] == "wait"
        assert plan["done"] is False
        assert "gco-analytics" in plan["reason"]


# ---------------------------------------------------------------------------
# format_remediation
# ---------------------------------------------------------------------------


class TestFormatRemediation:
    def test_clean_state(self):
        """7. Clean state returns the sentinel string."""
        state = _state_with_stacks(
            {"gco-global", "gco-api-gateway", "gco-us-east-2"}, analytics_enabled=False
        )
        assert lifecycle.format_remediation(state) == "No remediation needed — state is clean."

    def test_with_orphans(self):
        """8. Orphan counts produce mention-of-EFS text."""
        state = lifecycle.LifecycleState(
            region="us-east-2",
            project_name="gco",
            stacks={"gco-global": lifecycle.STACK_ABSENT},
            retained_efs_count=2,
        )
        output = lifecycle.format_remediation(state)
        assert "EFS" in output
        assert "2 orphaned" in output

    def test_analytics_toggle_mismatch(self):
        """12. enabled=true in cdk.json but gco-analytics missing is flagged."""
        state = lifecycle.LifecycleState(
            region="us-east-2",
            project_name="gco",
            stacks={
                "gco-global": "CREATE_COMPLETE",
                "gco-analytics": lifecycle.STACK_ABSENT,
            },
            analytics_enabled=True,
        )
        output = lifecycle.format_remediation(state)
        assert "gco-analytics" in output
        assert "analytics_environment.enabled=true" in output

    def test_reverse_mismatch_flagged(self):
        """Opposite of above: stack present but toggle false."""
        state = lifecycle.LifecycleState(
            region="us-east-2",
            project_name="gco",
            stacks={"gco-analytics": "CREATE_COMPLETE"},
            analytics_enabled=False,
        )
        output = lifecycle.format_remediation(state)
        assert "cdk.json has" in output
        assert "analytics_environment.enabled=false" in output


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_status_dry_run_json(self, aws_creds_env, fake_cdk_json, capsys):
        """9. ``status --dry-run --json`` returns 0 and prints valid JSON."""
        with mock_aws():
            rc = lifecycle.main(
                [
                    "status",
                    "--dry-run",
                    "--json",
                    "--region",
                    "us-east-2",
                    "--cdk-json-path",
                    fake_cdk_json,
                ]
            )

        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "state" in payload
        assert "remediation" in payload

    def test_invalid_phase_returns_two(self, aws_creds_env, fake_cdk_json, capsys):
        """10. Unknown phase returns exit code 2."""
        with mock_aws():
            rc = lifecycle.main(
                [
                    "invalid-phase",
                    "--cdk-json-path",
                    fake_cdk_json,
                    "--region",
                    "us-east-2",
                ]
            )
        assert rc == 2

    def test_exit_code_on_stuck_stack(self, aws_creds_env, fake_cdk_json):
        """11. A ``ROLLBACK_FAILED`` stack surfaces as exit code 1.

        moto doesn't let us drive a stack into ``ROLLBACK_FAILED`` directly,
        so we patch ``detect_state`` to return a fabricated state with that
        status and confirm ``main`` returns 1.
        """
        import unittest.mock as _mock

        stuck_state = lifecycle.LifecycleState(
            region="us-east-2",
            project_name="gco",
            stacks={
                "gco-global": "CREATE_COMPLETE",
                "gco-api-gateway": "CREATE_COMPLETE",
                "gco-analytics": "ROLLBACK_FAILED",
                "gco-us-east-2": "CREATE_COMPLETE",
            },
            ssm_params={
                "/gco/cluster-shared-bucket/name": True,
                "/gco/cluster-shared-bucket/arn": True,
                "/gco/cluster-shared-bucket/region": True,
            },
            analytics_enabled=True,
        )

        with _mock.patch.object(lifecycle, "detect_state", return_value=stuck_state):
            rc = lifecycle.main(
                [
                    "status",
                    "--cdk-json-path",
                    fake_cdk_json,
                    "--region",
                    "us-east-2",
                ]
            )

        assert rc == 1

    def test_dry_run_deploy_skips_subprocess(self, aws_creds_env, fake_cdk_json, capsys):
        """--dry-run on the deploy phase must not invoke cdk."""
        with mock_aws():
            import unittest.mock as _mock

            with _mock.patch("subprocess.run") as mock_run:
                rc = lifecycle.main(
                    [
                        "deploy",
                        "--dry-run",
                        "--region",
                        "us-east-2",
                        "--cdk-json-path",
                        fake_cdk_json,
                    ]
                )

        assert rc == 0
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# verify-clean phase
# ---------------------------------------------------------------------------


class TestVerifyCleanPhase:
    def test_clean_phase_clean(self):
        state = lifecycle.LifecycleState(
            region="us-east-2",
            project_name="gco",
            stacks={"gco-global": lifecycle.STACK_ABSENT},
            ssm_params={
                "/gco/cluster-shared-bucket/name": False,
                "/gco/cluster-shared-bucket/arn": False,
                "/gco/cluster-shared-bucket/region": False,
            },
        )
        plan = lifecycle.next_step(state, "verify-clean")
        assert plan["action"] == "clean"
        assert plan["done"] is True

    def test_clean_phase_flags_orphaned_efs(self):
        state = lifecycle.LifecycleState(
            region="us-east-2",
            project_name="gco",
            stacks={"gco-global": lifecycle.STACK_ABSENT},
            retained_efs_count=3,
        )
        plan = lifecycle.next_step(state, "verify-clean")
        assert plan["action"] == "cleanup-efs"
        assert plan["done"] is False
