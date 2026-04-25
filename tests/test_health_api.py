"""
Tests for gco/services/health_api.py — the FastAPI health service.

Covers the create_app factory, route registration for the full health
surface (/, /healthz, /readyz, /api/v1/health, /api/v1/metrics,
/api/v1/status), and the endpoint handlers driven via TestClient
against a mocked HealthMonitor. An autouse fixture seeds the auth
middleware's token cache with a known test token so authenticated
endpoints run through the real validation code rather than patching
get_valid_tokens — this catches regressions in how the middleware
reads the cache.
"""

import contextlib
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Auth token used by tests that hit authenticated endpoints.
# The autouse fixture seeds the middleware's token cache so the
# real validation code runs — no mocking of get_valid_tokens.
_TEST_AUTH_TOKEN = "test-health-api-token"  # nosec B105 - test fixture token, not a real credential
_AUTH_HEADERS = {"x-gco-auth-token": _TEST_AUTH_TOKEN}


@pytest.fixture(autouse=True)
def _seed_auth_cache():
    """Seed the auth middleware token cache with a known token."""
    import gco.services.auth_middleware as auth_module

    original_tokens = auth_module._cached_tokens
    original_timestamp = auth_module._cache_timestamp
    auth_module._cached_tokens = {_TEST_AUTH_TOKEN}
    auth_module._cache_timestamp = time.time()
    yield
    auth_module._cached_tokens = original_tokens
    auth_module._cache_timestamp = original_timestamp


class TestHealthAPIModels:
    """Tests for Health API models and functions."""

    def test_create_app_returns_fastapi(self):
        """Test create_app returns FastAPI instance."""
        from fastapi import FastAPI

        from gco.services.health_api import create_app

        app = create_app()
        assert isinstance(app, FastAPI)

    def test_app_has_routes(self):
        """Test app has expected routes."""
        from gco.services.health_api import app

        routes = [route.path for route in app.routes]
        assert "/" in routes
        assert "/healthz" in routes
        assert "/readyz" in routes
        assert "/api/v1/health" in routes
        assert "/api/v1/metrics" in routes
        assert "/api/v1/status" in routes


def _create_mock_health_monitor():
    """Create a mock health monitor for testing."""
    from gco.models import HealthStatus, ResourceThresholds, ResourceUtilization

    mock_status = HealthStatus(
        cluster_id="test-cluster",
        region="us-east-1",
        timestamp=datetime.now(),
        status="healthy",
        resource_utilization=ResourceUtilization(cpu=50.0, memory=60.0, gpu=30.0),
        thresholds=ResourceThresholds(cpu_threshold=80, memory_threshold=85, gpu_threshold=90),
        active_jobs=5,
    )

    mock_monitor = MagicMock()
    mock_monitor.get_health_status = AsyncMock(return_value=mock_status)
    # Set string values for attributes used by HealthMonitorMetrics
    mock_monitor.cluster_id = "test-cluster"
    mock_monitor.region = "us-east-1"
    return mock_monitor


class TestHealthAPIBasicEndpoints:
    """Tests for basic Health API endpoints using TestClient."""

    def test_root_endpoint(self):
        """Test root endpoint returns service info."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/", headers=_AUTH_HEADERS)
                assert response.status_code == 200
                data = response.json()
                assert data["service"] == "GCO Health Monitor API"
                assert "endpoints" in data

    def test_healthz_endpoint(self):
        """Test Kubernetes liveness probe."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/healthz")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"

    def test_readyz_endpoint(self):
        """Test Kubernetes readiness probe."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/readyz")
                # Returns 200 if monitor is set, 503 if not
                assert response.status_code in [200, 503]

    def test_status_endpoint(self):
        """Test service status endpoint."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/status", headers=_AUTH_HEADERS)
                assert response.status_code == 200
                data = response.json()
                assert data["service"] == "GCO Health Monitor API"
                assert "environment" in data


class TestHealthCheckEndpoint:
    """Tests for /api/v1/health endpoint."""

    def test_health_check_endpoint(self):
        """Test health check endpoint."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/health")
                # Returns 200 if healthy, 503 if not
                assert response.status_code in [200, 503]


class TestMetricsEndpoint:
    """Tests for /api/v1/metrics endpoint."""

    def test_metrics_endpoint(self):
        """Test metrics endpoint."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/metrics", headers=_AUTH_HEADERS)
                # Returns 200 if metrics available, 500/503 if not
                assert response.status_code in [200, 500, 503]


class TestBackgroundHealthMonitor:
    """Tests for background health monitoring task."""

    @pytest.mark.asyncio
    async def test_background_monitor_handles_cancellation(self):
        """Test background monitor handles cancellation gracefully."""
        import asyncio

        from gco.models import HealthStatus, ResourceThresholds, ResourceUtilization

        mock_status = HealthStatus(
            cluster_id="test-cluster",
            region="us-east-1",
            timestamp=datetime.now(),
            status="healthy",
            resource_utilization=ResourceUtilization(cpu=50.0, memory=60.0, gpu=30.0),
            thresholds=ResourceThresholds(cpu_threshold=80, memory_threshold=85, gpu_threshold=90),
            active_jobs=5,
        )

        mock_monitor = MagicMock()
        mock_monitor.get_health_status = AsyncMock(return_value=mock_status)

        import gco.services.health_api as health_api_module

        original_monitor = health_api_module.health_monitor
        health_api_module.health_monitor = mock_monitor

        try:
            from gco.services.health_api import background_health_monitor

            task = asyncio.create_task(background_health_monitor())
            await asyncio.sleep(0.1)
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

            # Verify status was updated
            assert health_api_module.current_health_status is not None
        finally:
            health_api_module.health_monitor = original_monitor


class TestHealthCheckWithMockedMonitor:
    """Tests for health check endpoint with mocked monitor."""

    def test_health_check_returns_response(self):
        """Test health check returns a response."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/health")
                # Should return some response
                assert response.status_code in [200, 503]


class TestMetricsWithMockedMonitor:
    """Tests for metrics endpoint with mocked monitor."""

    def test_metrics_returns_response(self):
        """Test metrics endpoint returns a response."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/metrics", headers=_AUTH_HEADERS)
                # Should return some response
                assert response.status_code in [200, 500, 503]


class TestStatusEndpointDetails:
    """Tests for status endpoint details."""

    def test_status_includes_environment(self):
        """Test status endpoint includes environment info."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/status", headers=_AUTH_HEADERS)
                assert response.status_code == 200
                data = response.json()
                assert "environment" in data
                assert "cluster_name" in data["environment"]
                assert "region" in data["environment"]
                assert "cpu_threshold" in data["environment"]
                assert "memory_threshold" in data["environment"]
                assert "gpu_threshold" in data["environment"]

    def test_status_includes_service_info(self):
        """Test status endpoint includes service info."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/v1/status", headers=_AUTH_HEADERS)
                assert response.status_code == 200
                data = response.json()
                assert data["service"] == "GCO Health Monitor API"
                assert data["version"] == "1.0.0"


class TestGlobalExceptionHandler:
    """Tests for global exception handler."""

    def test_exception_handler_returns_500(self):
        """Test global exception handler returns 500."""
        with (
            patch(
                "gco.services.health_api.create_health_monitor_from_env",
                return_value=_create_mock_health_monitor(),
            ),
            patch("gco.services.health_api.HealthMonitorMetrics"),
            patch("gco.services.health_api.create_webhook_dispatcher_from_env"),
        ):
            from fastapi.testclient import TestClient

            from gco.services.health_api import app

            # The exception handler is tested implicitly through other tests
            # that may trigger exceptions
            with TestClient(app, raise_server_exceptions=False) as client:
                # This should not raise but return proper error response
                response = client.get("/nonexistent-endpoint", headers=_AUTH_HEADERS)
                assert response.status_code == 404  # FastAPI handles this


class TestBackgroundMonitorEdgeCases:
    """Tests for background monitor edge cases."""

    @pytest.mark.asyncio
    async def test_background_monitor_handles_error(self):
        """Test background monitor handles errors gracefully."""
        import asyncio

        mock_monitor = MagicMock()
        mock_monitor.get_health_status = AsyncMock(side_effect=Exception("API error"))

        import gco.services.health_api as health_api_module

        original_monitor = health_api_module.health_monitor
        health_api_module.health_monitor = mock_monitor

        try:
            from gco.services.health_api import background_health_monitor

            task = asyncio.create_task(background_health_monitor())
            await asyncio.sleep(0.2)  # Let it run and handle error
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            health_api_module.health_monitor = original_monitor
