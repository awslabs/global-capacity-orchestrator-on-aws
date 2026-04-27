"""
Extended tests for lambda/proxy-shared/proxy_utils.py.

Covers the forward_request paths the base proxy suite doesn't reach:
urllib3 TimeoutError → 504 response, MaxRetryError → 503 response,
unknown exceptions → 500 without retry, retryable statuses that
persist through every attempt, hop-by-hop header stripping in
_build_success_response, and body encoding (None vs string). Also
exercises get_secret_token's thread-safe caching and the stale-cache
fallback when Secrets Manager becomes unavailable.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import urllib3

from tests._lambda_imports import load_lambda_module


@pytest.fixture
def proxy_module():
    """Import proxy_utils with mocked boto3 and env.

    Loaded via :func:`load_lambda_module` — see
    ``tests/_lambda_imports.py`` for the rationale.
    """
    with (
        patch("boto3.client") as mock_client,
        patch.dict(
            "os.environ",
            {
                "SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test",
                "SECRET_CACHE_TTL_SECONDS": "300",
                "PROXY_MAX_RETRIES": "3",
                "PROXY_RETRY_BACKOFF_BASE": "0.001",
            },
        ),
    ):
        proxy_utils = load_lambda_module("proxy-shared", "proxy_utils")

        proxy_utils._cached_secret = None
        proxy_utils._cache_timestamp = 0.0

        mock_sm = mock_client.return_value
        mock_sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"token": "test-token"})
        }
        yield proxy_utils, mock_sm


class TestForwardRequestTimeout:
    """Tests for forward_request timeout handling."""

    def test_timeout_returns_504(self, proxy_module):
        """TimeoutError should result in 504 after retries."""
        pu, _ = proxy_module

        mock_http = MagicMock()
        mock_http.request.side_effect = urllib3.exceptions.TimeoutError("timed out")

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None, timeout=1.0)

        assert result["statusCode"] == 504
        body = json.loads(result["body"])
        assert "Gateway timeout" in body["error"]

    def test_connection_error_returns_503(self, proxy_module):
        """MaxRetryError should result in 503 after retries."""
        pu, _ = proxy_module

        mock_http = MagicMock()
        mock_http.request.side_effect = urllib3.exceptions.MaxRetryError(
            pool=None, url="http://example.com", reason="Connection refused"
        )

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None)

        assert result["statusCode"] == 503
        body = json.loads(result["body"])
        assert "Service unavailable" in body["error"]


class TestForwardRequestUnknownException:
    """Tests for forward_request unknown exception handling."""

    def test_unknown_exception_returns_500_no_retry(self, proxy_module):
        """Unknown exceptions should return 500 immediately without retry."""
        pu, _ = proxy_module

        mock_http = MagicMock()
        mock_http.request.side_effect = RuntimeError("Unexpected crash")

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "Internal server error" in body["error"]
        # Should only be called once (no retry)
        assert mock_http.request.call_count == 1


class TestForwardRequestRetryableStatus:
    """Tests for retryable status code handling."""

    def test_502_retries_then_returns_last_response(self, proxy_module):
        """502 should retry and return the last response on exhaustion."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 502
        mock_response.headers = {}
        mock_response.data = b'{"error": "Bad Gateway"}'

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None)

        assert result["statusCode"] == 502
        assert mock_http.request.call_count == 3  # 3 retries

    def test_429_retries_then_succeeds(self, proxy_module):
        """429 should retry and succeed if next attempt returns 200."""
        pu, _ = proxy_module

        fail_response = MagicMock()
        fail_response.status = 429

        ok_response = MagicMock()
        ok_response.status = 200
        ok_response.headers = {"Content-Type": "application/json"}
        ok_response.data = b'{"ok": true}'

        mock_http = MagicMock()
        mock_http.request.side_effect = [fail_response, ok_response]

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None)

        assert result["statusCode"] == 200
        assert mock_http.request.call_count == 2

    def test_504_retries(self, proxy_module):
        """504 should be retried."""
        pu, _ = proxy_module

        fail_response = MagicMock()
        fail_response.status = 504

        ok_response = MagicMock()
        ok_response.status = 200
        ok_response.headers = {}
        ok_response.data = b'{"ok": true}'

        mock_http = MagicMock()
        mock_http.request.side_effect = [fail_response, ok_response]

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None)

        assert result["statusCode"] == 200

    def test_non_retryable_status_returned_immediately(self, proxy_module):
        """Non-retryable status codes (400, 404, etc.) should return immediately."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.data = b'{"error": "Not found"}'

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "GET", {}, None)

        assert result["statusCode"] == 404
        assert mock_http.request.call_count == 1


class TestBuildSuccessResponse:
    """Tests for _build_success_response hop-by-hop header removal."""

    def test_removes_hop_by_hop_headers(self, proxy_module):
        """Hop-by-hop headers should be stripped from response."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=5",
            "Transfer-Encoding": "chunked",
            "X-Custom": "preserved",
        }
        mock_response.data = b'{"ok": true}'

        result = pu._build_success_response(mock_response)

        assert result["statusCode"] == 200
        assert "Connection" not in result["headers"]
        assert "connection" not in result["headers"]
        assert "Keep-Alive" not in result["headers"]
        assert "Transfer-Encoding" not in result["headers"]
        assert result["headers"]["X-Custom"] == "preserved"
        assert result["headers"]["Content-Type"] == "application/json"

    def test_removes_lowercase_hop_by_hop(self, proxy_module):
        """Lowercase hop-by-hop headers should also be removed."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {
            "te": "trailers",
            "trailer": "Expires",
            "upgrade": "websocket",
        }
        mock_response.data = b"{}"

        result = pu._build_success_response(mock_response)

        assert "te" not in result["headers"]
        assert "trailer" not in result["headers"]
        assert "upgrade" not in result["headers"]


class TestForwardRequestBodyEncoding:
    """Tests for body encoding in forward_request."""

    def test_none_body_sends_none(self, proxy_module):
        """None body should send None to urllib3."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.data = b"{}"

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            pu.forward_request("http://example.com", "GET", {}, None)

        call_kwargs = mock_http.request.call_args[1]
        assert call_kwargs["body"] is None

    def test_string_body_encoded_to_utf8(self, proxy_module):
        """String body should be encoded to UTF-8 bytes."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.data = b"{}"

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            pu.forward_request("http://example.com", "POST", {}, '{"key": "value"}')

        call_kwargs = mock_http.request.call_args[1]
        assert call_kwargs["body"] == b'{"key": "value"}'

    def test_empty_string_body_sends_none(self, proxy_module):
        """Empty string body should send None (falsy)."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.data = b"{}"

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            pu.forward_request("http://example.com", "POST", {}, "")

        call_kwargs = mock_http.request.call_args[1]
        assert call_kwargs["body"] is None


class TestGetSecretTokenCaching:
    """Tests for get_secret_token caching behavior."""

    def test_caches_within_ttl(self, proxy_module):
        """Second call within TTL should not hit Secrets Manager."""
        pu, mock_sm = proxy_module

        pu.get_secret_token()
        mock_sm.get_secret_value.reset_mock()

        token = pu.get_secret_token()
        assert (
            token == "test-token"
        )  # nosec B105 - test assertion against fixture value, not a real credential
        mock_sm.get_secret_value.assert_not_called()

    def test_refreshes_after_ttl(self, proxy_module):
        """Call after TTL should refresh from Secrets Manager."""
        pu, mock_sm = proxy_module

        pu.get_secret_token()
        pu._cache_timestamp = time.time() - 400  # Expire cache

        mock_sm.get_secret_value.return_value = {"SecretString": json.dumps({"token": "new-token"})}

        token = pu.get_secret_token()
        assert (
            token == "new-token"
        )  # nosec B105 - test assertion against fixture value, not a real credential

    def test_stale_cache_on_sm_failure(self, proxy_module):
        """SM failure with existing cache should return stale token."""
        pu, mock_sm = proxy_module

        pu.get_secret_token()
        pu._cache_timestamp = time.time() - 400  # Expire cache

        mock_sm.get_secret_value.side_effect = Exception("SM down")

        token = pu.get_secret_token()
        assert token == "test-token"  # Stale cache

    def test_no_cache_and_sm_failure_raises(self, proxy_module):
        """SM failure with no cache should raise RuntimeError."""
        pu, mock_sm = proxy_module

        mock_sm.get_secret_value.side_effect = Exception("SM down")

        with pytest.raises(RuntimeError, match="Failed to load secret"):
            pu.get_secret_token()


class TestForwardRequestSuccess:
    """Tests for successful forward_request scenarios."""

    def test_200_with_json_body(self, proxy_module):
        """200 response should be returned with decoded body."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.data = b'{"result": "ok"}'

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request(
                "http://example.com/api",
                "POST",
                {"Accept": "application/json"},
                '{"input": "data"}',
                timeout=10.0,
            )

        assert result["statusCode"] == 200
        assert result["body"] == '{"result": "ok"}'
        assert result["headers"]["Content-Type"] == "application/json"

    def test_201_returned_immediately(self, proxy_module):
        """201 Created should be returned without retry."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.headers = {}
        mock_response.data = b'{"id": "123"}'

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            result = pu.forward_request("http://example.com/api", "POST", {}, '{"name": "test"}')

        assert result["statusCode"] == 201
        assert mock_http.request.call_count == 1

    def test_custom_timeout_passed_to_urllib3(self, proxy_module):
        """Custom timeout should be passed to urllib3."""
        pu, _ = proxy_module

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.data = b"{}"

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(pu, "_http", mock_http):
            pu.forward_request("http://example.com", "GET", {}, None, timeout=5.0)

        call_kwargs = mock_http.request.call_args[1]
        assert call_kwargs["timeout"] == 5.0
