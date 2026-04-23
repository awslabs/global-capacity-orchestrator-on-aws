# BATS Shell Script Tests

[BATS](https://github.com/bats-core/bats-core) (Bash Automated Testing System) tests for GCO's shell scripts. These are primarily functional tests that execute real bash logic (parameter expansion, sed transforms, jq queries against cdk.json, JSON validation) rather than just checking for string presence.

## Table of Contents

- [What's Tested](#whats-tested)
- [Running Locally](#running-locally)
- [CI Integration](#ci-integration)
- [Adding New Tests](#adding-new-tests)
- [Test Design Philosophy](#test-design-philosophy)

## What's Tested

| Test File | Script Under Test | Tests | What It Covers |
|---|---|---|---|
| `test_live_demo.bats` | `demo/live_demo.sh` + `demo/lib_demo.sh` | 38 | Sources `lib_demo.sh` and calls real functions: `feature_status`, `detect_features`, `setup_pauses`, `setup_colors`, `is_assumed_role`, `extract_role_name`, `build_role_arn`. Also validates manifest YAML, namespace targeting, inference lifecycle, section completeness |
| `test_setup_cluster_access.bats` | `scripts/setup-cluster-access.sh` | 20 | Argument defaults and overrides, sources `lib_demo.sh` for ARN helpers (`is_assumed_role`, `extract_role_name`, `build_role_arn`), error handling patterns, AWS CLI call structure |
| `test_aws_cli_examples.bats` | `docs/client-examples/aws_cli_examples.sh` | 17 | API region detection with fallback chain, URL trailing-slash stripping, JSON payload validation, trusted registry enforcement, SigV4 signing patterns |
| `test_curl_sigv4_proxy.bats` | `docs/client-examples/curl_sigv4_proxy_example.sh` | 27 | URL host/path parsing pipeline, stack name construction, proxy lifecycle (port check, trap, kill), HTTP method coverage, Host header inclusion, auth failure testing, temp file cleanup |
| `test_record_demo.bats` | `demo/record_demo.sh` | 39 | Configuration defaults and overrides, preflight checks (asciinema, agg, jq, gco, kubectl, cluster connectivity, disk space), wrapper generation, asciinema/agg invocation, SKIP_GIF support, path resolution |
| `test_record_deploy.bats` | `demo/record_deploy.sh` | 13 | Syntax validation, configuration defaults, preflight checks, asciinema/agg invocation patterns, deploy command structure, output file paths |
| `test_record_destroy.bats` | `demo/record_destroy.sh` | 13 | Syntax validation, configuration defaults, preflight checks, asciinema/agg invocation patterns, destroy command structure, output file paths |

## Running Locally

```bash
# Install BATS (pick one)
npm install -g bats          # via npm
brew install bats-core       # via Homebrew (macOS)
apt install bats             # via apt (Debian/Ubuntu)

# Run all BATS tests
bats tests/BATS/

# Run a single test file
bats tests/BATS/test_live_demo.bats

# TAP output (verbose, shows each test name)
bats tests/BATS/ --tap
```

## CI Integration

BATS tests run on every push and PR as the `unit:bats:shell` job in `.github/workflows/unit-tests.yml`. They run in parallel with pytest, linting, and security scans — see the workflow file for the exact image pin and installed dependencies.

## Adding New Tests

1. Create a new `.bats` file in this directory (e.g., `test_my_script.bats`)
2. Prefer functional tests — run bash logic, parse real files, validate output
3. Use `command -v tool &>/dev/null || skip "tool not installed"` for optional dependencies
4. Use `run bash -c '...'` for portable inline evaluation (avoids subshell variable issues)
5. The CI job auto-discovers all `.bats` files in this directory

## Test Design Philosophy

These tests prioritize functional correctness over string matching:

- **Parameter expansion**: Tests actually evaluate `${VAR:-default}` and `${VAR:+override}` patterns to verify defaults and overrides work
- **sed/awk transforms**: Tests run the real sed commands from the scripts against sample input (e.g., assumed-role ARN parsing)
- **jq queries**: Tests execute the actual jq expressions against the real `cdk.json` to verify feature detection
- **JSON validation**: Tests pipe payloads through `jq -e` to verify structure, not just syntax
- **YAML validation**: Tests use `python3 -c "import yaml; ..."` to parse manifests the same way Kubernetes would
- **Portability**: All tests use `bash -c` for inline evaluation, avoid GNU-only flags (like `head -n -1`), and skip gracefully when optional tools are missing
