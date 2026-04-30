# Composite Actions

Reusable GitHub Actions composite actions shared across multiple CI workflows. Invoked with `uses: ./.github/actions/<name>`.

## Table of Contents

- [Actions](#actions)
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

## Adding a New Action

1. Create a new directory under `.github/actions/` (e.g. `my-action/`)
2. Add an `action.yml` with `runs: using: "composite"` and your steps
3. Reference it in workflows with `uses: ./.github/actions/my-action`
