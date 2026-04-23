"""
Tests for scripts/bump_version.py.

Exercises the SemVer version bumper that keeps gco/_version.py and
cli/__init__.py in sync: reading the current version (single- and
double-quoted forms), patch/minor/major bumps with correct field
resets, dry-run mode that prints but doesn't write, invalid-input
error paths, and the main() CLI dispatcher including case-insensitive
bump arguments. Uses a tmp_path-backed fixture that patches the
module's PROJECT_ROOT, VERSION_FILE, and CLI_INIT_FILE constants so
the real repo files are never touched.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import bump_version


@pytest.fixture
def version_files(tmp_path):
    """Create temporary version files for testing."""
    version_py = tmp_path / "gco" / "_version.py"
    version_py.parent.mkdir(parents=True)
    version_py.write_text('"""Version."""\n\n__version__ = "1.2.3"\n')

    cli_init = tmp_path / "cli" / "__init__.py"
    cli_init.parent.mkdir(parents=True)
    cli_init.write_text(
        '"""CLI."""\n\ntry:\n    from gco._version import __version__\n'
        'except ImportError:\n    __version__ = "1.2.3"\n'
    )

    # Patch the module-level paths
    with (
        patch.object(bump_version, "PROJECT_ROOT", tmp_path),
        patch.object(bump_version, "VERSION_FILE", version_py),
        patch.object(bump_version, "CLI_INIT_FILE", cli_init),
    ):
        yield tmp_path, version_py, cli_init


class TestGetVersion:
    def test_reads_version(self, version_files):
        _, _, _ = version_files
        assert bump_version.get_version() == "1.2.3"

    def test_missing_version_raises(self, version_files):
        _, version_py, _ = version_files
        version_py.write_text("# no version here\n")
        with pytest.raises(ValueError, match="Could not find version"):
            bump_version.get_version()

    def test_single_quoted_version(self, version_files):
        _, version_py, _ = version_files
        version_py.write_text("__version__ = '9.8.7'\n")
        assert bump_version.get_version() == "9.8.7"


class TestBumpVersion:
    def test_patch(self):
        with patch.object(bump_version, "get_version", return_value="1.2.3"):
            assert bump_version.bump_version("patch") == "1.2.4"

    def test_minor(self):
        with patch.object(bump_version, "get_version", return_value="1.2.3"):
            assert bump_version.bump_version("minor") == "1.3.0"

    def test_major(self):
        with patch.object(bump_version, "get_version", return_value="1.2.3"):
            assert bump_version.bump_version("major") == "2.0.0"

    def test_patch_resets_nothing(self):
        with patch.object(bump_version, "get_version", return_value="0.0.0"):
            assert bump_version.bump_version("patch") == "0.0.1"

    def test_minor_resets_patch(self):
        with patch.object(bump_version, "get_version", return_value="1.5.9"):
            assert bump_version.bump_version("minor") == "1.6.0"

    def test_major_resets_minor_and_patch(self):
        with patch.object(bump_version, "get_version", return_value="3.7.11"):
            assert bump_version.bump_version("major") == "4.0.0"

    def test_invalid_bump_type(self):
        with (
            patch.object(bump_version, "get_version", return_value="1.0.0"),
            pytest.raises(ValueError, match="Invalid bump type"),
        ):
            bump_version.bump_version("hotfix")

    def test_invalid_version_format(self):
        with (
            patch.object(bump_version, "get_version", return_value="1.2"),
            pytest.raises(ValueError, match="Invalid version format"),
        ):
            bump_version.bump_version("patch")


class TestUpdateVersionFile:
    def test_updates_version(self, version_files):
        _, version_py, _ = version_files
        bump_version.update_version_file("2.0.0")
        content = version_py.read_text()
        assert '__version__ = "2.0.0"' in content

    def test_dry_run_no_change(self, version_files):
        _, version_py, _ = version_files
        original = version_py.read_text()
        bump_version.update_version_file("2.0.0", dry_run=True)
        assert version_py.read_text() == original


class TestUpdateCliInit:
    def test_updates_fallback_version(self, version_files):
        _, _, cli_init = version_files
        bump_version.update_cli_init("2.0.0")
        content = cli_init.read_text()
        assert '__version__ = "2.0.0"' in content

    def test_dry_run_no_change(self, version_files):
        _, _, cli_init = version_files
        original = cli_init.read_text()
        bump_version.update_cli_init("2.0.0", dry_run=True)
        assert cli_init.read_text() == original


class TestSetVersion:
    def test_updates_all_files(self, version_files):
        _, version_py, cli_init = version_files
        bump_version.set_version("5.0.0")
        assert '__version__ = "5.0.0"' in version_py.read_text()
        assert '__version__ = "5.0.0"' in cli_init.read_text()

    def test_dry_run_updates_nothing(self, version_files):
        _, version_py, cli_init = version_files
        orig_v = version_py.read_text()
        orig_c = cli_init.read_text()
        bump_version.set_version("5.0.0", dry_run=True)
        assert version_py.read_text() == orig_v
        assert cli_init.read_text() == orig_c


class TestMain:
    def test_no_args_shows_current_version(self, version_files, capsys):
        with patch.object(sys, "argv", ["bump_version.py"]):
            bump_version.main()
        out = capsys.readouterr().out
        assert "Current version: 1.2.3" in out
        assert "gco/_version.py" in out
        assert "cli/__init__.py" in out

    def test_patch_bump(self, version_files, capsys):
        _, version_py, cli_init = version_files
        with patch.object(sys, "argv", ["bump_version.py", "patch"]):
            bump_version.main()
        out = capsys.readouterr().out
        assert "1.2.3 -> 1.2.4" in out
        assert '__version__ = "1.2.4"' in version_py.read_text()
        assert '__version__ = "1.2.4"' in cli_init.read_text()

    def test_minor_bump(self, version_files, capsys):
        _, version_py, _ = version_files
        with patch.object(sys, "argv", ["bump_version.py", "minor"]):
            bump_version.main()
        assert '__version__ = "1.3.0"' in version_py.read_text()

    def test_major_bump(self, version_files, capsys):
        _, version_py, _ = version_files
        with patch.object(sys, "argv", ["bump_version.py", "major"]):
            bump_version.main()
        assert '__version__ = "2.0.0"' in version_py.read_text()

    def test_dry_run_flag(self, version_files, capsys):
        _, version_py, _ = version_files
        original = version_py.read_text()
        with patch.object(sys, "argv", ["bump_version.py", "patch", "--dry-run"]):
            bump_version.main()
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert version_py.read_text() == original

    def test_dry_run_short_flag(self, version_files, capsys):
        _, version_py, _ = version_files
        original = version_py.read_text()
        with patch.object(sys, "argv", ["bump_version.py", "minor", "-n"]):
            bump_version.main()
        assert version_py.read_text() == original

    def test_invalid_bump_type_exits(self, version_files):
        with (
            patch.object(sys, "argv", ["bump_version.py", "hotfix"]),
            pytest.raises(SystemExit),
        ):
            bump_version.main()

    def test_case_insensitive(self, version_files, capsys):
        _, version_py, _ = version_files
        with patch.object(sys, "argv", ["bump_version.py", "PATCH"]):
            bump_version.main()
        assert '__version__ = "1.2.4"' in version_py.read_text()
