# `.github/` — GitHub-native configuration

Everything GitHub reads from this folder: CI/CD workflows, issue and PR templates, Dependabot config, CODEOWNERS, composite actions used by the workflows, and helper scripts.

For contributor-facing docs (how to run tests locally, release process, dependency updates), see [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Table of contents

- [Layout](#layout)
- [Workflows](#workflows)
  - [Primary (run on every push + PR)](#primary-run-on-every-push--pr)
  - [Satellites](#satellites)
  - [Naming conventions](#naming-conventions)
  - [Cross-cutting defaults](#cross-cutting-defaults)
- [Composite actions](#composite-actions)
- [CodeQL config](#codeql-config)
- [README badges](#readme-badges)
- [Issue & PR templates](#issue--pr-templates)
- [CODEOWNERS](#codeowners)
- [Dependabot](#dependabot)
- [Helper scripts](#helper-scripts)
  - [Dependency-scan script](#dependency-scan-script)
- [Kind config](#kind-config)
- [Running checks locally](#running-checks-locally)

## Layout

```
.github/
├── actions/
│   └── build-lambda-package/       # Composite action: stage Lambda build dirs
├── codeql/
│   └── codeql-config.yml           # Paths + query-filters for Code Scanning
├── ISSUE_TEMPLATE/
│   ├── bug_report.md
│   ├── config.yml                  # Blank-issue + contact links config
│   └── feature_request.md
├── kind/
│   └── kind-calico.yaml            # Kind cluster config for integration:kind:cluster-e2e
├── scripts/
│   └── dependency-scan.sh          # Monthly dependency-drift scanner
├── workflows/
│   ├── unit-tests.yml              # Unit Tests workflow
│   ├── integration-tests.yml       # Integration Tests workflow
│   ├── security.yml                # Security workflow
│   ├── lint.yml                    # Linting workflow
│   ├── release.yml                 # Manual workflow_dispatch release
│   ├── deps-scan.yml               # Monthly dependency scan
│   └── cve-scan.yml                # Weekly CVE scan
├── CODEOWNERS
├── dependabot.yml
├── pull_request_template.md
├── release.yml                     # GitHub Release notes categorization
└── CI.md                           # You are here (reference for everything in this folder)
```

## Workflows

### Primary (run on every push + PR)

Each file maps to one row in the README badge table.

| File | README row | What it covers |
|------|------------|----------------|
| `workflows/unit-tests.yml` | Unit Tests | pytest with coverage (fail under 85%), BATS, CLI smoke, CDK synth + config matrix, lockfile freshness, fresh install, workload import checks |
| `workflows/integration-tests.yml` | Integration Tests | Per-Dockerfile build + module-import smoke, dev-container smoke, kind E2E with Calico (so NetworkPolicy is actually enforced and the apiserver validates every manifest schema), K8s manifest validation, Lambda import validation, cross-module pytest, MCP server pytest |
| `workflows/security.yml` | Security | bandit, pip-audit, trivy (filesystem + per-image matrix), trufflehog, gitleaks, semgrep, checkov, KICS |
| `workflows/lint.yml` | Linting | actionlint, black, flake8, hadolint, isort, mypy (strict / stacks / lambda), ruff, shellcheck, yamllint |

### Satellites

| File | Trigger | Purpose |
|------|---------|---------|
| `workflows/release.yml` | `workflow_dispatch` | Bump version, tag, create a GitHub Release with auto-generated notes. Uses the built-in `GITHUB_TOKEN` — no PAT required |
| `workflows/deps-scan.yml` | `cron: 0 9 1 * *` (monthly, UTC) + manual | Check Python / Docker / Helm / EKS-addon versions; open a GitHub issue if drift is found |
| `workflows/cve-scan.yml` | `cron: 0 9 * * 1` (Mondays, UTC) + manual | Re-run trivy against current CVE databases |

### Naming conventions

- **Display names:** colon-delimited `category:tool:test_name`, for example `unit:pytest:core`, `security:trivy:container-scan`, `lint:mypy:stacks`.
- **Job IDs:** hyphen-delimited (GitHub Actions requires `[A-Za-z0-9_-]`), for example `unit-pytest-core`, `security-trivy-container-scan`.
- **Click target for every badge:** the workflow file on the Actions tab, not a per-job deep link. GitHub's per-job URL scheme is inconsistent; the Actions tab surfaces every job of a workflow in one view.

### Cross-cutting defaults

All CI workflows share the same safety defaults:

- `concurrency.group: ${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: true` so rapid pushes on the same branch supersede in-flight runs. Explicitly **off** on `release.yml` — a half-run release is worse than a slow one.
- `timeout-minutes` on every job (10 min for lint, 15 for unit, 20–30 for integration).
- `permissions:` scoped narrowly. All CI workflows run with `contents: read`; `release.yml` upgrades to `contents: write` so the version-bump job can push a tag and create a GitHub Release.
- Caching: `actions/setup-python@v6` with `cache: pip` and `cache-dependency-path: requirements-lock.txt`. Mypy jobs add an explicit `actions/cache@v5` on `.mypy_cache/`.
- AWS auth (when a future test needs it) uses OIDC via `aws-actions/configure-aws-credentials@v4` — not long-lived access keys.

## Composite actions

Shared logic used by multiple jobs. Invoked with `uses: ./.github/actions/<name>`.

- **`actions/build-lambda-package`** — stages `lambda/kubectl-applier-simple-build/` and `lambda/helm-installer-build/` that CDK synth, pytest, and KICS scans all expect. Used by `unit:cdk:synth`, `unit:cdk:config-matrix`, `unit:cdk:nag-compliance`, `unit:pytest:core`, and `security:kics:iac`.

## CodeQL config

[`codeql/codeql-config.yml`](codeql/codeql-config.yml) is read by GitHub's **Default Setup** for Code Scanning whenever it exists at `.github/codeql/codeql-config.yml`. It does three things:

- **Scopes the scan** to hand-authored Python (`gco/`, `cli/`, `mcp/`, `lambda/`, `scripts/`, `app.py`). Generated output (`cdk.out/`, `lambda/*-build/`), virtualenvs, caches, tests, and the demo folder are excluded.
- **Pins the query pack** to `security-and-quality` so the additional maintainability queries still surface alongside the default security suite.
- **Filters two rules** that have been reviewed and classified as false positives against this codebase: `py/clear-text-logging-sensitive-data` (we log operational identifiers like ARNs and registry hostnames, not credential values) and `py/incomplete-url-substring-sanitization` (only ever hit by test-file assertions, not access-control code paths). Each exclusion carries an inline comment naming the exact files and the reason — audit them when the codebase shape changes.

No workflow file is checked in for CodeQL itself; the scan runs on GitHub's hosted schedule via Default Setup. If the project ever outgrows that (extra languages, custom query suites, tighter schedules), drop in a `.github/workflows/codeql.yml` and point `uses: github/codeql-action/init@v3` at `config-file: .github/codeql/codeql-config.yml` — the config file already contains everything needed.

## README badges

The README's badge row has two parts:

1. **Four workflow-status badges** (`Unit Tests`, `Integration Tests`, `Security`, `Linting`) from GitHub's native `badge.svg` endpoint.
2. **Eight stack/tech badges** (Python, CDK, EKS Auto Mode, Kubernetes, CDK-Nag, etc.) rendered by shields.io from hardcoded values, each linking to the authoritative source (pyproject.toml, cdk.json, upstream docs, etc.).

There are no auto-generated test-count or coverage badges — those were removed before the first release because they depended on an orphan `badges` branch and a shields.io endpoint that didn't resolve reliably against a private repo. Room to add them back once the repo goes public; for now the workflow status itself carries the signal.

### "repo or workflow not found" on fresh or private repositories

The four workflow-status badges at the top of the README come from GitHub's native `badge.svg` endpoint and render a placeholder image when the repo is unreachable. All other shields.io URLs (`img.shields.io/badge/...`) are static and always render.

If a stale run ever shows a `img.shields.io/github/actions/workflow/status/...` URL rendering as **"repo or workflow not found"**, the usual cause is the repo being private (shields.io hits the public GitHub REST API and gets a 404). Making the repo public resolves it; there's no code change needed.

## Issue & PR templates

- `ISSUE_TEMPLATE/bug_report.md` — structured bug report with environment, repro steps, expected vs. actual.
- `ISSUE_TEMPLATE/feature_request.md` — problem/solution/alternatives framing.
- `ISSUE_TEMPLATE/config.yml` — links out to the docs (TROUBLESHOOTING.md, QUICKSTART.md) so users who arrive here with a support question are routed there first.
- `pull_request_template.md` — summary, type-of-change checkboxes (the leading token `feat:`, `fix:`, etc. is what `release.yml` uses to categorize auto-generated release notes), testing checklist.

## CODEOWNERS

[`CODEOWNERS`](CODEOWNERS) lists path-based review owners. Reviews are requested automatically when matched paths change. Make it mandatory by enabling "Require review from Code Owners" in branch protection.

## Dependabot

[`dependabot.yml`](dependabot.yml) covers **GitHub Actions and Docker only**, not Python.

Rationale: Python deps are pinned through `requirements-lock.txt` with `pip-compile` and reviewed intentionally; Dependabot would fight that workflow. CVE-driven Python bumps are caught by the weekly `cve-scan` workflow (Trivy) and the monthly `deps-scan` workflow.

Ecosystems tracked:

- GitHub Actions (`uses:` versions across all workflows)
- Docker (`dockerfiles/`, `lambda/helm-installer/`, `Dockerfile.dev` at repo root)

## Helper scripts

- **`scripts/dependency-scan.sh`** — backs the `deps-scan` workflow. See [below](#dependency-scan-script) for the full reference.

### Dependency-scan script

`scripts/dependency-scan.sh` is the engine behind the monthly `deps-scan` workflow. It detects drift across every dependency surface the project controls and, when run from CI, writes a Markdown report that the workflow turns into a GitHub issue.

#### What it checks

| Surface | Source | Notes |
|---------|--------|-------|
| Python packages | `pip list --outdated` against the editable install of the current repo | Compares installed pins against the latest on PyPI |
| Docker image tags | `image: …:<tag>` references in `.github/workflows/*.yml`, `lambda/kubectl-applier-simple/manifests/`, `examples/`, and `lambda/helm-installer/charts.yaml` | Queries the original registry (Docker Hub, Quay, GHCR, GCR, ECR Public, registry.k8s.io) via `skopeo`; only semver tags |
| Helm charts | `lambda/helm-installer/charts.yaml` | Uses `helm show chart` for OCI charts and `helm search repo` for traditional repos |
| EKS add-ons | `addon_name`/`addon_version` pairs extracted from `gco/stacks/regional_stack.py` | Requires AWS credentials (via OIDC). The script pre-flights `sts get-caller-identity`; without valid creds the add-on section is explicitly **skipped** and the report notes why — everything else still runs |

Images matching `gco/*` are skipped (we build those). Non-semver tags (`latest`, branch names, SHAs) are ignored.

#### Inputs

Set via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKFLOWS_DIR` | `.github/workflows` | Directory scanned for Docker image references in workflow files. Lets forks that vendor workflows elsewhere still use the script. |

#### Outputs

The script writes a Markdown report to a temp file and, when invoked from a workflow, emits two keys on `$GITHUB_OUTPUT` for the caller:

| Output | Value |
|--------|-------|
| `has_drift` | `true` when any of the four surfaces reported drift, else `false` |
| `report_path` | Path to the Markdown report (only set when `has_drift=true`) |

Exit code is `0` in both cases — drift is a signal, not a failure. The `deps-scan` workflow turns `has_drift=true` into a new GitHub issue labeled `dependencies, automated`.

#### Running it locally

```bash
# Requires: python3, pip, jq, skopeo, helm, kubectl, awscli
# (install or skip individual tools — the script handles missing awscli gracefully)

bash .github/scripts/dependency-scan.sh
```

The console output shows each surface's drift inline. To trigger the exact workflow path from GitHub, go to Actions → "Deps scan" → "Run workflow" and pick `main`.

#### Extending it

- **New Docker image source** — add a `grep … >> "$ALL_IMAGES"` block alongside the existing ones. Anything with a semver tag is picked up automatically.
- **New Helm chart** — nothing to change; the script walks every entry in `lambda/helm-installer/charts.yaml`.
- **New EKS add-on** — add the `addon_name=…, addon_version=…` pair in `gco/stacks/regional_stack.py` (the regex in the script picks it up).

#### Failure modes & debugging

| Symptom | Likely cause |
|---------|--------------|
| `has_drift=false` but you expected drift | The latest-tag query returned empty (rate-limited Docker Hub, private registry). Run with `skopeo` directly to confirm |
| EKS add-on section explicitly skipped | No AWS credentials. Either expected (private repo without OIDC yet) or an OIDC misconfiguration. See [Enabling the EKS add-on check](#enabling-the-eks-add-on-check) |
| Helm chart resolution silently skipped | `helm repo add` failed. The script runs with `|| true` for these to avoid aborting on a single flaky repo; check the console log |

#### Enabling the EKS add-on check

The add-on-version section is the only surface that needs AWS credentials — there's no client-side catalog of supported EKS add-on versions (CDK doesn't ship one and neither does any public mirror; the authoritative answer only exists in the EKS API itself). Without creds the scan logs a one-line skip note and moves on, so the Python / Docker / Helm checks still report drift normally.

To turn the check on without introducing long-lived access keys, configure a GitHub OIDC trust to a read-only IAM role:

1. **Create the OIDC identity provider in the target AWS account** (one-time, skip if already present):

   ```
   URL:      https://token.actions.githubusercontent.com
   Audience: sts.amazonaws.com
   Thumbprint: (auto-fetched by AWS; no manual step)
   ```

2. **Create a role** `GCODependencyScanRole` with a trust policy scoped to this repo's main branch:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
         "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:awslabs/global-capacity-orchestrator-on-aws:ref:refs/heads/main" }
       }
     }]
   }
   ```

3. **Attach a single-permission inline policy** (principle of least privilege — the scan needs exactly one API call):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect":   "Allow",
       "Action":   "eks:DescribeAddonVersions",
       "Resource": "*"
     }]
   }
   ```

4. **Add the OIDC step to `deps-scan.yml`** just above the "Run dependency scan" step:

   ```yaml
   permissions:
     id-token: write     # required to mint the OIDC JWT
     contents: read
     issues: write
   steps:
     # ...existing checkout + tooling install steps...
     - uses: aws-actions/configure-aws-credentials@v4
       with:
         role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/GCODependencyScanRole
         aws-region: us-east-1
     - name: Run dependency scan
       # ...
   ```

The script self-detects the credentials via `aws sts get-caller-identity`. No script changes are needed when you flip this on.

## Kind config

- **`kind/kind-calico.yaml`** — kind cluster config with `disableDefaultCNI: true` so Calico can be installed on top and actually enforce the `NetworkPolicy` resources from `lambda/kubectl-applier-simple/manifests/03-network-policies.yaml`. The default kindnet CNI does not enforce NetworkPolicy. Used exclusively by `integration:kind:cluster-e2e`.

## Running checks locally

Most jobs map to a single command you can run locally. Quick reference:

```bash
# Lint (matches jobs in workflows/lint.yml)
black --check gco/ cli/ tests/ lambda/ scripts/
ruff check gco/ cli/ tests/
isort --check-only gco/ cli/ tests/ lambda/ scripts/
flake8 gco/ cli/ tests/ lambda/ scripts/
yamllint .

# Type check (matches lint:mypy:strict and lint:mypy:stacks)
mypy gco/ cli/ mcp/ scripts/ --exclude 'gco/stacks/'
mypy gco/stacks/ app.py          # requires ".[cdk,typecheck]"

# Unit tests (matches unit:pytest:core)
pytest tests/ --cov=gco --cov=cli --cov-fail-under=85 \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_nag_compliance.py

# cdk-nag compliance matrix (matches unit:cdk:nag-compliance)
pytest tests/test_nag_compliance.py -n auto

# CDK synth / config matrix (matches unit:cdk:synth and unit:cdk:config-matrix)
cdk synth --quiet
python scripts/test_cdk_synthesis.py

# Security (matches security:bandit:sast)
bandit -r gco/ cli/ -c pyproject.toml --severity-level medium

# Validate workflow files (matches lint:actionlint:workflows)
actionlint
```

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full contributor setup and dependency management workflow.
