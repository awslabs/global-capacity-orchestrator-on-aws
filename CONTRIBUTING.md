# Contributing to GCO (Global Capacity Orchestrator on AWS)

Thank you for contributing to GCO (Global Capacity Orchestrator on AWS)! This guide will help you get started.

## Table of Contents

- [Development Setup](#development-setup)
  - [Prerequisites](#prerequisites)
  - [Local Development Environment](#local-development-environment)
- [Development Workflow](#development-workflow)
  - [Dependency Management](#dependency-management)
  - [Type Checking](#type-checking)
  - [Authentication](#authentication)
- [Code Organization](#code-organization)
  - [Directory Structure](#directory-structure)
  - [Adding New Features](#adding-new-features)
- [Testing](#testing)
  - [Running Tests Locally](#running-tests-locally)
  - [CI/CD Pipeline](#cicd-pipeline)
  - [Integration Tests](#integration-tests)
- [Documentation](#documentation)
- [Code Review Guidelines](#code-review-guidelines)
- [Release Process](#release-process)
- [Best Practices](#best-practices)
- [Common Tasks](#common-tasks)
- [Getting Help](#getting-help)
- [Code of Conduct](#code-of-conduct)

## Development Setup

### Prerequisites

- AWS account with appropriate permissions
- Python 3.10+ (required for type union syntax `str | None`)
- Node.js 20+ (for CDK)
- Docker or Finch
- kubectl
- Git

**Alternative: Use the Dev Container** (Python 3.14, Node.js 24, CDK, kubectl, AWS CLI) to avoid local dependency issues (see below).

### Local Development Environment

```bash
# Clone repository
git clone <repository-url>
cd GCO

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### Using the Dev Container (Recommended)

The dev container includes all dependencies pre-installed (Python 3.14, Node.js 24, CDK, kubectl, AWS CLI). This avoids "works on my machine" issues.

```bash
# Build the container
docker build -f Dockerfile.dev -t gco-dev .

# Run an interactive shell
docker run -it --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd):/workspace \
  -w /workspace \
  gco-dev

# Or run a single command
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd):/workspace \
  -w /workspace \
  gco-dev gco stacks list

# Run CDK commands
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd):/workspace \
  -w /workspace \
  -e CDK_DOCKER=docker \
  gco-dev cdk synth

# Run tests
docker run --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  gco-dev pytest tests/ -v
```

**Tip**: Create a shell alias for convenience:
```bash
alias gco-dev='docker run --rm -v ~/.aws:/root/.aws:ro -v $(pwd):/workspace -w /workspace gco-dev'
# Then use: gco-dev gco stacks list
```

## Development Workflow

### Dependency Management

GCO uses exact-pinned dependencies in `pyproject.toml` and a committed lockfile (`requirements-lock.txt`) for reproducible builds.

#### Dependency Groups

| Group | Install command | What it includes |
|-------|----------------|------------------|
| Core | `pip install -e .` | CLI runtime deps (boto3, click, requests, etc.) |
| CDK | `pip install -e ".[cdk]"` | AWS CDK, cdk-nag, constructs (for stack synthesis) |
| Dev | `pip install -e ".[dev]"` | Everything: CDK + lint + typecheck + test + security |
| MCP | `pip install -e ".[mcp]"` | FastMCP server |

CDK dependencies are in a separate `[cdk]` extras group so operators who only use the CLI don't need to install the full CDK toolchain.

#### Regenerating the Lockfile

After updating any dependency version in `pyproject.toml`, regenerate the lockfile:

```bash
pip install pip-tools
pip-compile --no-emit-index-url --strip-extras -o requirements-lock.txt pyproject.toml
```

Commit the updated `requirements-lock.txt` alongside your `pyproject.toml` changes. The lockfile pins all transitive dependencies to ensure reproducible builds across environments.

#### Installing from the Lockfile

For reproducible installs (CI, production containers):

```bash
pip install -r requirements-lock.txt
pip install -e . --no-deps
```

### Type Checking

mypy runs across the entire codebase with `--check-untyped-defs` enabled. The CI pipeline has two type-checking jobs:

1. **`lint:typecheck`** — Checks `gco/config/`, `gco/models/`, `gco/services/`, and `cli/`. Installs only mypy + type stubs (fast, no CDK needed).
2. **`lint:typecheck-stacks`** — Checks `gco/stacks/`. Installs CDK dependencies since stack code uses CDK types.

To run locally:

```bash
# Check everything except stacks (fast, no CDK needed)
mypy gco/config/ gco/models/ gco/services/ cli/ --ignore-missing-imports --check-untyped-defs

# Check stacks (requires CDK: pip install -e ".[cdk,typecheck]")
mypy gco/stacks/ --ignore-missing-imports --check-untyped-defs

# Check everything at once (requires CDK installed)
mypy gco/ cli/ --ignore-missing-imports --check-untyped-defs
```

### Authentication

The in-cluster services use token-based authentication via AWS Secrets Manager. The auth middleware (`gco/services/auth_middleware.py`) validates an `X-GCO-Auth-Token` header on every request (except health checks).

**Important:** The middleware is fail-closed by default. If `AUTH_SECRET_ARN` is not set and `GCO_DEV_MODE` is not enabled, all authenticated requests return 503. To run services locally without Secrets Manager:

```bash
export GCO_DEV_MODE=true
```

This is intentional — a missing `AUTH_SECRET_ARN` in production should fail loudly rather than silently allowing unauthenticated access.

### 1. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes

Follow these guidelines:

- **Code Style**: Follow PEP 8 for Python, use type hints
- **Documentation**: Update docs for any user-facing changes
- **Tests**: Add tests for new functionality
- **Commits**: Use clear, descriptive commit messages

### 3. Test Locally

```bash
# Synthesize CDK
cdk synth

# Deploy to dev account
export AWS_PROFILE=dev
gco stacks deploy-all -y

# Run tests
pytest tests/

# Verify deployment
kubectl get pods -n gco-system
gco jobs list -r us-east-1
```

### 4. Submit Changes

```bash
# Commit changes
git add .
git commit -m "feat: add new feature"

# Push to remote
git push origin feature/your-feature-name

# Create pull request
# Follow your organization's PR process
```

## Code Organization

### Directory Structure

```
gco/
├── stacks/                  # CDK stack definitions
├── services/                # Kubernetes services (Python/FastAPI)
├── models/                  # Data models
└── config/                  # Configuration management

cli/                         # CLI commands and utilities
  ├── commands/              # Per-group command modules (jobs, capacity, stacks, …)
  ├── main.py                # Root CLI group and entry point
  ├── kubectl_helpers.py     # Shared kubeconfig utilities

lambda/
├── kubectl-applier-simple/  # Lambda for kubectl operations
├── helm-installer/          # Lambda for Helm chart installation
├── api-gateway-proxy/       # API Gateway proxy Lambda
├── ga-registration/         # Global Accelerator registration
├── secret-rotation/         # Secret rotation Lambda
└── alb-header-validator/    # ALB header validation

dockerfiles/         # Dockerfiles for K8s services
docs/                # Documentation
examples/            # Example job manifests
tests/               # Test suites
scripts/             # Utility scripts
```

### Adding New Features

#### New CDK Stack

1. Create file in `gco/stacks/`
2. Import in `app.py`
3. Add to deployment workflow
4. Document in `docs/ARCHITECTURE.md`

#### New Kubernetes Service

1. Create service code in `gco/services/`
2. Create Dockerfile in `dockerfiles/`
3. Add manifest to `lambda/kubectl-applier-simple/manifests/`
4. Update `regional_stack.py` to build image
5. Document in README

#### New Region Support

1. Update `cdk.json` context
2. Test deployment
3. Update documentation
4. Verify Global Accelerator integration

## Testing

### Running Tests Locally

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_integration.py

# Run with coverage
pytest --cov=gco --cov=cli tests/

# Run with verbose output
pytest tests/ -v

# Run only unit tests
pytest tests/ -m unit

# Run only integration tests
pytest tests/ -m integration
```

### CI/CD Pipeline

The project uses GitLab CI/CD for automated testing. The pipeline runs on every push and merge request.

#### Pipeline Stages

1. **Validate** - All checks run in parallel:
   - Python linting (Black, Ruff, isort)
   - YAML validation
   - Dockerfile linting
   - Type checking (mypy)
   - CDK synthesis validation
   - CDK configuration matrix (20 config combinations)
   - Fresh install verification (imports and entry points)
   - Unit tests with coverage
   - Security scanning (Bandit, Safety, Trivy, TruffleHog)
   - Docker image builds

2. **Integration** - Runs after validate:
   - API contract validation
   - Kubernetes manifest validation
   - Lambda handler import verification
   - Lambda build directory validation (via CDK synth)

3. **Deploy Preview** - Coverage report publishing

4. **Release** - Manual version bumps (patch/minor/major)

5. **Maintenance** - Scheduled + manual jobs (dependency checks)
   - `dependency-scan:monthly` runs automatically on the 1st of each month (requires pipeline schedule setup)
   - `security-scan:weekly` runs every Monday to check for new CVEs (requires pipeline schedule setup)
   - `dependency-check` can be triggered manually at any time

#### Running Pipeline Locally

You can simulate the CI pipeline locally:

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run linters
black --check gco/ cli/ tests/ lambda/ scripts/
ruff check gco/ cli/ tests/
isort --check-only gco/ cli/ tests/ lambda/ scripts/

# Run type checks (everything except stacks — fast, no CDK needed)
mypy gco/config/ gco/models/ gco/services/ cli/ --ignore-missing-imports --check-untyped-defs

# Run type checks on stacks (requires CDK)
pip install -e ".[cdk,typecheck]"
mypy gco/stacks/ --ignore-missing-imports --check-untyped-defs

# Run security scans
bandit -r gco/ cli/ -c pyproject.toml --severity-level medium

# Run tests with coverage
pytest tests/ --cov=gco --cov=cli --cov-report=html --cov-fail-under=85

# Run CDK config matrix (tests 20 config combinations synthesize cleanly)
python scripts/test-cdk-synthesis.py

# Regenerate the lockfile (after dependency changes)
pip-compile --no-emit-index-url --strip-extras -o requirements-lock.txt pyproject.toml
```

#### Pipeline Badges

The README displays pipeline status and coverage badges:
- Pipeline status: Shows if the latest build passed
- Coverage: Shows test coverage percentage

### Integration Tests

```bash
# Deploy to test environment
export AWS_PROFILE=test
gco stacks deploy-all -y

# Run tests against deployed environment
pytest tests/ -v

# Clean up
gco stacks destroy-all -y
```

## Documentation

### When to Update Docs

- New features or capabilities
- Changes to deployment process
- New configuration options
- Breaking changes
- Bug fixes that affect users

### Documentation Files

- `README.md`: Overview and quick start
- `QUICKSTART.md`: Step-by-step setup guide
- `docs/ARCHITECTURE.md`: Technical architecture
- `docs/CLI.md`: CLI reference
- `docs/API.md`: REST API reference
- `docs/CONCEPTS.md`: Core concepts for new users
- `docs/CUSTOMIZATION.md`: How to customize
- `docs/TROUBLESHOOTING.md`: Common issues
- `docs/RUNBOOKS.md`: Operational runbooks for incident response
- `CONTRIBUTING.md`: This file

### Documentation Style

- Use clear, concise language
- Include code examples
- Add diagrams where helpful
- Keep it up-to-date with code changes

## Code Review Guidelines

### For Authors

- Keep PRs focused and reasonably sized
- Write clear PR descriptions
- Include tests
- Update documentation
- Respond to feedback promptly

### For Reviewers

- Be constructive and respectful
- Focus on code quality and maintainability
- Check for security issues
- Verify documentation is updated
- Test changes if possible

## Release Process

### Versioning

We use semantic versioning (MAJOR.MINOR.PATCH):

- MAJOR: Breaking changes
- MINOR: New features (backward compatible)
- PATCH: Bug fixes

### CI/CD Token Setup

The release and dependency-check jobs require a `RELEASE_TOKEN` with appropriate permissions. This is a one-time setup:

#### Step 1: Create a Project Access Token

1. Go to your GitLab project → Settings → Access Tokens
2. Create a new token with these settings:
   - **Token name**: `CI Release Token` (or similar)
   - **Role**: `Maintainer`
   - **Scopes** (check all of these):
     - `api` - Required for creating issues (dependency-check job)
     - `read_repository` - Required for git operations
     - `write_repository` - Required for pushing tags and commits (release jobs)
   - **Expiration**: Set as appropriate for your security policy (recommend 1 year)
3. Click "Create project access token"
4. **Copy the token value immediately** - it won't be shown again

#### Step 2: Add Token as CI/CD Variable

1. Go to Settings → CI/CD → Variables
2. Click "Add variable"
3. Configure the variable:
   - **Key**: `RELEASE_TOKEN`
   - **Value**: Paste the token you copied
   - **Type**: Variable
   - **Flags**:
     - ✅ Mask variable (hides value in job logs)
     - ⬜ Protect variable (optional - if checked, only available on protected branches)
     - ⬜ Expand variable reference (leave unchecked)
4. Click "Add variable"

#### What Each Job Needs

| Job | Required Scopes | Purpose |
|-----|-----------------|---------|
| `release:patch/minor/major` | `write_repository` | Push version commits and tags |
| `dependency-check` | `api` | Create GitLab issues for outdated deps |

### Creating a Release

Releases are created via the GitLab CI/CD pipeline:

1. Go to CI/CD → Pipelines → Latest pipeline on main
2. Find the Release stage and click the appropriate job:
   - `release:patch` - Bug fixes (0.0.X)
   - `release:minor` - New features (0.X.0)
   - `release:major` - Breaking changes (X.0.0)
3. Click "Play" to trigger the release

The job will automatically:
- Bump the version in `gco/_version.py`
- Commit the change
- Create a git tag
- Push to the repository

#### Manual Release (Alternative)

If you need to release manually:

```bash
# Bump version
python scripts/bump_version.py patch  # or minor/major

# Commit and tag
git add gco/_version.py
git commit -m "Release v1.2.3"
git tag -a v1.2.3 -m "Release v1.2.3"
git push origin main --tags
```

After releasing, update CHANGELOG.md and deploy to production environments.

### Dependency Updates

The `dependency-check` job checks for outdated Python packages and Docker images, then creates a GitLab issue if updates are available.

#### What Gets Checked

1. **Python Packages**: All packages in `pyproject.toml` are checked against PyPI for newer versions
2. **Docker Images**: Images are checked against their registries for newer semver tags
   - CI images in `.gitlab-ci.yml`
   - K8s manifests in `lambda/kubectl-applier-simple/manifests/` (e.g., NVIDIA device plugin)
   - Example manifests in `examples/`
   - Helm chart value images in `lambda/helm-installer/charts.yaml` (e.g., Slurm operator, KubeRay)
   - Uses `skopeo` to query Docker Hub, Quay.io, GHCR, and other registries
   - Only checks semver-tagged images (e.g., `v1.2.3`, `1.2.3`)
   - Skips `gco/*` images (we control those), `latest` tags, and template variables

#### Running the Dependency Check

1. Go to CI/CD → Pipelines → Latest pipeline on main
2. Find the Maintenance stage and click `dependency-check`
3. Click "Play" to trigger the check

The job will:
- Check all packages in `pyproject.toml` for newer versions
- Check Docker images for newer semver tags
- Create a GitLab issue with tables of outdated dependencies
- Include current version, latest version, and update instructions

#### Scheduling Automatic Checks

A monthly dependency scan (`dependency-scan:monthly`) is built into the pipeline and runs on the 1st of each month at 09:00 UTC. It uses the same logic as the manual `dependency-check` job.

**One-time setup** (required to activate the schedule):

1. Go to CI/CD → Schedules
2. Click "New schedule"
3. Configure:
   - **Description**: `Monthly dependency scan`
   - **Interval pattern**: `0 9 1 * *`
   - **Cron timezone**: UTC
   - **Target branch**: `main`
4. Add a variable:
   - **Key**: `SCHEDULED_SCAN`
   - **Value**: `true`
5. Click "Create pipeline schedule"

The job will automatically check all Python packages and Docker images (CI, K8s manifests, examples) for newer versions and create a GitLab issue if updates are found.

You can also trigger the scan manually at any time via the `dependency-check` job in the Maintenance stage.

#### Weekly Security Scan (CVE Detection)

A weekly CVE scan (`security-scan:weekly`) runs every Monday at 09:00 UTC. It re-checks your dependencies against the latest Trivy and Safety vulnerability databases to catch newly published CVEs for packages you already depend on.

**One-time setup** (required to activate the schedule):

1. Go to CI/CD → Schedules
2. Click "New schedule"
3. Configure:
   - **Description**: `Weekly security scan`
   - **Interval pattern**: `0 9 * * 1`
   - **Cron timezone**: UTC
   - **Target branch**: `main`
4. Add a variable:
   - **Key**: `SECURITY_SCAN`
   - **Value**: `true`
5. Click "Create pipeline schedule"

This is separate from the per-push security scans (which run on every commit). The weekly scan exists because CVE databases update daily — a dependency that was clean last week may have a new HIGH/CRITICAL vulnerability today.

#### Checking EKS Addon Versions

EKS addon versions are not automatically checked by the CI pipeline (requires AWS credentials). Periodically check for updates manually:

```bash
# Check latest versions for all addons used by GCO
K8S_VERSION="1.35"  # Match your configured Kubernetes version

for addon in metrics-server aws-efs-csi-driver amazon-cloudwatch-observability aws-fsx-csi-driver; do
  echo "=== $addon ==="
  aws eks describe-addon-versions \
    --addon-name $addon \
    --kubernetes-version $K8S_VERSION \
    --query 'addons[0].addonVersions[0].addonVersion' \
    --output text
done
```

Current addon versions are defined in `gco/stacks/regional_stack.py`. To update:

1. Run the command above to get latest versions
2. Update the `addon_version` parameter for each addon in `regional_stack.py`
3. Test the deployment in a non-production environment first
4. Review the [EKS addon release notes](https://docs.aws.amazon.com/eks/latest/userguide/eks-add-ons.html) for breaking changes

## Best Practices

### Security

- Never commit secrets or credentials
- Use IAM roles, not access keys
- Follow least-privilege principle
- Encrypt sensitive data
- Review security groups and network ACLs

### Performance

- Optimize Docker images (use slim base images)
- Set appropriate resource limits
- Use caching where possible
- Monitor and profile performance

### Cost Optimization

- Use Spot instances for fault-tolerant workloads
- Right-size resources
- Clean up unused resources
- Set up cost alerts

### Reliability

- Implement health checks
- Use multiple replicas
- Test failure scenarios
- Monitor and alert on issues

## Common Tasks

### Adding a New Kubernetes Manifest

```bash
# 1. Create manifest file
cat > lambda/kubectl-applier-simple/manifests/33-my-service.yaml << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-service
  namespace: gco-system
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-service
  template:
    metadata:
      labels:
        app: my-service
    spec:
      containers:
        - name: my-service
          image: {{MY_SERVICE_IMAGE}}
          ports:
            - containerPort: 8080
EOF

# 2. Update CDK stack to build image (if needed)
# Edit gco/stacks/regional_stack.py

# 3. Deploy
gco stacks deploy-all -y
```

### Updating Service Code

```bash
# 1. Make changes to service
vim gco/services/health_monitor.py

# 2. Test locally (if possible)
python gco/services/health_monitor.py

# 3. Rebuild and deploy
gco stacks deploy-all -y

# 4. Verify deployment
kubectl get pods -n gco-system
gco jobs list -r us-east-1
```

### Debugging Issues

```bash
# Check CloudFormation events
aws cloudformation describe-stack-events \
  --stack-name gco-us-east-1 \
  --region us-east-1 \
  --max-items 20

# Check Lambda logs
aws logs tail /aws/lambda/gco-us-east-1-KubectlApplier* \
  --region us-east-1 \
  --since 30m

# Check pod logs (requires kubectl for detailed pod inspection)
kubectl logs -n gco-system deployment/health-monitor --tail=100

# Describe pod for events (requires kubectl)
kubectl describe pod POD-NAME -n gco-system

# Check job logs via CLI
gco jobs logs JOB-NAME -n gco-jobs -r us-east-1
```

## Getting Help

- Check existing documentation
- Search for similar issues
- Open a GitHub issue

## Code of Conduct

- Be respectful and professional
- Welcome newcomers
- Focus on constructive feedback
- Collaborate openly

---

**Questions?** Open an issue on the [GCO GitHub repository](https://github.com/awslabs/global-capacity-orchestrator-on-aws/issues).
