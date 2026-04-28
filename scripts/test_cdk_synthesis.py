#!/usr/bin/env python3
"""CDK Configuration Matrix Synthesis Tests.

Tests that `cdk synth` succeeds across a matrix of configuration
combinations. This catches issues like hardcoded regions, missing
conditional guards, and broken feature flag interactions — without
deploying anything.

The configuration matrix itself lives in
``tests/_cdk_config_matrix.CONFIGS`` so both this script and
``tests/test_nag_compliance.py`` iterate over the same set of cdk.json
overlays. See the docstring at the top of that module for the
rationale.

Usage:
    python3 scripts/test_cdk_synthesis.py [--verbose]
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

VERBOSE = "--verbose" in sys.argv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CDK_JSON = PROJECT_ROOT / "cdk.json"

# Put the repo root on sys.path so ``tests._cdk_config_matrix`` resolves
# even though this script lives under ``scripts/``.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests._cdk_config_matrix import CONFIGS  # noqa: E402

# Save original config to restore after each test
ORIGINAL_CONFIG = CDK_JSON.read_text()
BASE_CONFIG = json.loads(ORIGINAL_CONFIG)


def log(msg: str) -> None:
    print(msg, flush=True)


def synth_with_config(name: str, overrides: dict[str, Any]) -> bool:
    """Run cdk synth with a modified cdk.json config."""
    config = json.loads(json.dumps(BASE_CONFIG))
    ctx = config["context"]

    for key, value in overrides.items():
        if isinstance(value, dict) and key in ctx and isinstance(ctx[key], dict):
            ctx[key].update(value)
        else:
            ctx[key] = value

    try:
        # Write modified config
        CDK_JSON.write_text(json.dumps(config, indent=2))

        result = subprocess.run(
            ["cdk", "synth", "--quiet", "--no-staging", "--app", f"{sys.executable} app.py"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

        # CDK returns 0 on success even with notices
        if result.returncode == 0:
            log(f"  PASS: {name}")
            return True

        # Check if it's a real error or just notices
        stderr = result.stderr
        if "Error" in stderr or "error" in stderr.split("NOTICES")[0]:
            log(f"  FAIL: {name}")
            # Show the error before NOTICES
            error_part = stderr.split("NOTICES")[0].strip()
            if error_part:
                log(f"  {error_part[-300:]}")
            return False

        log(f"  PASS: {name} (with notices)")
        return True

    except subprocess.TimeoutExpired:
        log(f"  FAIL: {name} (timeout)")
        return False
    except Exception as e:
        log(f"  FAIL: {name} ({e})")
        return False
    finally:
        # Always restore original config
        CDK_JSON.write_text(ORIGINAL_CONFIG)


def main() -> int:
    log(f"Running CDK synthesis matrix: {len(CONFIGS)} configurations")
    log("=" * 60)

    passed = 0
    failed = 0
    failures = []

    for name, overrides in CONFIGS:
        success = synth_with_config(name, overrides)
        if success:
            passed += 1
        else:
            failed += 1
            failures.append(name)

    log("")
    log("=" * 60)
    log(f"Results: {passed} passed, {failed} failed out of {len(CONFIGS)}")

    if failures:
        log(f"Failed configs: {', '.join(failures)}")
        return 1

    log("All configurations synthesized successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
