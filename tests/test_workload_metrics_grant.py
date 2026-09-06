"""Synthesis checks for the job-pod CloudWatch metrics grant (W4).

Workload pods must run as ``gco-service-account`` to write artifacts (it is
the only identity with RW on the regional shared bucket plus KMS encrypt),
so that same Pod Identity role needs ``cloudwatch:PutMetricData`` — scoped
by a ``cloudwatch:namespace`` condition to exactly one configurable
namespace (``cdk.json::workload_metrics.cloudwatch_namespace``, default
``GCO/Workloads``).

These tests synthesize the regional template and assert:

* exactly one namespace-conditioned ``PutMetricData`` statement exists on
  the pod role, with ``Resource: *`` carried entirely by the condition;
* the condition value follows the ``workload_metrics`` context block;
* no unconditioned ``PutMetricData`` statement exists on the pod role.

The Docker + helm-installer patching pattern is borrowed from
``tests/test_regional_stack.py`` so the synth needs no Docker daemon.
"""

from __future__ import annotations

from functools import cache
from typing import Any
from unittest.mock import MagicMock, patch

import aws_cdk as cdk
from aws_cdk import assertions

from gco.stacks.regional_stack import GCORegionalStack

# Reuse the MockConfigLoader + helm-installer patch helpers from the regional
# stack tests rather than re-implementing a synth fixture, and the pod-role
# statement collector from the regional bucket synthesis tests.
from tests.test_mooncake_regional_bucket_synthesis import _pod_role_statements
from tests.test_regional_stack import MockConfigLoader
from tests.test_regional_stack import TestRegionalStackSynthesis as _RegionalStackSynthesisFixtures

_ACCOUNT = "123456789012"
_REGION = "us-east-1"


def _synthesize(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synthesize the regional stack with optional app context overrides."""
    app = cdk.App(context=context or {})
    config = MockConfigLoader(app)

    with (
        patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
        patch.object(
            GCORegionalStack,
            "_create_helm_installer_lambda",
            _RegionalStackSynthesisFixtures._mock_helm_installer,
        ),
    ):
        mock_image = MagicMock()
        mock_image.image_uri = f"{_ACCOUNT}.dkr.ecr.{_REGION}.amazonaws.com/test:latest"
        mock_docker.return_value = mock_image

        stack = GCORegionalStack(
            app,
            "test-workload-metrics-synthesis",
            config=config,
            region=_REGION,
            auth_secret_arn=f"arn:aws:secretsmanager:{_REGION}:{_ACCOUNT}:secret:test-secret",  # nosec B106
            env=cdk.Environment(account=_ACCOUNT, region=_REGION),
        )
        return assertions.Template.from_stack(stack).to_json()


@cache
def _default_template() -> dict[str, Any]:
    return _synthesize()


def _put_metric_data_statements(template: dict[str, Any]) -> list[dict[str, Any]]:
    """Every pod-role statement that grants cloudwatch:PutMetricData."""
    statements = []
    for stmt in _pod_role_statements(template):
        action = stmt.get("Action")
        actions = {action} if isinstance(action, str) else set(action or [])
        if "cloudwatch:PutMetricData" in actions:
            statements.append(stmt)
    return statements


class TestWorkloadMetricsGrant:
    """The job-pod role gets exactly one namespace-conditioned metrics grant."""

    def test_exactly_one_namespace_conditioned_putmetricdata_statement(self) -> None:
        statements = _put_metric_data_statements(_default_template())

        assert len(statements) == 1, (
            f"expected exactly one PutMetricData statement on the pod role, "
            f"found {len(statements)}: {statements}"
        )
        grant = statements[0]
        assert grant["Effect"] == "Allow"
        assert grant["Action"] == "cloudwatch:PutMetricData"
        assert grant["Resource"] == "*"
        assert grant["Condition"] == {"StringEquals": {"cloudwatch:namespace": "GCO/Workloads"}}

    def test_namespace_follows_the_workload_metrics_context(self) -> None:
        template = _synthesize({"workload_metrics": {"cloudwatch_namespace": "Acme/Training"}})
        statements = _put_metric_data_statements(template)

        assert len(statements) == 1
        assert statements[0]["Condition"] == {
            "StringEquals": {"cloudwatch:namespace": "Acme/Training"}
        }

    def test_empty_or_missing_namespace_falls_back_to_the_default(self) -> None:
        template = _synthesize({"workload_metrics": {"cloudwatch_namespace": ""}})
        statements = _put_metric_data_statements(template)

        assert len(statements) == 1
        assert statements[0]["Condition"] == {
            "StringEquals": {"cloudwatch:namespace": "GCO/Workloads"}
        }

    def test_shipped_cdk_json_declares_the_default_namespace(self) -> None:
        """The shipped config block and the code default tell one story."""
        import json
        from pathlib import Path

        shipped = json.loads(Path("cdk.json").read_text(encoding="utf-8"))
        block = shipped["context"]["workload_metrics"]

        assert block == {"cloudwatch_namespace": "GCO/Workloads"}
