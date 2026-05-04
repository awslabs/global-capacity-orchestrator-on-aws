# GCO Test Suite

This directory contains the test suite for GCO (Global Capacity Orchestrator on AWS). The tests are organized by component and functionality.

## Table of Contents

- [Running Tests](#running-tests)
- [Test Organization](#test-organization)
- [Test Files by Category](#test-files-by-category)
- [cdk-nag Compliance Testing](#cdk-nag-compliance-testing)
- [Lambda Handler Import Helper](#lambda-handler-import-helper)
- [Writing New Tests](#writing-new-tests)
- [Mocking Patterns](#mocking-patterns)
- [Coverage Requirements](#coverage-requirements)
- [Type Checking](#type-checking)
- [Import Conventions After the Manifest API Refactor](#import-conventions-after-the-manifest-api-refactor)
- [Hermetic Environment Variables](#hermetic-environment-variables)
- [Common Issues](#common-issues)

## Running Tests

```bash
# Run all tests
python -m pytest

# Run with coverage report
python -m pytest --cov=gco --cov=cli --cov-report=term-missing

# Run specific test file
python -m pytest tests/test_manifest_api.py -v

# Run specific test class
python -m pytest tests/test_manifest_api.py::TestManifestSubmission -v

# Run specific test
python -m pytest tests/test_manifest_api.py::TestManifestSubmission::test_submit_valid_manifest -v

# Run tests matching a pattern
python -m pytest -k "health" -v
```

## Test Organization

Tests are organized by the component they test:

| Category | Files | Description |
|----------|-------|-------------|
| CLI | `test_cli*.py` | Command-line interface tests |
| API | `test_manifest_api*.py`, `test_health_api*.py` | REST API endpoint tests |
| Services | `test_manifest_processor*.py`, `test_health_monitor*.py` | Core service logic tests |
| Models | `test_models*.py` | Pydantic model validation tests |
| CDK Stacks | `test_*_stack*.py` | Infrastructure-as-code tests |
| Storage | `test_template_store.py` | DynamoDB storage layer tests |
| Integration | `test_integration.py`, `test_sqs_integration.py` | End-to-end integration tests |
| MCP Server | `test_mcp_server.py`, `test_mcp_audit.py`, `test_mcp_resources_new.py`, `test_mcp_integration.py` | MCP tools, resources, audit logging, protocol tests |
| Infrastructure | `test_oidc_stack.py`, `test_feature_toggles.py` | OIDC provider stack, feature toggle helpers (Valkey, Aurora, FSx) |

## Test Files by Category

### CLI Tests

| File | Description |
|------|-------------|
| `test_cli.py` | Core CLI functionality and argument parsing |
| `test_cli_main.py` | Main CLI entry point and command routing |
| `test_cli_commands.py` | Individual CLI command tests |
| `test_cli_coverage.py` | Additional CLI edge cases for coverage |
| `test_cli_help.py` | Help text and documentation tests |
| `test_cli_queue_templates_webhooks.py` | Queue, template, and webhook CLI commands |
| `test_cli_sqs_commands.py` | SQS-related CLI commands |

### API Tests

| File | Description |
|------|-------------|
| `test_manifest_api.py` | Core manifest API endpoints |
| `test_manifest_api_extended.py` | Extended manifest API scenarios |
| `test_manifest_api_new_endpoints.py` | New API endpoints (templates, webhooks) |
| `test_manifest_api_queue_endpoints.py` | Job queue API endpoints |
| `test_manifest_api_coverage.py` | Edge cases and error paths for coverage |
| `test_health_api.py` | Health check API endpoints |
| `test_health_api_extended.py` | Extended health API scenarios |

### Service Tests

| File | Description |
|------|-------------|
| `test_manifest_processor.py` | Manifest processing and validation |
| `test_manifest_processor_extended.py` | Extended manifest processor scenarios |
| `test_manifest_security_validation.py` | Manifest security validation (hostNetwork, hostPID, hostIPC, hostPath, capabilities, init/ephemeral containers, kind allowlist, auth middleware) |
| `test_manifest_validation_preservation.py` | Validation preservation/regression tests — ensures existing behavior is not broken by security changes |
| `test_security_policy_toggles.py` | Security policy toggle configuration tests — verifies each toggle can be individually enabled/disabled |
| `test_queue_processor.py` | SQS queue processor — manifest validation, security policy toggles (parity with `manifest_processor`), SA-token auto-mount injection, structural parity checks |
| `test_rbac_manifest.py` | RBAC manifest regression tests — verifies every runtime API path (pod logs, events, patch, metrics) has the Kubernetes RBAC grants the services need |
| `test_health_monitor.py` | Health monitoring service |
| `test_health_monitor_extended.py` | Extended health monitor scenarios |
| `test_health_monitor_main.py` | Health monitor main entry point |
| `test_auth_middleware.py` | Authentication middleware |
| `test_metrics_publisher.py` | CloudWatch metrics publishing |
| `test_template_store.py` | DynamoDB template/webhook/job storage |
| `test_helm_installer_handler.py` | Helm installer Lambda handler — ``run_helm`` timeout handling, ``_clear_stuck_release`` preflight that recovers releases wedged in ``pending-install`` / ``pending-upgrade`` / ``pending-rollback`` state from a prior interrupted deploy, and ``install_chart`` integration against the preflight (never invokes the old ``helm rollback --wait`` path that could hang on stuck operators). Mocks ``subprocess.run`` directly; does not invoke helm or kubectl. |

### Model Tests

| File | Description |
|------|-------------|
| `test_models.py` | Core Pydantic model tests |
| `test_models_extended.py` | Extended model validation scenarios |
| `test_config_loader.py` | Configuration loading and parsing |
| `test_config_loader_validation.py` | Configuration validation rules |

### CDK Stack Tests

| File | Description |
|------|-------------|
| `test_cdk_stacks.py` | General CDK stack synthesis tests |
| `test_regional_stack.py` | Regional stack (EKS, VPC) tests |
| `test_monitoring_stack.py` | Monitoring stack (CloudWatch) tests |
| `test_stacks.py` | CLI stack management commands |
| `test_stacks_extended.py` | Extended stack scenarios |

### Analytics Environment Tests

Tests for the optional `analytics_environment` (SageMaker Studio + EMR
Serverless + Cognito) and the always-on `Cluster_Shared_Bucket` in
`GCOGlobalStack`. The analytics stack is only synthesized when
`analytics_environment.enabled=true` in `cdk.json`; off-by-default
assertions live in `test_analytics_stack.py`.

| File | Description |
|------|-------------|
| `test_analytics_stack.py` | Core CDK template assertions for `GCOAnalyticsStack` — SageMaker Studio domain, EMR Serverless Spark application, Cognito user pool + client + hosted domain, `Analytics_KMS_Key`, private-isolated VPC + nine interface endpoints + S3 gateway endpoint, `Studio_EFS` + dedicated SG, `SageMaker_Execution_Role` grants (including the cross-region `Cluster_Shared_Bucket` policy resolved via `AwsCustomResource`), IAM/cdk-nag compliance |
| `test_analytics_default_image.py` | Verifies the Studio domain uses the stock AWS-published SageMaker Distribution image — no `CustomImages` key on the domain, no ECR repository resources, no `Dockerfile`s for Studio images in the repository |
| `test_analytics_bucket_isolation_property.py` | Hypothesis property test: across randomized cdk.json overlays the regional job-pod role's S3 policy only references `arn:aws:s3:::gco-cluster-shared-*` ARNs and never touches `gco-analytics-studio-*` |
| `test_analytics_configmap_property.py` | Hypothesis property test for the biconditional between `analytics_environment.enabled` and the presence of the SageMaker execution role's RW grant on `Cluster_Shared_Bucket` — enabling the toggle must materialize the grant, disabling it must remove both the role and the grant |
| `test_analytics_roundtrip_property.py` | Hypothesis property test that the two-bit `(enabled, hyperpod_enabled)` toggle state can be recovered from the synthesized CloudFormation templates alone (derive the toggles back from resource presence/absence and assert equality with the input config) |
| `test_analytics_cluster_shared_configmap_property.py` | Hypothesis property test that the `gco-cluster-shared-bucket` ConfigMap is present in every regional cluster regardless of the `analytics_environment.enabled` toggle — the cluster-shared bucket is always-on |
| `test_analytics_cmd.py` | CLI tests for `gco analytics enable/disable/status/users/studio login/doctor/iterate` including the R14.6-style toggle round-trip Hypothesis property and a `cdk synth` integration test that exercises the full analytics pipeline from CLI toggle to template |
| `test_analytics_cmd_branches.py` | Edge-case coverage for the analytics CLI command branches (error paths, missing-config fallbacks, mixed-toggle scenarios) |
| `test_analytics_user_mgmt.py` | Tests for the stdlib SRP implementation and Cognito auto-discovery helpers in `cli/analytics_user_mgmt.py` (used by `gco analytics studio login`) |
| `test_analytics_examples_validation.py` | Validates the three new analytics example manifests (notebook-hosted SageMaker job, EMR Serverless Spark job, cluster-shared-bucket read/write job) pass `ManifestProcessor.validate_manifest` against the trusted-registry security config |
| `test_analytics_lifecycle_script.py` | Tests for `scripts/test_analytics_lifecycle.py` — the pure helpers (`detect_state`, `next_step`, `format_remediation`) and the argparse `main` entry point dispatch |
| `test_api_gateway_analytics_config.py` | Tests for the `AnalyticsApiConfig` mutator and the `/studio/*` route wiring it attaches to the existing API Gateway when analytics is enabled |
| `test_cluster_shared_bucket.py` | Tests for the always-on `Cluster_Shared_Bucket` (name, KMS encryption, versioning, public-access-block, `DenyInsecureTransport` policy) + its KMS key + the `/gco/cluster-shared-bucket/{name,arn,region}` SSM parameters written by `GCOGlobalStack` |
| `test_presigned_url_lambda.py` | Tests for `lambda/analytics-presigned-url/handler.py` — happy path (`CreatePresignedDomainUrl` success), error-token mapping (auth, profile-missing, quota, throttle), and a Hypothesis property test for the response-shape invariants |

#### Analytics Test Helpers

The `tests/_analytics_*.py` modules are shared helpers, not tests. Pytest
does not pick them up as test files but the analytics tests above import
them for strategy construction, overlay generation, template parsing,
and inverse-derivation logic.

| Helper | Purpose |
|--------|---------|
| `_analytics_strategies.py` | Hypothesis strategies for randomized `analytics_environment` cdk.json overlays (enabled/disabled, hyperpod on/off, removal-policy choices, cognito prefix overrides) |
| `_analytics_cdk_overlays.py` | Materializes a strategy draw into a real cdk.json context dict that `ConfigLoader` can consume; kept separate from the strategies so the same overlay shape can be written as a fixture without running Hypothesis |
| `_analytics_template_inspectors.py` | Small library of template-walk helpers (`find_sagemaker_role`, `find_studio_bucket`, `collect_role_statements`, `extract_cluster_shared_grant`) used across the analytics stack assertions; consolidates the boilerplate that earlier iterations inlined into every test class |
| `_analytics_derivations.py` | The inverse-direction helpers — given a set of synthesized templates, derive back the `(enabled, hyperpod_enabled)` toggle pair used by `test_analytics_roundtrip_property.py` |

### CDK Configuration Matrix

The cdk.json configuration matrix — the set of overlays users can pick from (multi-region, FSx on/off, all feature toggles, resource threshold values, helm chart enable/disable, etc.) — lives in `tests/_cdk_config_matrix.CONFIGS` and is the single source of truth shared between two test surfaces:

1. **`scripts/test_cdk_synthesis.py`** runs `cdk synth --quiet` as a subprocess for each of the 24 configs. Catches node/CLI toolchain breakage, hardcoded regions, missing conditional guards, and broken feature-flag interactions. Run locally or in CI:

    ```bash
    python scripts/test_cdk_synthesis.py
    ```

2. **`tests/test_nag_compliance.py`** runs the full CDK app in-process against the same 24 configs and asserts zero unsuppressed cdk-nag findings across five rule packs (AwsSolutions, HIPAA Security, NIST 800-53 R5, PCI DSS 3.2.1, Serverless). This is the gate that prevents a user from hitting a cdk-nag error at `cdk deploy` time on a config CI hasn't already validated. See [cdk-nag Compliance Testing](#cdk-nag-compliance-testing) below for details.

Sharing the matrix is deliberate — divergence between the two lists is how we ended up with an `AwsSolutions-IAM5` error on a user's `gco-us-east-1` deploy that neither tool had exercised. Adding a new cdk.json knob means adding one entry to `tests/_cdk_config_matrix.py` and both tests pick it up.

### cdk-nag Compliance Testing

The cdk-nag rule packs that block production deploys (AwsSolutions-IAM5 wildcards, Serverless-LambdaTracing, etc.) are enforced by `tests/test_nag_compliance.py` across every `cdk.json` configuration in the shared matrix. If the test is green, every config the user can pick has been verified to produce zero unsuppressed findings.

**Why this exists:** `cdk synth --quiet` exits 0 even when unsuppressed findings exist, and we shipped a regional-stack `AwsSolutions-IAM5` finding on the auth-secret ARN to v0.1.0 that only surfaced when a user ran `cdk deploy gco-us-east-1` for the first time. The CI matrix at that point only ran `cdk synth --quiet` and called exit 0 success — the finding slipped through.

**How it works:**

- `tests/_cdk_nag_logger.py` implements a custom `INagLogger` that routes every rule-pack finding into a Python list rather than CDK's annotation system. This bypasses the CLI's silent-drop behavior.
- `tests/test_nag_compliance.py` parameterizes over the full 24-config matrix, builds the complete CDK app (Global, API Gateway, Regional, Monitoring) the same way `app.py` does, attaches all five rule packs plus the capturing logger, calls `app.synth()`, and asserts the finding list is empty.
- CI runs this as its own job — `unit:cdk:nag-compliance` — with `pytest-xdist`'s `-n auto` (via the `psutil` extra). On an 8-core runner, all 24 configs finish in ~10 minutes.

**Scope discipline for new suppressions:**

Any `NagSuppressions.add_*_suppressions` call this test forces you to add should:

- Scope via `applies_to` to the specific resource (literal ARN, regex-matched token reference, etc.). Never use `applies_to=["Resource::*"]` or `applies_to=["Action::*"]` — those are blanket bypasses that defeat the whole test.
- Include a `reason` string that explains WHY the wildcard is necessary (cross-stack token, AWS-managed policy, Secrets Manager suffix, etc.) and links to any relevant AWS documentation.
- Live as close to the construct that created the finding as possible. Resource-level suppressions via `NagSuppressions.add_resource_suppressions` are preferred over stack-level suppressions via `add_stack_suppressions` — the former fail closed when the construct is renamed.

**Debugging findings locally:**

If the test fails, run `scripts/dump_nag_findings.py` for a compact, per-finding report grouped by rule + path + config name. It uses the same test harness and gives cleaner output than pytest's `AssertionError` repr.

```bash
python3 scripts/dump_nag_findings.py
```

### Fresh Install Verification

The `test:fresh-install` CI job does a clean `pip install -e .` and verifies all critical imports work — including `cdk-nag`, `aws_cdk.aws_eks_v2`, the CLI entry point, and the CDK stack classes. This catches missing or mismatched dependencies in `pyproject.toml`.

### Lambda Build Verification

The Lambda build directory (`lambda/kubectl-applier-simple-build/`) is auto-created by `StackManager` during deploy. In CI, this is validated at multiple levels:

- `integration:lambda` — verifies all Lambda handler modules import correctly
- `test:cdk-config-matrix` — builds the Lambda package in `before_script` and runs `cdk synth` against it (synth fails if the build dir is missing or incomplete)
- `test_stacks.py::TestStackManagerSyncLambdaSources` — unit tests that `_sync_lambda_sources` auto-creates the build directory when missing

### Lambda Handler Import Helper

Lambda handler modules live under `lambda/<name>/handler.py` and aren't on Python's normal `sys.path`. Early tests loaded them with the pattern:

```python
sys.path.insert(0, "lambda/foo")
sys.modules.pop("handler", None)
import handler
```

That works in isolation but leaks across tests. Pytest runs the whole suite in one Python process, so the first test to `import handler` wins `sys.modules['handler']`. Any later test that forgets to pop — or runs after a fixture that populated it with a different Lambda's module — silently gets the wrong handler. This collision broke CI on the v0.1.0 launch when two test files' `handler` imports collided.

**The helper:** `tests/_lambda_imports.py` exposes `load_lambda_module(lambda_dir, module_name="handler", *, shared_dirs=())`. It loads the target module under a unique, namespace-safe name (e.g. `_gco_lambda_secret_rotation_handler`) via `importlib.util.spec_from_file_location`, so registrations cannot collide across tests.

Features:

- **Unique `sys.modules` name** per `(lambda_dir, module_name)` — zero collision risk.
- **Fresh load on every call** — matches the semantics of the old `sys.modules.pop + import` pattern. Fixtures that wrap the load in `patch("boto3.client")` see the mock applied on every invocation, which is required by handlers like `alb-header-validator/handler.py` that do `boto3.client("secretsmanager")` at module-import time.
- **`shared_dirs`** — for handlers that `import` from a sibling lambda dir (e.g. `lambda/api-gateway-proxy/handler.py` doing `from proxy_utils import ...`), `shared_dirs=["proxy-shared"]` pushes that dir onto `sys.path` for the duration of the load only.
- **Collateral cleanup** — when `shared_dirs` is non-empty, any new entries the load added to `sys.modules` (e.g. a bare `proxy_utils` entry) are removed afterward, so the next fixture gets a truly fresh re-import under its own mocks. Standalone loads (no `shared_dirs`) leave `sys.modules` untouched so third-party globals like `boto3` aren't disturbed.
- **Input validation** — rejects path traversal in `lambda_dir` and `shared_dirs`, raises a clean `ValueError` if the target file doesn't exist.

Typical usage in a fixture:

```python
from tests._lambda_imports import load_lambda_module

@pytest.fixture
def rotation_module():
    with patch("boto3.client") as mock_client:
        handler = load_lambda_module("secret-rotation")
        yield handler, mock_client
```

Handler that depends on a shared utility module:

```python
proxy_utils = load_lambda_module("proxy-shared", "proxy_utils")
proxy_utils._cached_secret = None
handler = load_lambda_module("api-gateway-proxy", shared_dirs=["proxy-shared"])
```

Every Lambda handler test in this repo now loads via this helper. The legacy `sys.path.insert + import handler` pattern is gone, and `tests/test_lambda_imports.py` pins the helper's contract against regression.

### Other Tests

| File | Description |
|------|-------------|
| `test_aws_client.py` | AWS SDK client wrapper tests |
| `test_capacity.py` | Capacity management tests |
| `test_files.py` | File operations tests |
| `test_files_extended.py` | Extended file operation scenarios |
| `test_jobs.py` | Job management tests |
| `test_nodepools.py` | Node pool management tests |
| `test_output.py` | Output formatting tests |
| `test_deployment_regions.py` | Multi-region deployment tests |
| `test_cross_region_aggregator.py` | Cross-region data aggregation tests |
| `test_integration.py` | End-to-end integration tests |
| `test_sqs_integration.py` | SQS integration tests |
| `test_lambda_imports.py` | Contract tests for the `tests/_lambda_imports.py` helper — unique module naming, fresh-load semantics, collateral module cleanup when `shared_dirs` is used, input validation against path traversal |
| `test_nag_compliance.py` | End-to-end cdk-nag regression: synthesizes the full CDK app (Global, API Gateway, Regional, Monitoring) against each of the 24 `cdk.json` overlays in `tests/_cdk_config_matrix.py` and asserts zero unsuppressed findings across all five rule packs (AwsSolutions, HIPAA Security, NIST 800-53 R5, PCI DSS 3.2.1, Serverless). See the [cdk-nag Compliance Testing](#cdk-nag-compliance-testing) section. |

### Script Tests

Tests for helper scripts under `scripts/`. All of them exercise their
target script's public helpers or CLI argparse dispatch — none of them
actually deploy anything, hit AWS, or spawn long-running subprocesses.

| File | Script under test | What it covers |
|------|-------------------|----------------|
| `test_bump_version.py` | `scripts/bump_version.py` | SemVer parsing, bump paths (major/minor/patch), dry-run mode, argparse dispatch, keeping `VERSION`, `gco/_version.py`, and `cli/__init__.py` in sync |
| `test_webhook_delivery_script.py` | `scripts/test_webhook_delivery.py` | `WebhookHandler` do_POST capture and 200 response, silenced `log_message`, `start_local_server` port binding + daemon thread + clean shutdown, `create_mock_job` fixture shape, `main()` argparse branches between local-server and external-URL modes |
| `test_cdk_synthesis_script.py` | `scripts/test_cdk_synthesis.py` | `CONFIGS` matrix structural integrity (unique names, correct tuple shape, baseline first), `synth_with_config` overlay merging for dict vs scalar values, cdk.json restoration after success/error/exception, return-code classification (real error vs NOTICES-only), TimeoutExpired handling, `main()` aggregation/exit code |
| `test_dump_nag_findings_script.py` | `scripts/dump_nag_findings.py` | `run_config` threads context overrides through to `_build_app_with_logger`, invokes `app.synth()` while the Docker-asset mock is live, returns `logger.findings` verbatim. `main()` aggregates by `(rule_id, resource_path, finding_id)`, deduplicates across configs, emits per-config and summary counts, exits 0 on clean and 1 otherwise |

### MCP Server Tests

| File | Description |
|------|-------------|
| `test_mcp_server.py` | Unit tests for the MCP server — `_run_cli` wrapper, tool registration, tool argument passing, resource registration, resource content reading |
| `test_mcp_audit.py` | Audit logging — argument sanitization (redaction, truncation), `@audit_logged` decorator, startup log, Hypothesis property tests for completeness and sanitization |
| `test_mcp_resources_new.py` | Tests for `tests://`, `config://`, and `docs://gco/examples/guide` resource groups, enhanced example metadata, module structure verification |
| `test_mcp_integration.py` | End-to-end MCP protocol tests via FastMCP Client — tool discovery, tool call round trips, resource reading, schema validation, stdio subprocess transport |

### Infrastructure Tests

| File | Description |
|------|-------------|
| `test_oidc_stack.py` | GitHub OIDC provider CDK stack — synthesis, OIDC provider config, trust policy (wildcard/branch/custom repo), IAM policy actions, role properties, `policy.json` validation |
| `test_feature_toggles.py` | Generic feature toggle helpers, Valkey config (get/update/enable/disable), Aurora config (get/update/enable/disable), FSx refactor regression |

### Configuration Files

| File | Description |
|------|-------------|
| `conftest.py` | Shared pytest fixtures and configuration |
| `_lambda_imports.py` | `load_lambda_module()` helper for importing Lambda handler modules under unique `sys.modules` names. See the [Lambda Handler Import Helper](#lambda-handler-import-helper) section above. |
| `_cdk_config_matrix.py` | The canonical list of `cdk.json` configuration overlays (24 entries: default, multi-region, feature toggles, thresholds, helm matrix). Imported by both `scripts/test_cdk_synthesis.py` and `tests/test_nag_compliance.py` so the two iterate over the same set. See the [CDK Configuration Matrix](#cdk-stack-tests) section. |
| `_cdk_nag_logger.py` | `CapturingCdkNagLogger` — a custom `INagLogger` implementation that routes every cdk-nag finding into a Python list instead of CDK's annotation system. Used by `test_nag_compliance.py` and `scripts/dump_nag_findings.py` to assert on findings programmatically. |
| `__init__.py` | Package initialization |

## Writing New Tests

### General Guidelines

1. **Use descriptive test names**: Test names should describe what is being tested and the expected outcome.

   ```python
   def test_submit_manifest_with_invalid_namespace_returns_403():
       ...
   ```

2. **One assertion per test when possible**: Makes failures easier to diagnose.

3. **Use fixtures for common setup**: Define reusable fixtures in `conftest.py` or at the module level.

4. **Test both success and failure paths**: Don't just test the happy path.

5. **Mock external dependencies**: Use `unittest.mock` to isolate tests from external services.

### Test Structure

```python
"""
Tests for [component name].

Brief description of what this test file covers.
"""

from unittest.mock import MagicMock, patch, AsyncMock
import pytest


@pytest.fixture
def mock_dependency():
    """Fixture description."""
    mock = MagicMock()
    mock.some_method.return_value = "expected_value"
    return mock


class TestFeatureName:
    """Tests for [feature name]."""

    def test_success_case(self, mock_dependency):
        """Test description."""
        # Arrange
        ...
        
        # Act
        result = function_under_test()
        
        # Assert
        assert result == expected

    def test_error_case(self, mock_dependency):
        """Test error handling."""
        mock_dependency.some_method.side_effect = Exception("Error")
        
        with pytest.raises(Exception):
            function_under_test()
```

## Mocking Patterns

### Mocking FastAPI Applications

When testing FastAPI endpoints, you need to mock both the factory functions AND the module-level variables:

```python
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


def test_api_endpoint(mock_manifest_processor):
    """Test an API endpoint with proper mocking."""
    mock_job_store = MagicMock()
    mock_job_store.list_jobs.return_value = [{"job_id": "abc123"}]

    with (
        patch(
            "gco.services.manifest_api.create_manifest_processor_from_env",
            return_value=mock_manifest_processor,
        ),
        patch("gco.services.manifest_api.get_template_store", return_value=MagicMock()),
        patch("gco.services.manifest_api.get_webhook_store", return_value=MagicMock()),
        patch("gco.services.manifest_api.get_job_store", return_value=mock_job_store),
    ):
        # IMPORTANT: Also set the module-level variables directly
        import gco.services.manifest_api as api_module
        api_module.manifest_processor = mock_manifest_processor
        api_module.job_store = mock_job_store

        from gco.services.manifest_api import app

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/queue/jobs")
            assert response.status_code == 200
```

### Mocking Async Functions

Use `AsyncMock` for async functions:

```python
from unittest.mock import AsyncMock

mock_processor.process_manifest_submission = AsyncMock(return_value=mock_result)
```

### Mocking Kubernetes API

```python
@pytest.fixture
def mock_manifest_processor():
    """Fixture to mock the manifest processor."""
    mock_processor = MagicMock()
    mock_processor.cluster_id = "test-cluster"
    mock_processor.region = "us-east-1"
    mock_processor.core_v1 = MagicMock()
    mock_processor.batch_v1 = MagicMock()
    mock_processor.custom_objects = MagicMock()
    mock_processor.max_cpu_per_manifest = 10000
    mock_processor.max_memory_per_manifest = 34359738368
    mock_processor.max_gpu_per_manifest = 4
    mock_processor.allowed_namespaces = {"default", "gco-jobs"}
    mock_processor.validation_enabled = True
    return mock_processor
```

### Mocking DynamoDB

```python
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB table."""
    mock_table = MagicMock()
    mock_table.get_item.return_value = {"Item": {"name": "test"}}
    mock_table.put_item.return_value = {}
    mock_table.scan.return_value = {"Items": []}
    return mock_table
```

### Providing Valid Kubernetes Manifests

When testing endpoints that process Kubernetes manifests, provide complete manifests:

```python
valid_job_manifest = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"name": "test-job"},
    "spec": {
        "template": {
            "spec": {
                "containers": [{"name": "main", "image": "test:latest"}],
                "restartPolicy": "Never",
            }
        }
    },
}
```

## Coverage Requirements

The project requires a minimum of 90% test coverage. Current coverage is ~92%.

To check coverage:

```bash
python -m pytest --cov=gco --cov=cli --cov-report=term-missing
```

To generate an HTML coverage report:

```bash
python -m pytest --cov=gco --cov=cli --cov-report=html
open htmlcov/index.html
```

### Areas Needing Coverage

When adding new features, ensure tests cover:

1. **Success paths**: Normal operation with valid inputs
2. **Error paths**: Invalid inputs, missing data, exceptions
3. **Edge cases**: Empty lists, None values, boundary conditions
4. **Authentication**: Both authenticated and unauthenticated requests
5. **Authorization**: Namespace restrictions, permission checks

## Type Checking

CI runs `mypy --strict` across three jobs:

- `lint:typecheck` — `gco/` (except stacks), `cli/`, `mcp/`, `scripts/`, `app.py`
- `lint:typecheck-stacks` — `gco/stacks/` with `aws-cdk-lib` installed
- `lint:typecheck-lambda` — each `lambda/*/` directory individually

Strict flags enabled in `pyproject.toml` include `disallow_untyped_defs`,
`disallow_untyped_calls`, `disallow_any_generics`, `no_implicit_optional`,
`warn_return_any`, and `warn_unused_ignores`. Test files relax
`disallow_untyped_defs` so fixture and helper signatures can stay concise.

Prefer concrete types over `Any`. Runtime types from the installed packages
(boto3, Kubernetes, fastapi, click) are preferred over `Any` fallbacks —
the CI typecheck jobs install the full runtime (`pip install -e ".[typecheck,mcp]"`)
so stubs resolve properly.

Run locally with the same commands:

```bash
mypy gco/ cli/ mcp/ scripts/ app.py --exclude 'gco/stacks/'
mypy gco/stacks/
for d in lambda/*/; do ls "$d"*.py >/dev/null 2>&1 && mypy "$d"; done
```

## Import Conventions After the Manifest API Refactor

The manifest API was split into several modules. When writing tests that
import endpoint functions or shared helpers, import from the module they
actually live in, not from `gco.services.manifest_api`:

| Symbol | Import from |
|--------|-------------|
| Endpoint functions (`submit_manifests`, `list_jobs`, `delete_job`, etc.) | `gco.services.api_routes.{manifests,jobs,queue,templates,webhooks}` |
| Pydantic models (`ManifestSubmissionAPIRequest`, `BulkDeleteRequest`, `ResourceIdentifier`, `JobStatus`, `WebhookEvent`, etc.) | `gco.services.api_shared` |
| Helper parsers (`_parse_job_to_dict`, `_parse_pod_to_dict`, `_parse_event_to_dict`, `_apply_template_parameters`, `_check_namespace`, `_check_processor`) | `gco.services.api_shared` |
| App itself, lifecycle, health probes (`app`, `lifespan`, `create_app`, `health_check`, `kubernetes_readiness_check`, `global_exception_handler`, `get_service_status`, `DEFAULT_MAX_REQUEST_BODY_BYTES`) | `gco.services.manifest_api` |

`manifest_api.py` no longer re-exports the moved symbols — importing from
the wrong module will now fail at collection time instead of silently
masking drift.

## Hermetic Environment Variables

Several services read configuration from `os.environ` at module import
time (`queue_processor.py`, `manifest_processor.py`). Tests that cover
these services must not leak env vars to later tests. Two patterns
handle this:

1. **Use `monkeypatch.setenv` / `monkeypatch.delenv`** — pytest cleans
   up automatically between tests.
2. **Autouse scrub fixture** — for files that reload modules via
   `importlib.reload`, declare a module-level autouse fixture that
   calls `monkeypatch.delenv(name, raising=False)` on every variable
   the module reads. See `tests/test_queue_processor.py::_scrub_qp_env`
   for the canonical pattern.

Never set env vars via `os.environ["X"] = "..."` directly in a test body
without a tear-down — it will leak into unrelated tests that run later
in the same session.

## Common Issues

### Import Errors

If you see import errors, ensure you're running tests from the project root:

```bash
python -m pytest tests/test_file.py
```

### Async Test Warnings

The project uses `pytest-asyncio`. Async tests are automatically detected.

### Module Caching

FastAPI apps can be cached between tests. Use fresh imports within test functions:

```python
def test_something():
    with patch(...):
        from gco.services.manifest_api import app
        with TestClient(app) as client:
            ...
```
