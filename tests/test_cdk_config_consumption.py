"""
Guard against "dead config" in cdk.json.

For each declared section (global_accelerator, helm, resource_thresholds,
alb_config, inference_monitor) this scans every Python source file under
gco/ and lambda/ to confirm each configured key is actually read by the
CDK stacks or Lambda handlers. If a key exists in cdk.json but never
shows up as a dict-literal, attribute access, or .get() call anywhere
in the code, the test fails with a pointer to either consume or remove
the stale setting.
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


def _load_cdk_context() -> dict:
    """Load the context section from cdk.json."""
    with open(PROJECT_ROOT / "cdk.json", encoding="utf-8") as f:
        return json.load(f).get("context", {})


def _get_python_source() -> str:
    """Concatenate all Python source files in gco/ and lambda/ directories."""
    source_parts = []
    for directory in ["gco", "lambda"]:
        source_dir = PROJECT_ROOT / directory
        for py_file in source_dir.rglob("*.py"):
            if "__pycache__" in str(py_file) or "-build" in str(py_file):
                continue
            source_parts.append(py_file.read_text(encoding="utf-8"))
    return "\n".join(source_parts)


class TestGlobalAcceleratorConfigConsumed:
    """Verify every global_accelerator config key is used in CDK/Lambda code."""

    def test_all_ga_config_keys_are_consumed(self):
        """Every key in cdk.json global_accelerator must appear in source code.

        This catches the bug where health_check_path was defined in cdk.json
        but never read by the CDK stack that creates endpoint groups.
        """
        context = _load_cdk_context()
        ga_config = context.get("global_accelerator", {})
        source = _get_python_source()

        unconsumed = []
        for key in ga_config:
            # Check if the key appears in source code (as string literal or dict access)
            patterns = [
                f'"{key}"',  # dict literal key
                f"'{key}'",  # single-quoted key
                f".{key}",  # attribute access
                f'["{key}"]',  # bracket access
                f"['{key}']",  # bracket access single quote
                f'get("{key}"',  # .get() access
                f"get('{key}'",  # .get() access single quote
            ]
            found = any(p in source for p in patterns)
            if not found:
                unconsumed.append(key)

        if unconsumed:
            raise AssertionError(
                f"The following global_accelerator config keys in cdk.json are "
                f"not referenced in any Python source file:\n"
                f"  {unconsumed}\n\n"
                f"Either consume them in the CDK stack or remove them from cdk.json."
            )


class TestHelmConfigConsumed:
    """Verify every helm chart config key maps to actual chart installation logic."""

    def test_all_helm_keys_have_chart_mapping(self):
        """Every key in cdk.json helm section must be handled in the Helm installer."""
        context = _load_cdk_context()
        helm_config = context.get("helm", {})
        source = _get_python_source()

        unmapped = []
        for key in helm_config:
            # The key should appear in source code (chart mapping, config access, etc.)
            patterns = [
                f'"{key}"',
                f"'{key}'",
                f'get("{key}"',
                f"get('{key}'",
            ]
            found = any(p in source for p in patterns)
            if not found:
                unmapped.append(key)

        if unmapped:
            raise AssertionError(
                f"The following helm config keys in cdk.json are not referenced "
                f"in any Python source file:\n"
                f"  {unmapped}\n\n"
                f"Either add chart installation logic or remove them from cdk.json."
            )


class TestResourceThresholdsConsumed:
    """Verify every resource_thresholds config key is used by the health monitor."""

    def test_all_threshold_keys_are_consumed(self):
        """Every key in cdk.json resource_thresholds must be used in health monitoring."""
        context = _load_cdk_context()
        thresholds = context.get("resource_thresholds", {})
        source = _get_python_source()

        unconsumed = []
        for key in thresholds:
            patterns = [
                f'"{key}"',
                f"'{key}'",
                f".{key}",
                f'get("{key}"',
            ]
            found = any(p in source for p in patterns)
            if not found:
                unconsumed.append(key)

        if unconsumed:
            raise AssertionError(
                f"The following resource_thresholds config keys in cdk.json are "
                f"not referenced in any Python source file:\n"
                f"  {unconsumed}\n\n"
                f"Either consume them in the health monitor or remove from cdk.json."
            )


class TestAlbConfigConsumed:
    """Verify ALB config keys are consumed."""

    def test_all_alb_config_keys_are_consumed(self):
        """Every key in cdk.json alb_config must appear in source code."""
        context = _load_cdk_context()
        alb_config = context.get("alb_config", {})
        source = _get_python_source()

        unconsumed = []
        for key in alb_config:
            patterns = [
                f'"{key}"',
                f"'{key}'",
                f".{key}",
                f'get("{key}"',
            ]
            found = any(p in source for p in patterns)
            if not found:
                unconsumed.append(key)

        if unconsumed:
            raise AssertionError(
                f"The following alb_config keys in cdk.json are not referenced "
                f"in any Python source file:\n"
                f"  {unconsumed}\n\n"
                f"Either consume them in the CDK stack or remove from cdk.json."
            )


class TestInferenceMonitorConfigConsumed:
    """Verify inference_monitor config keys are consumed."""

    def test_all_inference_monitor_keys_are_consumed(self):
        """Every key in cdk.json inference_monitor must appear in source code."""
        context = _load_cdk_context()
        im_config = context.get("inference_monitor", {})
        if not im_config:
            pytest.skip("No inference_monitor config in cdk.json")

        source = _get_python_source()

        unconsumed = []
        for key in im_config:
            patterns = [
                f'"{key}"',
                f"'{key}'",
                f".{key}",
                f'get("{key}"',
                f"get('{key}'",
                # Also check env var form (UPPER_CASE)
                key.upper(),
            ]
            found = any(p in source for p in patterns)
            if not found:
                unconsumed.append(key)

        if unconsumed:
            raise AssertionError(
                f"The following inference_monitor config keys in cdk.json are "
                f"not referenced in any Python source file:\n"
                f"  {unconsumed}\n\n"
                f"Either consume them or remove from cdk.json."
            )
