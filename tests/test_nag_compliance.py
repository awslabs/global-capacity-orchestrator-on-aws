"""End-to-end cdk-nag compliance regression test.

What this catches
-----------------
The class ``TestCdkNagCompliance`` synthesizes the full CDK application
exactly the way ``app.py`` does — Global, API Gateway, one or more
Regional stacks, and the Monitoring stack — with a custom
``INagLogger`` attached alongside the normal cdk-nag rule packs. After
``app.synth()`` returns, the test asserts that the logger collected
zero unsuppressed findings.

Why not rely on ``cdk synth`` exit codes
----------------------------------------
``cdk synth`` writes cdk-nag findings to the CDK Annotations system,
which ``cdk synth --quiet`` suppresses entirely, and the CLI's exit
code is 0 even when unsuppressed findings exist. Our ``test:cdk:config-matrix``
job runs with ``--quiet`` to keep logs manageable — which means a
user-facing deploy can hit an ``AwsSolutions-IAM5`` error that CI
didn't catch.

Attaching a custom ``INagLogger`` gives us a Python-side hook that
captures findings directly, so pytest can assert on them without any
subprocess or text-parsing layer.

Scope
-----
Parameterized across the IAM-relevant subset of the cdk.json
configuration matrix (``tests/_cdk_config_matrix.NAG_CONFIGS``): the
5 configs that produce distinct IAM policy surfaces (default,
multi-region, FSx-enabled, all-features-enabled, three-regions). The
full 24-config matrix runs via ``scripts/test_cdk_synthesis.py`` for
synthesis correctness; this test focuses on the configs that actually
change IAM roles and policies, which is where cdk-nag findings live.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import aws_cdk as cdk
import pytest
from cdk_nag import (
    AwsSolutionsChecks,
    HIPAASecurityChecks,
    NIST80053R5Checks,
    PCIDSS321Checks,
    ServerlessChecks,
)

from tests._cdk_config_matrix import NAG_CONFIGS as _CONFIGS
from tests._cdk_nag_logger import CapturingCdkNagLogger


def _build_app_with_logger(
    context_overrides: dict[str, Any] | None = None,
) -> tuple[cdk.App, CapturingCdkNagLogger]:
    """Construct a CDK ``App`` configured the same way ``app.py`` does,
    with a ``CapturingCdkNagLogger`` attached to every rule pack.

    Args:
        context_overrides: cdk.json context keys to override. Merged
            into the baseline cdk.json context before the app is
            built — this is how each parameterized config exercises a
            different code path (multi-region, FSx on, etc.).

    Returns:
        ``(app, logger)`` — the constructed (but not yet synthesized)
        ``cdk.App`` and the logger whose ``.findings`` list will be
        populated when the caller invokes ``app.synth()``.

    The Docker asset and helm-installer Lambda are mocked out the
    same way every other regional-stack test mocks them, so no
    Docker daemon is required during pytest.
    """
    import json
    from pathlib import Path

    # Load the baseline cdk.json context.
    cdk_json_path = Path(__file__).resolve().parent.parent / "cdk.json"
    with cdk_json_path.open() as f:
        cdk_json = json.load(f)
    context: dict[str, Any] = dict(cdk_json.get("context", {}))

    # Apply overrides. Dict values are merged shallow-ly so partial
    # overrides (e.g. ``{"eks_cluster": {"endpoint_access": "PUBLIC_AND_PRIVATE"}}``)
    # don't clobber unrelated keys in the same block.
    if context_overrides:
        for key, value in context_overrides.items():
            if isinstance(value, dict) and key in context and isinstance(context[key], dict):
                merged = dict(context[key])
                merged.update(value)
                context[key] = merged
            else:
                context[key] = value

    app = cdk.App(context=context)
    logger = CapturingCdkNagLogger()

    # Register aspects identically to app.py, but with our logger
    # attached via additional_loggers. The real AnnotationLogger is
    # still registered by default so cdk-nag's own behavior is
    # unchanged — we just get a parallel feed into our list.
    from app import LambdaTracingAspect

    cdk.Aspects.of(app).add(LambdaTracingAspect())
    for check_cls in (
        AwsSolutionsChecks,
        HIPAASecurityChecks,
        NIST80053R5Checks,
        PCIDSS321Checks,
        ServerlessChecks,
    ):
        cdk.Aspects.of(app).add(check_cls(verbose=True, additional_loggers=[logger]))

    return app, logger


def _build_all_stacks(app: cdk.App) -> None:
    """Instantiate every stack ``app.py`` builds: global, API gateway,
    one regional stack per configured region, and monitoring. Matches
    ``app.py::main`` one-for-one so the cdk-nag findings captured
    here are the same ones a ``cdk deploy --all`` would surface.

    The heavy per-stack mocks (Docker asset + helm installer) are
    applied with ``patch.object`` inside the caller's ``with`` block;
    this function just wires the stacks together.
    """
    from gco.config.config_loader import ConfigLoader
    from gco.stacks.api_gateway_global_stack import GCOApiGatewayGlobalStack
    from gco.stacks.global_stack import GCOGlobalStack
    from gco.stacks.monitoring_stack import GCOMonitoringStack
    from gco.stacks.regional_stack import GCORegionalStack

    config = ConfigLoader(app)
    project_name = config.get_project_name()
    deployment_regions = config.get_deployment_regions()

    global_region = deployment_regions["global"]
    api_gateway_region = deployment_regions["api_gateway"]
    monitoring_region = deployment_regions["monitoring"]
    regional_regions = deployment_regions["regional"]

    global_stack = GCOGlobalStack(
        app,
        f"{project_name}-global",
        config=config,
        env=cdk.Environment(region=global_region),
    )

    api_gateway_stack = GCOApiGatewayGlobalStack(
        app,
        f"{project_name}-api-gateway",
        global_accelerator_dns=global_stack.accelerator.dns_name,
        env=cdk.Environment(region=api_gateway_region),
    )
    api_gateway_stack.add_dependency(global_stack)

    regional_stacks = []
    for region in regional_regions:
        regional_stack = GCORegionalStack(
            app,
            f"{project_name}-{region}",
            config=config,
            region=region,
            auth_secret_arn=api_gateway_stack.secret.secret_arn,
            env=cdk.Environment(region=region),
        )
        regional_stack.add_dependency(global_stack)
        regional_stack.add_dependency(api_gateway_stack)
        regional_stacks.append(regional_stack)
        global_stack.add_regional_endpoint(region, regional_stack.alb_arn)  # type: ignore[arg-type]

    monitoring_stack = GCOMonitoringStack(
        app,
        f"{project_name}-monitoring",
        config=config,
        global_stack=global_stack,
        regional_stacks=regional_stacks,
        api_gateway_stack=api_gateway_stack,
        env=cdk.Environment(region=monitoring_region),
    )
    for regional_stack in regional_stacks:
        monitoring_stack.add_dependency(regional_stack)


def _mock_helm_installer(stack: Any) -> None:
    """Mock ``_create_helm_installer_lambda`` so tests don't need a
    Docker daemon. Sets every attribute downstream consumers
    (monitoring_stack, regional_stack's own post-helm pipeline) read
    off of the helm-installer Lambda.
    """
    stack.helm_installer_lambda = MagicMock()
    stack.helm_installer_provider = MagicMock()
    # nosec B106 — test fixture ARN, not a real credential.
    stack.helm_installer_provider.service_token = (
        "arn:aws:lambda:us-east-1:123456789012:function:mock"
    )
    # monitoring_stack reads this as a plain string for widget setup;
    # it must be a concrete name, not a Token.
    stack.helm_installer_lambda_function_name = (
        f"gco-helm-{getattr(stack, 'deployment_region', 'us-east-1')}"
    )


class TestCdkNagCompliance:
    """End-to-end regression: ``app.synth()`` must produce zero
    unsuppressed cdk-nag findings across every representative
    configuration.

    When a test fails, the assertion message lists every finding by
    rule ID, resource path, and a short description — the same three
    pieces of information you'd need to either scope an existing
    suppression or add a new one.
    """

    # The IAM-relevant config subset is shared with
    # ``scripts/test_cdk_synthesis.py`` via
    # ``tests/_cdk_config_matrix.NAG_CONFIGS``. Only configs that
    # produce distinct IAM policy surfaces are included — the rest
    # (helm toggles, thresholds, etc.) don't change IAM and would
    # just burn CI time. See the module docstring in
    # ``_cdk_config_matrix.py`` for the rationale.
    CONFIGS: list[tuple[str, dict[str, Any]]] = _CONFIGS

    @pytest.mark.parametrize("config_name,overrides", CONFIGS, ids=[c[0] for c in CONFIGS])
    def test_no_unsuppressed_findings(self, config_name: str, overrides: dict[str, Any]) -> None:
        from gco.stacks.regional_stack import GCORegionalStack

        app, logger = _build_app_with_logger(context_overrides=overrides)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                _mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            _build_all_stacks(app)
            app.synth()

        assert not logger.findings, (
            f"cdk-nag found {len(logger.findings)} unsuppressed finding(s) "
            f"with config {config_name!r}.\n\n{logger.format_findings()}\n\n"
            f"Each finding either needs its underlying wildcard scoped "
            f"further or a targeted NagSuppressions entry with a "
            f"justification and an ``applies_to`` scoped to the "
            f"specific resource. Do NOT add broad ``Resource::*`` or "
            f"``Action::*`` entries — those defeat the whole point of "
            f"cdk-nag."
        )
        assert not logger.errors, (
            f"cdk-nag encountered {len(logger.errors)} rule evaluation "
            f"error(s) with config {config_name!r}. This usually means a "
            f"Token didn't resolve at synth time — investigate before "
            f"merging.\n\n{logger.format_findings()}"
        )
