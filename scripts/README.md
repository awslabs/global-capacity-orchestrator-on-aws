# Scripts

Utility scripts for development, testing, and operations.

## Contents

| Script | Description |
|--------|-------------|
| `setup-cluster-access.sh` | Configures kubectl access to a GCO EKS cluster. Adds your IAM principal to the cluster's access entries and verifies connectivity. |
| `bump_version.py` | Bumps the project version across all locations (pyproject.toml, CLI, docs). Supports major, minor, and patch increments. |
| `test_cdk_synthesis.py` | Runs CDK synthesis across every `cdk.json` configuration overlay in `tests/_cdk_config_matrix.py`. Catches toolchain and node-side breakage, hardcoded regions, and missing conditional guards. |
| `dump_nag_findings.py` | Dev-only debugging helper: runs the `tests/test_nag_compliance.py` harness and prints every cdk-nag finding grouped by rule + resource path + config. Use this when the compliance test gate fails in CI and you want a compact per-finding view instead of pytest's `AssertionError` repr. |
| `test_webhook_delivery.py` | Tests the webhook dispatcher by sending sample events and verifying delivery, HMAC signatures, and retry behavior. |
| `test_analytics_lifecycle.py` | Programmatic driver for the analytics-environment deploy → test → destroy iteration loop. Pure `detect_state` / `next_step` / `format_remediation` functions plus an effectful `main()` that shells out to `cdk`. Surfaces the `gco analytics iterate` CLI and supports `--dry-run`. |
| `test_analytics_lifecycle.sh` | One-line bash wrapper around `test_analytics_lifecycle.py` for operators who prefer the `.sh` extension (Make targets, CI hooks). All args and environment pass through to Python. |

> CI-only scripts live under [`.github/scripts/`](../.github/scripts/). In particular, [`.github/scripts/dependency-scan.sh`](../.github/scripts/dependency-scan.sh) powers the monthly `deps-scan` workflow — see [`.github/CI.md`](../.github/CI.md#dependency-scan-script) for its full reference.

Each script has corresponding tests under `tests/` (Python) or `tests/BATS/` (shell). The matrix is documented in [`tests/README.md`](../tests/README.md) — add an entry there whenever you land a new script.

## Usage

### Setup Cluster Access

```bash
# Configure kubectl for a specific cluster and region
./scripts/setup-cluster-access.sh gco-us-east-1 us-east-1
```

Requires `PUBLIC_AND_PRIVATE` endpoint access mode in `cdk.json`. See [Customization Guide](../docs/CUSTOMIZATION.md#endpoint-access-modes) for details.

### Bump Version

```bash
python3 scripts/bump_version.py patch   # 1.0.0 → 1.0.1
python3 scripts/bump_version.py minor   # 1.0.0 → 1.1.0
python3 scripts/bump_version.py major   # 1.0.0 → 2.0.0
```

### Test CDK Synthesis

```bash
python3 scripts/test_cdk_synthesis.py
```

### Dump cdk-nag Findings

Reach for this when the `unit:cdk:nag-compliance` CI job fails. It synthesizes every config in `tests/_cdk_config_matrix.py` with the full cdk-nag rule pack lineup attached and prints a compact, grouped summary of every unsuppressed finding. Exits 0 if clean, 1 otherwise.

```bash
python3 scripts/dump_nag_findings.py
```

Once you've scoped the relevant `NagSuppressions` entries, re-run to verify, then run the pytest gate to confirm:

```bash
pytest tests/test_nag_compliance.py -n auto -q
```

### Test Webhook Delivery

```bash
python3 scripts/test_webhook_delivery.py
```

### Drive the Analytics-Environment Lifecycle

```bash
# Probe the current state without taking action
python3 scripts/test_analytics_lifecycle.py status --json

# Plan the next action but skip the actual cdk invocation
python3 scripts/test_analytics_lifecycle.py deploy --dry-run

# Run the full deploy → test → destroy → verify-clean loop
python3 scripts/test_analytics_lifecycle.py all
```

The `.sh` wrapper exists for operators who want to invoke the driver from bash
tooling — all arguments and environment are forwarded unchanged:

```bash
./scripts/test_analytics_lifecycle.sh deploy --dry-run
```

The underlying decision functions (`next_step`, `format_remediation`) are pure
and unit-tested against fake `LifecycleState` fixtures — see
[`tests/test_analytics_lifecycle_script.py`](../tests/test_analytics_lifecycle_script.py).
