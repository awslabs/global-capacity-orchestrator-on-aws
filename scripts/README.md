# Scripts

Utility scripts for development, testing, and operations.

## Table of Contents

- [Contents](#contents)
- [Usage](#usage)
  - [Setup Cluster Access](#setup-cluster-access)
  - [Bump Version](#bump-version)
  - [Test CDK Synthesis](#test-cdk-synthesis)
  - [Dump cdk-nag Findings](#dump-cdk-nag-findings)
  - [Test Webhook Delivery](#test-webhook-delivery)

## Contents

| Script | Description |
|--------|-------------|
| `setup-cluster-access.sh` | Configures kubectl access to a GCO EKS cluster. Adds your IAM principal to the cluster's access entries and verifies connectivity. |
| `bump_version.py` | Bumps the project version across all locations (pyproject.toml, CLI, docs). Supports major, minor, and patch increments. |
| `dump_nag_findings.py` | Dev-only debugging helper: runs the `tests/test_nag_compliance.py` harness and prints every cdk-nag finding grouped by rule + resource path + config. Use this when the compliance test gate fails in CI and you want a compact per-finding view instead of pytest's `AssertionError` repr. |
| `test_webhook_delivery.py` | Tests the webhook dispatcher by sending sample events and verifying delivery, HMAC signatures, and retry behavior. |

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

The CDK configuration matrix is now exercised via pytest (runs in parallel under
`pytest-xdist`). Invoke it the same way CI does:

```bash
pytest tests/test_cdk_synthesis_matrix.py -n auto
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
