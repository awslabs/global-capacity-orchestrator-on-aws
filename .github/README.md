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
- [Auto-generated badges](#auto-generated-badges)
- [Issue & PR templates](#issue--pr-templates)
- [CODEOWNERS](#codeowners)
- [Dependabot](#dependabot)
- [Helper scripts](#helper-scripts)
- [Kind config](#kind-config)
- [Running checks locally](#running-checks-locally)

## Layout

```
.github/
├── actions/
│   ├── build-lambda-package/       # Composite action: stage Lambda build dirs
│   └── publish-badges/             # Composite action: write shields.io JSON to badges branch
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
└── README.md                       # You are here
```

## Workflows

### Primary (run on every push + PR)

Each file maps to one row in the README badge table.

| File | README row | What it covers |
|------|------------|----------------|
| `workflows/unit-tests.yml` | Unit Tests | pytest with coverage (fail under 85%), BATS, CLI smoke, CDK synth + config matrix, lockfile freshness, fresh install, workload import checks |
| `workflows/integration-tests.yml` | Integration Tests | Per-Dockerfile build + healthcheck, dev-container smoke, kind E2E with Calico (so NetworkPolicy is actually enforced), K8s manifest schema, Lambda import validation, cross-module pytest, MCP server pytest |
| `workflows/security.yml` | Security | bandit, safety, pip-audit, trivy (filesystem + per-image matrix), trufflehog, gitleaks, semgrep, checkov, KICS |
| `workflows/lint.yml` | Linting | actionlint, black, flake8, hadolint, isort, mypy (strict / stacks / lambda), ruff, shellcheck, yamllint |

### Satellites

| File | Trigger | Purpose |
|------|---------|---------|
| `workflows/release.yml` | `workflow_dispatch` | Bump version, tag, create a GitHub Release with auto-generated notes. Uses the built-in `GITHUB_TOKEN` — no PAT required |
| `workflows/deps-scan.yml` | `cron: 0 9 1 * *` (monthly, UTC) + manual | Check Python / Docker / Helm / EKS-addon versions; open a GitHub issue if drift is found |
| `workflows/cve-scan.yml` | `cron: 0 9 * * 1` (Mondays, UTC) + manual | Re-run trivy + safety against current CVE databases |

### Naming conventions

- **Display names:** colon-delimited `category:tool:test_name`, for example `unit:pytest:core`, `security:trivy:container-scan`, `lint:mypy:stacks`.
- **Job IDs:** hyphen-delimited (GitHub Actions requires `[A-Za-z0-9_-]`), for example `unit-pytest-core`, `security-trivy-container-scan`.
- **Click target for every badge:** the workflow file on the Actions tab, not a per-job deep link. GitHub's per-job URL scheme is inconsistent; the Actions tab surfaces every job of a workflow in one view.

### Cross-cutting defaults

All CI workflows share the same safety defaults:

- `concurrency.group: ${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: true` so rapid pushes on the same branch supersede in-flight runs. Explicitly **off** on `release.yml` — a half-run release is worse than a slow one.
- `timeout-minutes` on every job (10 min for lint, 15 for unit, 20–30 for integration).
- `permissions:` scoped narrowly. Most workflows run with `contents: read`; `unit-tests.yml` upgrades to `contents: write` only on the badge-publishing step, and only for `push: main`.
- Fork PRs cannot write to protected branches. Badge publishing is gated on `github.event_name == 'push' && github.ref == 'refs/heads/main'`. We do **not** use `pull_request_target` to work around this — well-known supply-chain footgun.
- Caching: `actions/setup-python@v5` with `cache: pip` and `cache-dependency-path: requirements-lock.txt`. Mypy jobs add an explicit `actions/cache@v4` on `.mypy_cache/`.
- AWS auth (when a future test needs it) uses OIDC via `aws-actions/configure-aws-credentials@v4` — not long-lived access keys.

## Composite actions

Shared logic used by multiple jobs. Invoked with `uses: ./.github/actions/<name>`.

- **`actions/build-lambda-package`** — stages `lambda/kubectl-applier-simple-build/` and `lambda/helm-installer-build/` that CDK synth, pytest, and KICS scans all expect. Used by `unit:cdk:synth`, `unit:cdk:config-matrix`, `unit:pytest:core`, and `security:kics:iac`.
- **`actions/publish-badges`** — writes shields.io endpoint JSON files to a dedicated `badges` orphan branch. The README consumes them via `img.shields.io/endpoint?url=…`.

## Auto-generated badges

The following README badges update automatically on `push: main`:

| Badge | Job that publishes it | Source value |
|-------|-----------------------|--------------|
| `unit:pytest:core` count | `unit:pytest:core` in `workflows/unit-tests.yml` | `pytest --collect-only` |
| `unit:bats:count` | `unit:bats:shell` | `bats --count tests/BATS/` |
| `unit:coverage` | `unit:pytest:core` | `coverage.json` → `totals.percent_covered_display` |

All three are served as JSON on the orphan `badges` branch and rendered via the `img.shields.io/endpoint` URL. See `actions/publish-badges/action.yml` for the write path.

## Issue & PR templates

- `ISSUE_TEMPLATE/bug_report.md` — structured bug report with environment, repro steps, expected vs. actual.
- `ISSUE_TEMPLATE/feature_request.md` — problem/solution/alternatives framing.
- `ISSUE_TEMPLATE/config.yml` — links out to the docs (TROUBLESHOOTING.md, QUICKSTART.md) so users who arrive here with a support question are routed there first.
- `pull_request_template.md` — summary, type-of-change checkboxes (the leading token `feat:`, `fix:`, etc. is what `release.yml` uses to categorize auto-generated release notes), testing checklist.

## CODEOWNERS

[`CODEOWNERS`](CODEOWNERS) lists path-based review owners. Reviews are requested automatically when matched paths change. Make it mandatory by enabling "Require review from Code Owners" in branch protection.

## Dependabot

[`dependabot.yml`](dependabot.yml) covers **GitHub Actions and Docker only**, not Python.

Rationale: Python deps are pinned through `requirements-lock.txt` with `pip-compile` and reviewed intentionally; Dependabot would fight that workflow. CVE-driven Python bumps are caught by the weekly `cve-scan` workflow (Trivy + safety) and the monthly `deps-scan` workflow.

Ecosystems tracked:

- GitHub Actions (`uses:` versions across all workflows)
- Docker (`dockerfiles/`, `lambda/helm-installer/`, `Dockerfile.dev` at repo root)

## Helper scripts

- **`scripts/dependency-scan.sh`** — backs the `deps-scan` workflow. Checks Python packages, Docker image tags (across workflow files, K8s manifests, `examples/`, and Helm chart values in `lambda/helm-installer/charts.yaml`), Helm chart versions, and EKS add-on versions. Emits a Markdown report and writes `has_drift` + `report_path` to `$GITHUB_OUTPUT` so the caller opens a GitHub issue via `gh issue create`. EKS add-on checks require AWS credentials (via OIDC); without them the add-on section is skipped silently.

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
pytest tests/ --cov=gco --cov=cli --cov-fail-under=85 --ignore=tests/test_integration.py

# CDK synth / config matrix (matches unit:cdk:synth and unit:cdk:config-matrix)
cdk synth --quiet
python scripts/test-cdk-synthesis.py

# Security (matches security:bandit:sast)
bandit -r gco/ cli/ -c pyproject.toml --severity-level medium

# Validate workflow files (matches lint:actionlint:workflows)
actionlint
```

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full contributor setup and dependency management workflow.
