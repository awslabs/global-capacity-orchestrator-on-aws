"""
Tests for gco/services/auth_middleware.py.

Exercises the FastAPI authentication middleware that validates the
x-gco-auth-token header against tokens cached from Secrets Manager,
including the unauthenticated-path allowlist (/healthz, /readyz,
/metrics, /api/v1/health), the explicit GCO_DEV_MODE bypass,
AWSCURRENT/AWSPENDING dual-token rotation, and the stale-cache
fallback that keeps old tokens valid when Secrets Manager is briefly
unavailable. Uses autouse fixtures to reset the module-level token
cache and client between tests so cached state doesn't leak.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from gco.services.auth_middleware import (
    UNAUTHENTICATED_PATHS,
    AuthenticationMiddleware,
    clear_token_cache,
    get_secret_token,
    get_secrets_client,
    get_valid_tokens,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset module-level cache before each test."""
    import gco.services.auth_middleware as auth_module

    auth_module._cached_tokens = set()
    auth_module._cache_timestamp = 0
    auth_module._secrets_client = None
    yield
    auth_module._cached_tokens = set()
    auth_module._cache_timestamp = 0
    auth_module._secrets_client = None


@pytest.fixture
def app_with_middleware():
    """Create FastAPI app with authentication middleware."""
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware)

    # Add exception handler for HTTPException
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):  # nosemgrep: useless-inner-function
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/api/v1/health")
    async def get_health():  # nosemgrep: useless-inner-function
        return {"status": "healthy"}

    @app.get("/healthz")
    async def healthz():  # nosemgrep: useless-inner-function
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():  # nosemgrep: useless-inner-function
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics():  # nosemgrep: useless-inner-function
        return {"metrics": []}

    @app.post("/api/v1/manifests")
    async def submit_manifest():  # nosemgrep: useless-inner-function
        return {"success": True}

    return app


class TestUnauthenticatedPaths:
    """Tests for unauthenticated path handling."""

    def test_healthz_bypasses_auth(self, app_with_middleware):
        """Test /healthz endpoint bypasses authentication."""
        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value={"secret-token"}),
        ):
            client = TestClient(app_with_middleware)
            response = client.get("/healthz")
            assert response.status_code == 200

    def test_readyz_bypasses_auth(self, app_with_middleware):
        """Test /readyz endpoint bypasses authentication."""
        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value={"secret-token"}),
        ):
            client = TestClient(app_with_middleware)
            response = client.get("/readyz")
            assert response.status_code == 200

    def test_metrics_bypasses_auth(self, app_with_middleware):
        """Test /metrics endpoint bypasses authentication."""
        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value={"secret-token"}),
        ):
            client = TestClient(app_with_middleware)
            response = client.get("/metrics")
            assert response.status_code == 200

    def test_unauthenticated_paths_constant(self):
        """Test UNAUTHENTICATED_PATHS contains expected paths."""
        assert "/healthz" in UNAUTHENTICATED_PATHS
        assert "/readyz" in UNAUTHENTICATED_PATHS
        assert "/metrics" in UNAUTHENTICATED_PATHS
        assert "/api/v1/health" in UNAUTHENTICATED_PATHS

    def test_api_health_bypasses_auth(self, app_with_middleware):
        """Test /api/v1/health bypasses authentication for GA health checks."""
        with (
            patch.dict(
                os.environ,
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
        ):
            client = TestClient(app_with_middleware)
            response = client.get("/api/v1/health")
            assert response.status_code == 200


class TestAuthenticatedPaths:
    """Tests for authenticated path handling."""

    def test_valid_token_allows_request(self, app_with_middleware):
        """Test valid token allows request through."""
        with patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}):
            client = TestClient(app_with_middleware, raise_server_exceptions=False)
            response = client.post("/api/v1/manifests", headers={"x-gco-auth-token": "valid-token"})
            assert response.status_code == 200

    def test_invalid_token_raises_exception(self, app_with_middleware):
        """Test invalid token raises HTTPException."""
        with patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}):
            client = TestClient(app_with_middleware, raise_server_exceptions=True)
            with pytest.raises(HTTPException) as exc_info:
                client.post("/api/v1/manifests", headers={"x-gco-auth-token": "wrong-token"})
            assert exc_info.value.status_code == 403

    def test_missing_token_raises_exception(self, app_with_middleware):
        """Test missing token raises HTTPException."""
        with patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}):
            client = TestClient(app_with_middleware, raise_server_exceptions=True)
            with pytest.raises(HTTPException) as exc_info:
                client.post("/api/v1/manifests")
            assert exc_info.value.status_code == 403

    def test_empty_token_raises_exception(self, app_with_middleware):
        """Test empty token raises HTTPException."""
        with patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}):
            client = TestClient(app_with_middleware, raise_server_exceptions=True)
            with pytest.raises(HTTPException) as exc_info:
                client.post("/api/v1/manifests", headers={"x-gco-auth-token": ""})
            assert exc_info.value.status_code == 403

    def test_pending_token_allowed_during_rotation(self, app_with_middleware):
        """Test AWSPENDING token is accepted during rotation."""
        with patch(
            "gco.services.auth_middleware.get_valid_tokens",
            return_value={"current-token", "pending-token"},
        ):
            client = TestClient(app_with_middleware, raise_server_exceptions=False)
            # Both tokens should work
            response1 = client.post(
                "/api/v1/manifests", headers={"x-gco-auth-token": "current-token"}
            )
            response2 = client.post(
                "/api/v1/manifests", headers={"x-gco-auth-token": "pending-token"}
            )
            assert response1.status_code == 200
            assert response2.status_code == 200


class TestDevelopmentMode:
    """Tests for development mode (explicit GCO_DEV_MODE flag required)."""

    def test_dev_mode_allows_requests_when_no_secret(self, app_with_middleware):
        """Test requests allowed when GCO_DEV_MODE=true and AUTH_SECRET_ARN not set."""
        with (
            patch.dict("os.environ", {"GCO_DEV_MODE": "true"}, clear=True),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value=set()),
        ):
            client = TestClient(app_with_middleware)
            response = client.post("/api/v1/manifests")
            assert response.status_code == 200

    def test_no_secret_no_dev_mode_returns_503(self, app_with_middleware):
        """Test requests denied when AUTH_SECRET_ARN not set and GCO_DEV_MODE not enabled."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value=set()),
        ):
            client = TestClient(app_with_middleware, raise_server_exceptions=True)
            with pytest.raises(HTTPException) as exc_info:
                client.post("/api/v1/manifests")
            assert exc_info.value.status_code == 503

    def test_warning_logged_when_dev_mode(self, app_with_middleware):
        """Test warning is logged when dev mode bypasses auth."""
        with (
            patch.dict("os.environ", {"GCO_DEV_MODE": "true"}, clear=True),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value=set()),
            patch("gco.services.auth_middleware.logger") as mock_logger,
        ):
            client = TestClient(app_with_middleware)
            client.post("/api/v1/manifests")
            # Warning should be logged about bypassed auth
            mock_logger.warning.assert_called()


class TestGetSecretToken:
    """Tests for get_secret_token and get_valid_tokens functions."""

    def test_returns_none_when_no_arn(self):
        """Test returns None when AUTH_SECRET_ARN not set."""
        with patch.dict("os.environ", {}, clear=True):
            result = get_secret_token()
            assert result is None

    def test_get_valid_tokens_returns_empty_set_when_no_arn(self):
        """Test get_valid_tokens returns empty set when AUTH_SECRET_ARN not set."""
        with patch.dict("os.environ", {}, clear=True):
            result = get_valid_tokens()
            assert result == set()

    def test_fetches_secret_from_secrets_manager(self):
        """Test fetches secret from Secrets Manager."""
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": '{"token": "my-secret-token"}'
        }
        # Mock ResourceNotFoundException for AWSPENDING
        mock_secrets.exceptions.ResourceNotFoundException = Exception

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            result = get_valid_tokens()
            assert "my-secret-token" in result

    def test_fetches_both_current_and_pending(self):
        """Test fetches both AWSCURRENT and AWSPENDING tokens during rotation."""
        mock_secrets = MagicMock()

        def mock_get_secret(SecretId, VersionStage):
            if VersionStage == "AWSCURRENT":
                return {"SecretString": '{"token": "current-token"}'}
            elif VersionStage == "AWSPENDING":
                return {"SecretString": '{"token": "pending-token"}'}

        mock_secrets.get_secret_value.side_effect = mock_get_secret
        mock_secrets.exceptions.ResourceNotFoundException = Exception

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            result = get_valid_tokens()
            assert "current-token" in result
            assert "pending-token" in result
            assert len(result) == 2

    def test_caches_tokens_with_ttl(self):
        """Test tokens are cached and reused within TTL."""
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {"SecretString": '{"token": "cached-token"}'}
        mock_secrets.exceptions.ResourceNotFoundException = Exception

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            # First call
            result1 = get_valid_tokens()
            # Second call should use cache
            result2 = get_valid_tokens()

            assert result1 == result2
            # Should only call Secrets Manager once (for AWSCURRENT, AWSPENDING may fail)
            assert mock_secrets.get_secret_value.call_count <= 2

    def test_clear_token_cache(self):
        """Test clear_token_cache resets the cache."""
        import gco.services.auth_middleware as auth_module

        auth_module._cached_tokens = {"old-token"}
        auth_module._cache_timestamp = 999999

        clear_token_cache()

        assert auth_module._cached_tokens == set()
        assert auth_module._cache_timestamp == 0


class TestGetSecretsClient:
    """Tests for get_secrets_client function."""

    def test_creates_boto3_client(self):
        """Test creates boto3 Secrets Manager client with region from ARN."""
        # Reset the cached client
        import gco.services.auth_middleware as auth_module

        auth_module._secrets_client = None

        with (
            patch("gco.services.auth_middleware.boto3") as mock_boto3,
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:123456:secret:test"},
            ),
        ):
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            result = get_secrets_client()

            mock_boto3.client.assert_called_once_with("secretsmanager", region_name="us-east-2")
            assert result == mock_client

    def test_creates_client_with_no_region_when_arn_missing(self):
        """Test creates boto3 client with None region when ARN not set."""
        import gco.services.auth_middleware as auth_module

        auth_module._secrets_client = None

        with (
            patch("gco.services.auth_middleware.boto3") as mock_boto3,
            patch.dict("os.environ", {}, clear=True),
        ):
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            result = get_secrets_client()

            mock_boto3.client.assert_called_once_with("secretsmanager", region_name=None)
            assert result == mock_client

    def test_reuses_existing_client(self):
        """Test reuses existing client on subsequent calls."""
        import gco.services.auth_middleware as auth_module

        auth_module._secrets_client = None

        with patch("gco.services.auth_middleware.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            # First call
            result1 = get_secrets_client()
            # Second call
            result2 = get_secrets_client()

            # Should only create client once
            assert mock_boto3.client.call_count == 1
            assert result1 == result2


class TestMiddlewareLogging:
    """Tests for middleware logging behavior."""

    def test_logs_invalid_token_attempt(self, app_with_middleware):
        """Test logs warning on invalid token attempt."""
        with (
            patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}),
            patch("gco.services.auth_middleware.logger") as mock_logger,
        ):
            client = TestClient(app_with_middleware, raise_server_exceptions=False)
            client.post("/api/v1/manifests", headers={"x-gco-auth-token": "invalid"})
            mock_logger.warning.assert_called()


class TestHeaderCaseSensitivity:
    """Tests for header case handling."""

    def test_lowercase_header_works(self, app_with_middleware):
        """Test lowercase header name works."""
        with patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}):
            client = TestClient(app_with_middleware)
            response = client.post("/api/v1/manifests", headers={"x-gco-auth-token": "valid-token"})
            assert response.status_code == 200

    def test_mixed_case_header_works(self, app_with_middleware):
        """Test mixed case header name works (HTTP headers are case-insensitive)."""
        with patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid-token"}):
            client = TestClient(app_with_middleware)
            response = client.post("/api/v1/manifests", headers={"X-GCO-Auth-Token": "valid-token"})
            assert response.status_code == 200


class TestSecretRotation:
    """Tests for secret rotation support."""

    def test_accepts_current_token(self, app_with_middleware):
        """Test accepts AWSCURRENT token."""
        with patch(
            "gco.services.auth_middleware.get_valid_tokens",
            return_value={"current-token", "pending-token"},
        ):
            client = TestClient(app_with_middleware)
            response = client.post(
                "/api/v1/manifests", headers={"x-gco-auth-token": "current-token"}
            )
            assert response.status_code == 200

    def test_accepts_pending_token(self, app_with_middleware):
        """Test accepts AWSPENDING token during rotation."""
        with patch(
            "gco.services.auth_middleware.get_valid_tokens",
            return_value={"current-token", "pending-token"},
        ):
            client = TestClient(app_with_middleware)
            response = client.post(
                "/api/v1/manifests", headers={"x-gco-auth-token": "pending-token"}
            )
            assert response.status_code == 200

    def test_rejects_old_token_after_rotation(self, app_with_middleware):
        """Test rejects old token after rotation completes."""
        with patch(
            "gco.services.auth_middleware.get_valid_tokens",
            return_value={"new-current-token"},  # Old token no longer valid
        ):
            client = TestClient(app_with_middleware, raise_server_exceptions=True)
            with pytest.raises(HTTPException) as exc_info:
                client.post("/api/v1/manifests", headers={"x-gco-auth-token": "old-token"})
            assert exc_info.value.status_code == 403


# =============================================================================
# Additional coverage tests for gco/services/auth_middleware.py
# =============================================================================


@pytest.fixture(autouse=True)
def reset_auth_cache_extended():
    """Reset auth middleware cache before each test."""
    import gco.services.auth_middleware as auth_module

    auth_module._cached_tokens = set()
    auth_module._cache_timestamp = 0
    auth_module._secrets_client = None
    yield
    auth_module._cached_tokens = set()
    auth_module._cache_timestamp = 0
    auth_module._secrets_client = None


class TestAuthMiddlewareErrorHandlingExtended:
    """Extended tests for auth middleware error handling paths."""

    def test_refresh_cache_awscurrent_exception(self):
        """Test _refresh_cache handles AWSCURRENT fetch exception."""
        from gco.services.auth_middleware import _refresh_cache, get_valid_tokens

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.side_effect = Exception("Connection error")
        mock_secrets.exceptions.ResourceNotFoundException = Exception

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            _refresh_cache()
            tokens = get_valid_tokens()
            assert tokens == set()

    def test_refresh_cache_awspending_generic_exception(self):
        """Test _refresh_cache handles AWSPENDING generic exception."""
        from gco.services.auth_middleware import _refresh_cache

        mock_secrets = MagicMock()

        def mock_get_secret(SecretId, VersionStage):
            if VersionStage == "AWSCURRENT":
                return {"SecretString": '{"token": "current-token"}'}
            elif VersionStage == "AWSPENDING":
                raise ValueError("Some other error")

        mock_secrets.get_secret_value.side_effect = mock_get_secret
        mock_secrets.exceptions.ResourceNotFoundException = KeyError

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            _refresh_cache()
            import gco.services.auth_middleware as auth_module

            assert "current-token" in auth_module._cached_tokens

    def test_refresh_cache_outer_exception(self):
        """Test _refresh_cache handles outer exception gracefully."""
        from gco.services.auth_middleware import _refresh_cache

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch(
                "gco.services.auth_middleware.get_secrets_client",
                side_effect=Exception("Client creation failed"),
            ),
        ):
            _refresh_cache()

    def test_middleware_503_when_secret_configured_but_load_fails(self):
        """Test middleware returns 503 when secret is configured but can't be loaded."""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        from gco.services.auth_middleware import AuthenticationMiddleware

        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)

        @app.exception_handler(HTTPException)
        async def http_exception_handler(request, exc):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        @app.post("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_valid_tokens", return_value=set()),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/api/v1/test")
            assert response.status_code in [500, 503]

    def test_middleware_logs_client_ip_unknown(self):
        """Test middleware handles missing client IP gracefully."""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        from gco.services.auth_middleware import AuthenticationMiddleware

        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)

        @app.exception_handler(HTTPException)
        async def http_exception_handler(request, exc):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        @app.post("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        with (
            patch("gco.services.auth_middleware.get_valid_tokens", return_value={"valid"}),
            patch("gco.services.auth_middleware.logger") as mock_logger,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/api/v1/test", headers={"x-gco-auth-token": "invalid-token"})
            assert response.status_code in [403, 500]
            mock_logger.warning.assert_called()


# =============================================================================
# Stale cache fallback tests
# =============================================================================


@pytest.fixture(autouse=False)
def reset_auth_cache_stale():
    """Reset auth middleware cache before each stale-cache test."""
    import gco.services.auth_middleware as auth_module

    auth_module._cached_tokens = set()
    auth_module._cache_timestamp = 0
    auth_module._secrets_client = None
    yield
    auth_module._cached_tokens = set()
    auth_module._cache_timestamp = 0
    auth_module._secrets_client = None


class TestAuthMiddlewareStaleCacheFallback:
    """Tests for stale cache fallback when Secrets Manager is unavailable."""

    def test_keeps_stale_tokens_on_refresh_failure(self, reset_auth_cache_stale):
        """When SM fails during refresh, stale tokens are kept instead of clearing."""
        import time

        import gco.services.auth_middleware as auth_module
        from gco.services.auth_middleware import _refresh_cache

        # Seed the cache with a valid token
        auth_module._cached_tokens = {"stale-token"}
        auth_module._cache_timestamp = time.time() - 400  # expired

        # SM fails completely
        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch(
                "gco.services.auth_middleware.get_secrets_client",
                side_effect=Exception("SM unavailable"),
            ),
        ):
            _refresh_cache()
            # Stale token should still be there
            assert "stale-token" in auth_module._cached_tokens
            # Timestamp should be extended so we don't retry immediately
            assert auth_module._cache_timestamp > time.time() - 10

    def test_keeps_stale_tokens_when_awscurrent_fails(self, reset_auth_cache_stale):
        """When AWSCURRENT fetch fails but we have stale tokens, keep them."""
        import time

        import gco.services.auth_middleware as auth_module
        from gco.services.auth_middleware import _refresh_cache

        auth_module._cached_tokens = {"old-token"}
        auth_module._cache_timestamp = time.time() - 400

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.side_effect = Exception("Throttled")
        mock_secrets.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            _refresh_cache()
            assert "old-token" in auth_module._cached_tokens

    def test_replaces_tokens_on_successful_refresh(self, reset_auth_cache_stale):
        """On successful refresh, old tokens are replaced with new ones."""
        import time

        import gco.services.auth_middleware as auth_module
        from gco.services.auth_middleware import _refresh_cache

        auth_module._cached_tokens = {"old-token"}
        auth_module._cache_timestamp = time.time() - 400

        mock_secrets = MagicMock()

        def mock_get(SecretId, VersionStage):
            if VersionStage == "AWSCURRENT":
                return {"SecretString": '{"token": "new-token"}'}
            raise mock_secrets.exceptions.ResourceNotFoundException()

        mock_secrets.get_secret_value.side_effect = mock_get
        mock_secrets.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )

        with (
            patch.dict(
                "os.environ",
                {"AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"},
            ),
            patch("gco.services.auth_middleware.get_secrets_client", return_value=mock_secrets),
        ):
            _refresh_cache()
            assert "new-token" in auth_module._cached_tokens
            assert "old-token" not in auth_module._cached_tokens
