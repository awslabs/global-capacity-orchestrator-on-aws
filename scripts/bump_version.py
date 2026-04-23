#!/usr/bin/env python3
"""
Version bump script for GCO.

Updates version in all locations:
- gco/_version.py (single source of truth)
- cli/__init__.py (fallback version)

Usage:
    python scripts/bump_version.py major            # 0.0.0 -> 1.0.0
    python scripts/bump_version.py minor            # 0.0.0 -> 0.1.0
    python scripts/bump_version.py patch            # 0.0.0 -> 0.0.1
    python scripts/bump_version.py patch --dry-run  # Show what would change
    python scripts/bump_version.py                  # Show current version
"""

import re
import sys
from pathlib import Path

# File paths relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
VERSION_FILE = PROJECT_ROOT / "gco" / "_version.py"
CLI_INIT_FILE = PROJECT_ROOT / "cli" / "__init__.py"


def get_version() -> str:
    """Read current version from _version.py."""
    content = VERSION_FILE.read_text()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise ValueError("Could not find version in _version.py")
    return match.group(1)


def update_version_file(version: str, dry_run: bool = False) -> None:
    """Update version in gco/_version.py."""
    if dry_run:
        print(f"  [dry-run] Would update {VERSION_FILE.relative_to(PROJECT_ROOT)}")
        return
    content = VERSION_FILE.read_text()
    new_content = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"',
        content,
    )
    VERSION_FILE.write_text(new_content)
    print(f"  ✓ Updated {VERSION_FILE.relative_to(PROJECT_ROOT)}")


def update_cli_init(version: str, dry_run: bool = False) -> None:
    """Update fallback version in cli/__init__.py."""
    if dry_run:
        print(f"  [dry-run] Would update {CLI_INIT_FILE.relative_to(PROJECT_ROOT)}")
        return
    content = CLI_INIT_FILE.read_text()
    # Update the fallback version in the except ImportError block
    new_content = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"',
        content,
        flags=re.MULTILINE,
    )
    CLI_INIT_FILE.write_text(new_content)
    print(f"  ✓ Updated {CLI_INIT_FILE.relative_to(PROJECT_ROOT)}")


def bump_version(bump_type: str) -> str:
    """Bump version based on type (major, minor, patch)."""
    current = get_version()
    parts = current.split(".")

    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {current}")

    major, minor, patch = map(int, parts)

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    else:
        raise ValueError(f"Invalid bump type: {bump_type}")

    return f"{major}.{minor}.{patch}"


def set_version(version: str, dry_run: bool = False) -> None:
    """Update version in all files."""
    action = "Would update" if dry_run else "Updating"
    print(f"\n{action} version to {version}:")
    update_version_file(version, dry_run)
    update_cli_init(version, dry_run)


def main() -> None:
    # Parse arguments
    args = [a.lower() for a in sys.argv[1:]]
    dry_run = "--dry-run" in args or "-n" in args
    args = [a for a in args if a not in ("--dry-run", "-n")]

    if len(args) < 1:
        print(f"Current version: {get_version()}")
        print("\nVersion locations:")
        print(f"  - {VERSION_FILE.relative_to(PROJECT_ROOT)}")
        print(f"  - {CLI_INIT_FILE.relative_to(PROJECT_ROOT)}")
        return

    bump_type = args[0]
    if bump_type not in ("major", "minor", "patch"):
        print(f"Usage: {sys.argv[0]} [major|minor|patch] [--dry-run]")
        sys.exit(1)

    old_version = get_version()
    new_version = bump_version(bump_type)
    set_version(new_version, dry_run)

    if dry_run:
        print(f"\n[dry-run] Would bump version: {old_version} -> {new_version}")
    else:
        print(f"\n✓ Bumped version: {old_version} -> {new_version}")
        print("\nTo complete the release:")
        print("  git add gco/_version.py cli/__init__.py")
        print(f"  git commit -m 'Release v{new_version}'")
        print(f"  git tag v{new_version}")
        print("  git push origin main --tags")


if __name__ == "__main__":
    main()
