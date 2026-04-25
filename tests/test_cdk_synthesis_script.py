"""
Tests for scripts/test_cdk_synthesis.py.

The script runs ``cdk synth`` across a matrix of cdk.json overlays to catch
regressions in hardcoded regions, missing conditional guards, and broken
feature-flag interactions. These unit tests cover the script's own helpers
and the main() aggregation without actually invoking CDK (which requires
Node.js and a ~30s synth per config).

Scope:
    - ``CONFIGS``                       structural integrity: every entry is a
                                        (name, dict) 2-tuple; names are unique;
                                        the "default-regions" baseline is always
                                        first so it catches broken base config
                                        before any overlay is tried.
    - ``synth_with_config`` — with ``subprocess.run`` mocked:
        * overlay merge semantics for both dict and scalar ``overrides`` values
        * the original cdk.json content is always restored after the call,
          even when CDK times out or raises
        * non-zero return code containing "Error" produces a FAIL
        * non-zero return code whose error text is only inside the NOTICES
          section is still treated as a PASS (CDK notices are noisy but not
          failures)
        * ``subprocess.TimeoutExpired`` is caught and reported as FAIL
    - ``main`` — tallies passes/failures across every CONFIGS entry, returns
      non-zero when anything failed, zero otherwise.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import test_cdk_synthesis as harness  # noqa: E402 - sys.path set above

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_cdk_json(tmp_path, monkeypatch):
    """
    Swap the module's ``CDK_JSON``, ``ORIGINAL_CONFIG``, and ``BASE_CONFIG``
    to point at a throwaway file in tmp_path. All subsequent calls to
    ``synth_with_config`` write to the tmp file only, so the real repo
    cdk.json stays untouched even if a test goes sideways.
    """
    base = {
        "context": {
            "valkey": {"enabled": False, "max_data_storage_gb": 5},
            "fsx_lustre": {"enabled": False},
            "eks_cluster": {"endpoint_access": "PRIVATE"},
            "deployment_regions": {
                "global": "us-east-2",
                "api_gateway": "us-east-2",
                "monitoring": "us-east-2",
                "regional": ["us-east-1"],
            },
        }
    }
    tmp_cdk = tmp_path / "cdk.json"
    tmp_cdk.write_text(json.dumps(base, indent=2))

    monkeypatch.setattr(harness, "CDK_JSON", tmp_cdk)
    monkeypatch.setattr(harness, "ORIGINAL_CONFIG", tmp_cdk.read_text())
    monkeypatch.setattr(harness, "BASE_CONFIG", json.loads(tmp_cdk.read_text()))

    yield tmp_cdk


# ---------------------------------------------------------------------------
# CONFIGS structural integrity
# ---------------------------------------------------------------------------


class TestConfigsMatrix:
    """The matrix itself is the contract — guard its shape."""

    def test_matrix_is_not_empty(self):
        assert len(harness.CONFIGS) > 0

    def test_every_entry_is_name_plus_overrides_tuple(self):
        for idx, entry in enumerate(harness.CONFIGS):
            assert isinstance(entry, tuple), f"CONFIGS[{idx}] is not a tuple: {entry!r}"
            assert len(entry) == 2, f"CONFIGS[{idx}] is not a 2-tuple: {entry!r}"
            name, overrides = entry
            assert isinstance(name, str) and name, f"CONFIGS[{idx}] has empty name"
            assert isinstance(
                overrides, dict
            ), f"CONFIGS[{idx}] overrides must be dict, got {type(overrides).__name__}"

    def test_config_names_are_unique(self):
        names = [name for name, _ in harness.CONFIGS]
        duplicates = {name for name in names if names.count(name) > 1}
        assert not duplicates, f"duplicate config names would overwrite each other: {duplicates}"

    def test_baseline_config_runs_first(self):
        """
        The default-regions baseline must be first so a broken base cdk.json
        surfaces before any overlay hides the real problem.
        """
        assert harness.CONFIGS[0][0] == "default-regions"
        assert harness.CONFIGS[0][1] == {}

    def test_multi_region_cases_cover_at_least_two_regions(self):
        for name, overrides in harness.CONFIGS:
            if "multi" in name or "three" in name:
                regional = overrides.get("deployment_regions", {}).get("regional")
                assert (
                    regional is not None and len(regional) >= 2
                ), f"multi-region case {name!r} should have ≥2 regional entries"


# ---------------------------------------------------------------------------
# synth_with_config — overlay behaviour
# ---------------------------------------------------------------------------


class TestSynthOverlayMerge:
    """``overrides`` should deep-merge into cdk.json's ``context`` block."""

    def test_dict_overrides_update_rather_than_replace(self, isolated_cdk_json):
        """
        A dict value for an existing dict key should update it in place so
        untouched sub-keys survive. Using valkey here because the baseline
        already has both ``enabled`` and ``max_data_storage_gb``.
        """
        captured = {}

        def fake_run(*args, **kwargs):
            # Capture cdk.json contents at the moment cdk synth would run.
            captured["config"] = json.loads(isolated_cdk_json.read_text())
            return MagicMock(returncode=0, stderr="", stdout="")

        with patch.object(harness.subprocess, "run", side_effect=fake_run):
            assert harness.synth_with_config("valkey-flip", {"valkey": {"enabled": True}})

        ctx = captured["config"]["context"]
        # Previously-untouched sub-key survives the update.
        assert ctx["valkey"]["max_data_storage_gb"] == 5
        # The overlay took effect.
        assert ctx["valkey"]["enabled"] is True

    def test_scalar_overrides_replace_outright(self, isolated_cdk_json):
        """
        Non-dict values replace the existing value whole. If someone passes
        ``{"valkey": False}`` it wipes the nested dict (the harness reads
        context keys directly, so a bool there would break callers).
        """
        captured = {}

        def fake_run(*args, **kwargs):
            captured["config"] = json.loads(isolated_cdk_json.read_text())
            return MagicMock(returncode=0, stderr="", stdout="")

        with patch.object(harness.subprocess, "run", side_effect=fake_run):
            harness.synth_with_config("scalar-replace", {"some_new_flag": True})

        assert captured["config"]["context"]["some_new_flag"] is True

    def test_cdk_json_is_always_restored(self, isolated_cdk_json):
        """
        Whether synth passes or fails, the original cdk.json text must be
        put back — otherwise subsequent CI steps see the overlay leaking.
        """
        with patch.object(
            harness.subprocess,
            "run",
            return_value=MagicMock(returncode=0, stderr="", stdout=""),
        ):
            harness.synth_with_config("restore-after-success", {"valkey": {"enabled": True}})
        # Working copy on disk matches ORIGINAL_CONFIG verbatim.
        assert isolated_cdk_json.read_text() == harness.ORIGINAL_CONFIG

    def test_cdk_json_is_restored_after_exception(self, isolated_cdk_json):
        """Generic exception from cdk synth still hits the finally-restore path."""
        with patch.object(harness.subprocess, "run", side_effect=RuntimeError("boom")):
            result = harness.synth_with_config("restore-after-error", {"valkey": {"enabled": True}})
        assert result is False
        assert isolated_cdk_json.read_text() == harness.ORIGINAL_CONFIG


# ---------------------------------------------------------------------------
# synth_with_config — return-code classification
# ---------------------------------------------------------------------------


class TestSynthReturnCodeClassification:
    def test_returncode_zero_is_pass(self, isolated_cdk_json):
        with patch.object(
            harness.subprocess,
            "run",
            return_value=MagicMock(returncode=0, stderr="", stdout=""),
        ):
            assert harness.synth_with_config("ok", {}) is True

    def test_returncode_zero_with_notices_is_still_pass(self, isolated_cdk_json):
        """
        CDK emits NOTICES even on success — they aren't failures. Verify we
        don't mis-classify a rc=0 case with trailing advisory text.
        """
        notices_stderr = "NOTICES\n\n19836 Foo\n\tFoobar notice text"
        with patch.object(
            harness.subprocess,
            "run",
            return_value=MagicMock(returncode=0, stderr=notices_stderr, stdout=""),
        ):
            assert harness.synth_with_config("notices-ok", {}) is True

    def test_error_before_notices_section_is_fail(self, isolated_cdk_json):
        """
        An ``Error: ...`` line before the NOTICES block means a real failure.
        """
        stderr = (
            "Error: Cannot find context value 'deployment_regions.regional[0]'\n"
            "NOTICES\n\n19836 Foo\n\tFoobar"
        )
        with patch.object(
            harness.subprocess,
            "run",
            return_value=MagicMock(returncode=1, stderr=stderr, stdout=""),
        ):
            assert harness.synth_with_config("real-error", {}) is False

    def test_error_only_inside_notices_is_classified_as_pass(self, isolated_cdk_json):
        """
        Some advisories live under NOTICES and include the word "error". The
        script splits on ``NOTICES`` and only fails when the error text lives
        in the pre-NOTICES section.
        """
        # ``Error`` appears only in the NOTICES advisory body; nothing before
        # the NOTICES delimiter should be flagged as a real error.
        stderr = "NOTICES\n\n12345 Deprecation: an error may occur in a future release."
        with patch.object(
            harness.subprocess,
            "run",
            return_value=MagicMock(returncode=1, stderr=stderr, stdout=""),
        ):
            assert harness.synth_with_config("notices-only-error", {}) is True

    def test_timeout_is_fail(self, isolated_cdk_json):
        with patch.object(
            harness.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="cdk", timeout=120),
        ):
            assert harness.synth_with_config("timeout", {}) is False


# ---------------------------------------------------------------------------
# main() aggregation
# ---------------------------------------------------------------------------


class TestMainAggregation:
    """``main()`` tallies per-config pass/fail and sets exit code accordingly."""

    def test_returns_zero_when_all_configs_pass(self):
        with (
            patch.object(harness, "CONFIGS", [("only", {})]),
            patch.object(harness, "synth_with_config", return_value=True),
        ):
            assert harness.main() == 0

    def test_returns_one_when_any_config_fails(self):
        with (
            patch.object(
                harness,
                "CONFIGS",
                [("pass-one", {}), ("fail-one", {}), ("pass-two", {})],
            ),
            patch.object(harness, "synth_with_config", side_effect=[True, False, True]),
        ):
            assert harness.main() == 1

    def test_runs_every_config_in_order(self):
        called_names = []

        def fake_synth(name, _overrides):
            called_names.append(name)
            return True

        with (
            patch.object(harness, "CONFIGS", [("a", {}), ("b", {}), ("c", {})]),
            patch.object(harness, "synth_with_config", side_effect=fake_synth),
        ):
            harness.main()

        assert called_names == ["a", "b", "c"]
