"""
Default-image property tests for the analytics stack.

For any synthesis of the ``gco-analytics`` stack (with
``analytics_environment.enabled=true`` and any value of
``hyperpod.enabled``), the resulting CloudFormation template SHALL
contain zero ``AWS::ECR::Repository``, zero ``AWS::SageMaker::Image``,
and zero ``AWS::SageMaker::AppImageConfig`` resources. The Studio
domain's ``DefaultUserSettings.JupyterLabAppSettings.CustomImages``
field SHALL be absent or an empty list. Additionally, the repository
SHALL contain no SageMaker-distribution Dockerfile under
``dockerfiles/``.

This is parameterized over ``hyperpod_enabled ∈ {True, False}`` — the
HyperPod sub-toggle adds SageMaker training-job IAM actions but must not
introduce any image-build machinery.

The repository-layout check is a pure-Python glob scoped to analytics-
and studio-named dockerfile directories so existing non-analytics
dockerfiles (``health-monitor-dockerfile``,
``manifest-processor-dockerfile``, etc.) don't trip the assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

from gco.config.config_loader import ConfigLoader
from gco.stacks.analytics_stack import GCOAnalyticsStack

# Reuse the established mock-config + synth helpers from the sibling
# analytics-stack test suite so this property check stays consistent with
# the rest of the analytics-stack tests.
from tests.test_analytics_stack import _AnalyticsMockConfig

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _synth_with_hyperpod(hyperpod_enabled: bool) -> assertions.Template:
    """Synthesize ``GCOAnalyticsStack`` with ``hyperpod.enabled`` set.

    Distinct from the sibling ``_synth_analytics`` in
    ``tests/test_analytics_stack.py`` because this test parameterizes on
    the HyperPod sub-toggle — otherwise the helper is identical.
    """
    app = cdk.App()
    config = _AnalyticsMockConfig(hyperpod_enabled=hyperpod_enabled)
    stack = GCOAnalyticsStack(
        app,
        "test-analytics-default-image",
        config=cast(ConfigLoader, config),
        env=cdk.Environment(account="123456789012", region="us-east-2"),
    )
    return assertions.Template.from_stack(stack)


def _project_root() -> Path:
    """Return the repository root.

    This test file lives at ``<repo>/tests/test_analytics_default_image.py``,
    so the root is two ``parents`` up.
    """
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Template-level assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hyperpod_enabled", [True, False])
class TestNoCustomImageResources:
    """Synthesized ``gco-analytics`` template contains zero image-related
    resources regardless of the HyperPod sub-toggle."""

    def test_zero_ecr_repositories(self, hyperpod_enabled: bool) -> None:
        """No ``AWS::ECR::Repository`` in the analytics stack.

        Studio uses stock AWS-published SageMaker Distribution images, so
        there is no build step that needs an ECR repository to land them
        in.
        """
        template = _synth_with_hyperpod(hyperpod_enabled)
        template.resource_count_is("AWS::ECR::Repository", 0)

    def test_zero_sagemaker_images(self, hyperpod_enabled: bool) -> None:
        """No ``AWS::SageMaker::Image`` in the analytics stack.

        Custom-image registration would live here; its absence asserts the
        default-image scope decision.
        """
        template = _synth_with_hyperpod(hyperpod_enabled)
        template.resource_count_is("AWS::SageMaker::Image", 0)

    def test_zero_sagemaker_app_image_configs(self, hyperpod_enabled: bool) -> None:
        """No ``AWS::SageMaker::AppImageConfig`` in the analytics stack.

        ``AppImageConfig`` binds a ``SageMaker::Image`` to a JupyterLab /
        KernelGateway app config; with no images there should be no
        bindings either.
        """
        template = _synth_with_hyperpod(hyperpod_enabled)
        template.resource_count_is("AWS::SageMaker::AppImageConfig", 0)

    def test_domain_has_no_custom_images(self, hyperpod_enabled: bool) -> None:
        """``DefaultUserSettings.JupyterLabAppSettings.CustomImages`` is
        absent or empty on the Studio domain.

        The design's stricter stance is that the entire
        ``JupyterLabAppSettings`` key is absent — that yields no
        ``CustomImages`` either way. Both shapes (key absent; key present
        with ``CustomImages`` absent; key present with empty list) satisfy
        the property, so this test walks the actual value and accepts any
        of them.
        """
        template = _synth_with_hyperpod(hyperpod_enabled)
        domains = template.find_resources("AWS::SageMaker::Domain")
        assert len(domains) == 1, f"expected exactly one Studio domain, got {len(domains)}"
        domain = next(iter(domains.values()))

        default_user_settings = domain["Properties"].get("DefaultUserSettings") or {}
        jupyter_settings = default_user_settings.get("JupyterLabAppSettings")

        # Branch on the three acceptable shapes.
        if jupyter_settings is None:
            # Stricter — the entire key is absent.
            return
        assert isinstance(jupyter_settings, dict), (
            f"expected JupyterLabAppSettings to be a dict if present, "
            f"got {type(jupyter_settings).__name__}={jupyter_settings!r}"
        )
        custom_images = jupyter_settings.get("CustomImages")
        if custom_images is None:
            # Key absent within JupyterLabAppSettings — acceptable.
            return
        assert (
            isinstance(custom_images, list) and custom_images == []
        ), f"expected CustomImages absent or empty list, got {custom_images!r}"


# ---------------------------------------------------------------------------
# Repository-layout assertion
# ---------------------------------------------------------------------------


class TestRepositoryLayoutNoAnalyticsDockerfiles:
    """The repository SHALL contain no SageMaker-distribution
    Dockerfile under ``dockerfiles/``.

    Scope: only analytics- or studio-related subdirectories. Existing
    non-analytics dockerfiles (``health-monitor-dockerfile``,
    ``inference-monitor-dockerfile``, ``manifest-processor-dockerfile``,
    ``queue-processor-dockerfile``) are unrelated to this feature and
    must not trip the assertion.
    """

    def test_no_analytics_dockerfile_subdirectories(self) -> None:
        """No path matching ``dockerfiles/**/analytics*/Dockerfile*`` or
        ``dockerfiles/**/studio*/Dockerfile*`` exists under the repo root.

        Each match is a Dockerfile we'd expect to see if someone added a
        custom SageMaker Distribution image build — the presence of any
        such file is a regression on the default-image scope decision.
        """
        root = _project_root()
        dockerfiles_dir = root / "dockerfiles"

        # If the dockerfiles tree doesn't exist at all, the assertion is
        # trivially true — skip early so the test doesn't silently no-op
        # in a repository reshuffle.
        if not dockerfiles_dir.exists():
            return

        # Glob for either ``analytics*/Dockerfile*`` or ``studio*/Dockerfile*``
        # at any depth under ``dockerfiles/``. These are the two patterns an
        # analytics-specific Dockerfile would plausibly live under.
        analytics_matches = list(dockerfiles_dir.glob("**/analytics*/Dockerfile*"))
        studio_matches = list(dockerfiles_dir.glob("**/studio*/Dockerfile*"))

        # Also catch a direct ``dockerfiles/analytics*`` or
        # ``dockerfiles/studio*`` named file (not in a subdir) that looks
        # like a Dockerfile by name.
        direct_matches: list[Path] = []
        for entry in dockerfiles_dir.glob("*"):
            if entry.is_file() and entry.name.lower().startswith(("analytics", "studio")):
                direct_matches.append(entry)

        all_matches = analytics_matches + studio_matches + direct_matches
        assert not all_matches, (
            "Expected no analytics- or studio-named Dockerfile "
            f"under dockerfiles/, but found: {[str(p.relative_to(root)) for p in all_matches]!r}"
        )

    def test_no_sagemaker_named_dockerfile(self) -> None:
        """No path matching ``dockerfiles/**/sagemaker*/Dockerfile*`` either.

        A SageMaker-distribution image build would most naturally live
        under a ``sagemaker`` subdir; we exclude it here as well so a
        future custom-image sneak-in lands on this test directly.
        """
        root = _project_root()
        dockerfiles_dir = root / "dockerfiles"
        if not dockerfiles_dir.exists():
            return

        matches = list(dockerfiles_dir.glob("**/sagemaker*/Dockerfile*"))
        matches += [
            entry
            for entry in dockerfiles_dir.glob("*")
            if entry.is_file() and entry.name.lower().startswith("sagemaker")
        ]
        assert not matches, (
            "Expected no sagemaker-named Dockerfile under "
            f"dockerfiles/, but found: {[str(p.relative_to(root)) for p in matches]!r}"
        )


# ---------------------------------------------------------------------------
# Combined smoke test (keeps the parametrize + glob wiring obvious)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hyperpod_enabled", [True, False])
def test_default_image_full_property(hyperpod_enabled: bool) -> None:
    """Smoke composition of both halves of the default-image property —
    the template assertions and the repository-layout assertion run
    together.

    Fails loudly if either half drifts, independently of whether the
    class-based tests above continue to pass.
    """
    # Template side.
    template = _synth_with_hyperpod(hyperpod_enabled)
    template.resource_count_is("AWS::ECR::Repository", 0)
    template.resource_count_is("AWS::SageMaker::Image", 0)
    template.resource_count_is("AWS::SageMaker::AppImageConfig", 0)

    domains = template.find_resources("AWS::SageMaker::Domain")
    assert len(domains) == 1
    domain = next(iter(domains.values()))
    jupyter_settings = (domain["Properties"].get("DefaultUserSettings") or {}).get(
        "JupyterLabAppSettings"
    )
    custom_images: Any = None if jupyter_settings is None else jupyter_settings.get("CustomImages")
    assert custom_images in (
        None,
        [],
    ), f"expected CustomImages absent or empty, got {custom_images!r}"

    # Repository-layout side.
    root = _project_root()
    dockerfiles_dir = root / "dockerfiles"
    if dockerfiles_dir.exists():
        matches = (
            list(dockerfiles_dir.glob("**/analytics*/Dockerfile*"))
            + list(dockerfiles_dir.glob("**/studio*/Dockerfile*"))
            + list(dockerfiles_dir.glob("**/sagemaker*/Dockerfile*"))
        )
        matches += [
            entry
            for entry in dockerfiles_dir.glob("*")
            if entry.is_file()
            and entry.name.lower().startswith(("analytics", "studio", "sagemaker"))
        ]
        assert not matches, (
            "Analytics/studio/sagemaker dockerfile found: "
            f"{[str(p.relative_to(root)) for p in matches]!r}"
        )
