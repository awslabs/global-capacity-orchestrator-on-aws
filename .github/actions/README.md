# Composite Actions

Reusable GitHub Actions composite actions shared across multiple CI workflows. Invoked with `uses: ./.github/actions/<name>`.

## Table of Contents

- [Actions](#actions)
  - [`build-lambda-package`](#build-lambda-package)
  - [`upload-artifact-with-retry`](#upload-artifact-with-retry)
- [Adding a New Action](#adding-a-new-action)

## Actions

### `build-lambda-package`

Stages the Lambda build directories that CDK synth, pytest, and KICS scans all expect:

- `lambda/kubectl-applier-simple-build/` — copies handler + manifests, installs `kubernetes`, `pyyaml`, `urllib3`
- `lambda/helm-installer-build/` — copies the helm-installer source

**Used by:** `unit:cdk:synth`, `unit:cdk:config-matrix`, `unit:cdk:nag-compliance`, `unit:pytest:core`, `security:kics:iac`

**Prerequisite:** The calling job must set up Python (via `actions/setup-python`) before invoking this action.

**Usage:**

```yaml
steps:
  - uses: actions/checkout@v6
  - uses: actions/setup-python@v6
    with:
      python-version: "3.14"
  - uses: ./.github/actions/build-lambda-package
```

### `upload-artifact-with-retry`

Wraps `actions/upload-artifact@v7.0.1` with an inline retry loop. The GitHub Actions artifact backend occasionally returns 5xx or 403s during the finalize step (after the bytes are fully uploaded), and the default upload action surfaces those as a hard job failure. This composite retries the upload up to 3 times with a 30 s / 60 s backoff so a flaky finalize doesn't fail an otherwise-green CI run.

Behaviour matches `actions/upload-artifact@v7.0.1` for every successful path; the only observable difference is on transient failures.

**Inputs (passed straight through to `upload-artifact`):**

| Name | Default | Description |
|------|---------|-------------|
| `name` | (required) | Artifact name |
| `path` | (required) | File or directory path(s) to upload |
| `retention-days` | `""` | Retention period in days |
| `if-no-files-found` | `warn` | Behaviour when no matching files exist (`warn` / `error` / `ignore`) |
| `overwrite` | `false` | Whether to overwrite an existing artifact with the same name |
| `include-hidden-files` | `false` | Whether to include hidden files in the upload |

**Used by:** every workflow that uploads artifacts — `unit-tests.yml`, `integration-tests.yml`, `security.yml`, `cve-scan.yml`. Drop-in replacement for `actions/upload-artifact@v7.0.1`.

**Usage:**

```yaml
- name: Upload coverage artifacts
  if: always()
  uses: ./.github/actions/upload-artifact-with-retry
  with:
    name: pytest-coverage
    path: |
      htmlcov/
      coverage.xml
    retention-days: 7
```

## Adding a New Action

1. Create a new directory under `.github/actions/` (e.g. `my-action/`)
2. Add an `action.yml` with `runs: using: "composite"` and your steps
3. Reference it in workflows with `uses: ./.github/actions/my-action`
4. Document it under [Actions](#actions) above
5. Add an anchor link in the [Table of Contents](#table-of-contents) so the action is discoverable from the top of the file
