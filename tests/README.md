# GCO Test Suite

This directory contains the test suite for GCO (Global Capacity Orchestrator on AWS). The tests are organized by component and functionality.

## Table of Contents

- [Running Tests](#running-tests)
- [Test Organization](#test-organization)
- [Test Files by Category](#test-files-by-category)
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

### CDK Configuration Matrix

The `scripts/test-cdk-synthesis.py` script tests that `cdk synth` succeeds across 20 configuration combinations. This runs in CI and can be run locally:

```bash
python scripts/test-cdk-synthesis.py
```

It covers region variations (us-east, us-west, eu, ap, multi-region), feature toggles (Valkey, FSx, endpoint access modes), resource threshold settings, and combined configurations. This catches hardcoded regions, missing conditional guards, and broken feature flag interactions without deploying anything.

### Fresh Install Verification

The `test:fresh-install` CI job does a clean `pip install -e .` and verifies all critical imports work — including `cdk-nag`, `aws_cdk.aws_eks_v2`, the CLI entry point, and the CDK stack classes. This catches missing or mismatched dependencies in `pyproject.toml`.

### Lambda Build Verification

The Lambda build directory (`lambda/kubectl-applier-simple-build/`) is auto-created by `StackManager` during deploy. In CI, this is validated at multiple levels:

- `integration:lambda` — verifies all Lambda handler modules import correctly
- `test:cdk-config-matrix` — builds the Lambda package in `before_script` and runs `cdk synth` against it (synth fails if the build dir is missing or incomplete)
- `test_stacks.py::TestStackManagerSyncLambdaSources` — unit tests that `_sync_lambda_sources` auto-creates the build directory when missing

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

### Configuration Files

| File | Description |
|------|-------------|
| `conftest.py` | Shared pytest fixtures and configuration |
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

The project requires a minimum of 85% test coverage. Current coverage is ~93%.

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
(boto3, kubernetes, fastapi, click) are preferred over `Any` fallbacks —
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
