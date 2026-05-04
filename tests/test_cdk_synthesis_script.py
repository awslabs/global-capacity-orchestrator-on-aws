"""Tests for ``scripts/test_cdk_synthesis.py``.

The script shells out to ``cdk synth`` for every config in
``tests._cdk_config_matrix.CONFIGS``. It writes a modified ``cdk.json``
before each run and restores the original in a ``finally`` block — so
the invariants worth locking down are:

1. ``synth_with_config`` writes a merged config, invokes ``cdk synth``,
   and **always** restores the original ``cdk.json`` even when synth
   fails, times out, or raises.
2. Shallow-merge semantics for dict-valued context keys. An override
   that only touches one sub-key of ``eks_cluster`` must not clobber
   the others.
3. Success / failure / timeout branches all return the expected bool.
4. ``main`` aggregates per-config results, returns 0 on clean runs,
   and 1 with the list of failing config names otherwise.

These tests never run a real ``cdk synth`` — the ``subprocess.run`` call
is patched to a fake. That keeps the unit suite fast (the real matrix
is already covered by ``unit:cdk:config-matrix`` in CI, which runs the
script end-to-end).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import
# ---------------------------------------------------------------------------
#
# The script needs a real ``cdk.json`` readable at import time. It captures
# the baseline as ``ORIGINAL_CONFIG`` / ``BASE_CONFIG`` during module
# execution. We load from the real repo ``cdk.json`` once so the module
# imports cleanly, then patch ``BASE_CONFIG`` + ``CDK_JSON`` to a tmp
# fixture in every test that touches synth.

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "test_cdk_synthesis.py"
_SPEC = importlib.util.spec_from_file_location("cdk_synth_script", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
cdk_synth = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("cdk_synth_script", cdk_synth)
_SPEC.loader.exec_module(cdk_synth)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_cdk_json(tmp_path, monkeypatch):
    """Redirect the script's ``CDK_JSON`` to a throwaway file.

    Yields ``(path, baseline_dict)``. Tests that call
    ``synth_with_config`` should use this fixture so the real repo
    ``cdk.json`` is never mutated.
    """
    baseline = {
        "context": {
            "project_name": "gco",
            "eks_cluster": {
                "endpoint_access": "PRIVATE",
                "worker_node_type": "m5.large",
            },
            "analytics_environment": {"enabled": False},
            "deployment_regions": {
                "regional": ["us-east-1"],
            },
        }
    }
    cdk_file = tmp_path / "cdk.json"
    cdk_file.write_text(json.dumps(baseline, indent=2))

    monkeypatch.setattr(cdk_synth, "CDK_JSON", cdk_file)
    monkeypatch.setattr(cdk_synth, "ORIGINAL_CONFIG", cdk_file.read_text())
    monkeypatch.setattr(cdk_synth, "BASE_CONFIG", json.loads(cdk_file.read_text()))

    yield cdk_file, baseline


def _fake_result(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    """Build a subprocess.CompletedProcess the script knows how to read."""
    return subprocess.CompletedProcess(
        args=["cdk", "synth"], returncode=returncode, stdout="", stderr=stderr
    )


# ---------------------------------------------------------------------------
# synth_with_config — happy path
# ---------------------------------------------------------------------------


def test_synth_with_config_returns_true_on_success(tmp_cdk_json):
    """``cdk synth`` returning 0 with no error output is a pass."""
    with patch.object(cdk_synth.subprocess, "run", return_value=_fake_result(0)):
        ok = cdk_synth.synth_with_config("clean-config", {})
    assert ok is True


def test_synth_with_config_passes_with_notices(tmp_cdk_json):
    """A run that emits only upstream NOTICES (and no ``Error``) still passes."""
    stderr = "NOTICES\n99999\tDeprecation warning\n"
    with patch.object(cdk_synth.subprocess, "run", return_value=_fake_result(0, stderr)):
        ok = cdk_synth.synth_with_config("notice-only", {})
    assert ok is True


# ---------------------------------------------------------------------------
# synth_with_config — failure modes
# ---------------------------------------------------------------------------


def test_synth_with_config_returns_false_on_nonzero_exit_with_error_text(tmp_cdk_json):
    """Non-zero exit + the literal ``Error`` token is a failure."""
    stderr = "Error: stack gco-xyz failed to synthesize\n"
    with patch.object(cdk_synth.subprocess, "run", return_value=_fake_result(1, stderr)):
        ok = cdk_synth.synth_with_config("broken-config", {})
    assert ok is False


def test_synth_with_config_tolerates_nonzero_with_only_notices(tmp_cdk_json):
    """A non-zero exit with only NOTICES (no ``Error`` text) is classified as a pass.

    This matches the heuristic in the script — ``cdk`` occasionally
    exits non-zero when it only wants to surface a deprecation notice.
    We don't want to fail CI over that.
    """
    stderr = "NOTICES\n\n11111\tjust a notice\n"
    with patch.object(cdk_synth.subprocess, "run", return_value=_fake_result(2, stderr)):
        ok = cdk_synth.synth_with_config("notice-but-nonzero", {})
    assert ok is True


def test_synth_with_config_returns_false_on_timeout(tmp_cdk_json):
    """A subprocess timeout is reported as a failure."""
    with patch.object(
        cdk_synth.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="cdk", timeout=120),
    ):
        ok = cdk_synth.synth_with_config("slow-config", {})
    assert ok is False


def test_synth_with_config_returns_false_on_unexpected_exception(tmp_cdk_json):
    """An unexpected exception from ``subprocess.run`` is caught and logged."""
    with patch.object(cdk_synth.subprocess, "run", side_effect=RuntimeError("boom")):
        ok = cdk_synth.synth_with_config("broken-config", {})
    assert ok is False


# ---------------------------------------------------------------------------
# synth_with_config — cdk.json restoration (the critical invariant)
# ---------------------------------------------------------------------------


def test_synth_with_config_restores_cdk_json_after_success(tmp_cdk_json):
    cdk_file, _baseline = tmp_cdk_json
    original = cdk_file.read_text()
    with patch.object(cdk_synth.subprocess, "run", return_value=_fake_result(0)):
        cdk_synth.synth_with_config("some-config", {"new_key": "new_val"})
    # After the call completes, the committed cdk.json is back to the baseline.
    assert cdk_file.read_text() == original


def test_synth_with_config_restores_cdk_json_after_failure(tmp_cdk_json):
    cdk_file, _baseline = tmp_cdk_json
    original = cdk_file.read_text()
    with patch.object(cdk_synth.subprocess, "run", return_value=_fake_result(1, "Error: x")):
        cdk_synth.synth_with_config("bad-config", {"new_key": "new_val"})
    assert cdk_file.read_text() == original


def test_synth_with_config_restores_cdk_json_after_timeout(tmp_cdk_json):
    cdk_file, _baseline = tmp_cdk_json
    original = cdk_file.read_text()
    with patch.object(
        cdk_synth.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="cdk", timeout=120),
    ):
        cdk_synth.synth_with_config("slow", {})
    assert cdk_file.read_text() == original


def test_synth_with_config_restores_cdk_json_after_unexpected_exception(tmp_cdk_json):
    cdk_file, _baseline = tmp_cdk_json
    original = cdk_file.read_text()
    with patch.object(cdk_synth.subprocess, "run", side_effect=RuntimeError("boom")):
        cdk_synth.synth_with_config("explode", {})
    assert cdk_file.read_text() == original


# ---------------------------------------------------------------------------
# synth_with_config — override merge semantics
# ---------------------------------------------------------------------------


def test_synth_with_config_applies_flat_overrides(tmp_cdk_json):
    """Flat (non-dict) overrides replace the context key directly."""
    cdk_file, _ = tmp_cdk_json
    captured: dict[str, object] = {}

    def _capture_and_read(*_args, **_kw):
        # Read the file the script wrote so we can assert the merge result.
        captured["written"] = json.loads(cdk_file.read_text())
        return _fake_result(0)

    with patch.object(cdk_synth.subprocess, "run", side_effect=_capture_and_read):
        cdk_synth.synth_with_config("flat", {"project_name": "other"})

    assert captured["written"]["context"]["project_name"] == "other"


def test_synth_with_config_shallow_merges_dict_overrides(tmp_cdk_json):
    """Dict overrides merge at the top level, preserving untouched sub-keys."""
    cdk_file, baseline = tmp_cdk_json
    captured: dict[str, object] = {}

    def _capture_and_read(*_args, **_kw):
        captured["written"] = json.loads(cdk_file.read_text())
        return _fake_result(0)

    with patch.object(cdk_synth.subprocess, "run", side_effect=_capture_and_read):
        cdk_synth.synth_with_config(
            "partial-cluster",
            {"eks_cluster": {"endpoint_access": "PUBLIC_AND_PRIVATE"}},
        )

    written = captured["written"]["context"]
    # Override applied
    assert written["eks_cluster"]["endpoint_access"] == "PUBLIC_AND_PRIVATE"
    # Sibling key untouched
    assert (
        written["eks_cluster"]["worker_node_type"]
        == baseline["context"]["eks_cluster"]["worker_node_type"]
    )


def test_synth_with_config_replaces_scalar_with_dict(tmp_cdk_json):
    """If a key was a scalar in the baseline, an incoming dict replaces it."""
    cdk_file, _ = tmp_cdk_json
    captured: dict[str, object] = {}

    def _capture_and_read(*_args, **_kw):
        captured["written"] = json.loads(cdk_file.read_text())
        return _fake_result(0)

    with patch.object(cdk_synth.subprocess, "run", side_effect=_capture_and_read):
        cdk_synth.synth_with_config("promote", {"project_name": {"nested": True}})

    assert captured["written"]["context"]["project_name"] == {"nested": True}


def test_synth_with_config_invokes_cdk_synth_with_quiet_and_app(tmp_cdk_json):
    """The subprocess must get ``cdk synth --quiet --no-staging --app <python> app.py``.

    Drifting those flags would either produce spurious pass results
    (``--quiet`` is how we suppress NOTICES that would be mistaken for
    errors) or slow CI down by ~10× (``--no-staging`` skips asset
    uploads).
    """
    captured: dict[str, object] = {}

    def _capture(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _fake_result(0)

    with patch.object(cdk_synth.subprocess, "run", side_effect=_capture):
        cdk_synth.synth_with_config("c", {})

    argv = captured["args"][0]
    assert argv[0] == "cdk"
    assert argv[1] == "synth"
    assert "--quiet" in argv
    assert "--no-staging" in argv
    assert "--app" in argv
    # App value is the current python executable + "app.py"
    app_idx = argv.index("--app") + 1
    assert "app.py" in argv[app_idx]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_exits_zero_when_all_configs_pass(tmp_cdk_json, capsys):
    with (
        patch.object(cdk_synth, "CONFIGS", [("a", {}), ("b", {})]),
        patch.object(cdk_synth, "synth_with_config", return_value=True),
    ):
        rc = cdk_synth.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 passed, 0 failed" in out
    assert "All configurations synthesized successfully." in out


def test_main_exits_one_when_any_config_fails(tmp_cdk_json, capsys):
    with (
        patch.object(cdk_synth, "CONFIGS", [("a", {}), ("b", {}), ("c", {})]),
        patch.object(cdk_synth, "synth_with_config", side_effect=[True, False, True]),
    ):
        rc = cdk_synth.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "1 failed" in out
    assert "Failed configs: b" in out


def test_main_reports_multiple_failures(tmp_cdk_json, capsys):
    with (
        patch.object(cdk_synth, "CONFIGS", [("a", {}), ("b", {}), ("c", {})]),
        patch.object(cdk_synth, "synth_with_config", side_effect=[False, True, False]),
    ):
        rc = cdk_synth.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "2 failed" in out
    assert "a" in out and "c" in out


def test_main_exits_zero_on_empty_config_list(tmp_cdk_json, capsys):
    """An empty CONFIGS list is a no-op pass — there's nothing to fail."""
    with patch.object(cdk_synth, "CONFIGS", []):
        rc = cdk_synth.main()
    assert rc == 0


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------


def test_script_exports_expected_symbols():
    """Public entry points the CI workflow relies on."""
    assert callable(cdk_synth.main)
    assert callable(cdk_synth.synth_with_config)


def test_script_shares_configs_list_with_cdk_config_matrix():
    """The CONFIGS list is the one imported from ``tests._cdk_config_matrix``.

    If this drifts, ``test_cdk_synthesis.py`` and
    ``tests/test_nag_compliance.py`` stop exercising the same matrix —
    the exact failure mode the shared module was created to prevent.
    """
    from tests._cdk_config_matrix import CONFIGS as shared

    assert cdk_synth.CONFIGS is shared or list(cdk_synth.CONFIGS) == list(shared)
