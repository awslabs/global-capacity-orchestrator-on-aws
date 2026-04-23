"""
Shared proxy utilities for API Gateway Lambda handlers.

Provides thread-safe secret caching and HTTP forwarding with retry logic.
Used by both api-gateway-proxy and regional-api-proxy handlers.
"""

import json
import logging
import os
import threading
import time
from typing import Any

import boto3
import urllib3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe secret cache
# ---------------------------------------------------------------------------

_secret_lock = threading.Lock()
_cached_secret: str | None = None
_cache_timestamp: float = 0.0

# Cache TTL in seconds (5 minutes) — ensures rotated secrets are picked up
# without requiring a Lambda cold start
_CACHE_TTL_SECONDS = int(os.environ.get("SECRET_CACHE_TTL_SECONDS", "300"))

# Shared Secrets Manager client (module-level for connection reuse)
_secrets_client = boto3.client("secretsmanager")


def get_secret_token() -> str:
    """
    Retrieve the authentication token from AWS Secrets Manager.

    Thread-safe with TTL-based caching. The token is refreshed after
    ``_CACHE_TTL_SECONDS`` to pick up rotated secrets without requiring
    a Lambda cold start.

    Raises:
        KeyError: If SECRET_ARN environment variable is not set.
        botocore.exceptions.ClientError: If Secrets Manager access fails.
    """
    global _cached_secret, _cache_timestamp

    now = time.time()

    # Fast path — read without lock (safe because Python GIL protects reads)
    if _cached_secret is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _cached_secret

    with _secret_lock:
        # Double-check after acquiring lock
        now = time.time()
        if _cached_secret is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
            return _cached_secret

        secret_arn = os.environ["SECRET_ARN"]
        response = _secrets_client.get_secret_value(SecretId=secret_arn)
        secret_data = json.loads(response["SecretString"])
        _cached_secret = secret_data["token"]
        _cache_timestamp = now

    return _cached_secret


# ---------------------------------------------------------------------------
# HTTP forwarding with retry
# ---------------------------------------------------------------------------

# Retry configuration
_MAX_RETRIES = int(os.environ.get("PROXY_MAX_RETRIES", "3"))
_RETRY_BACKOFF_BASE = float(os.environ.get("PROXY_RETRY_BACKOFF_BASE", "0.3"))

# Retryable HTTP status codes (server errors and rate limiting)
_RETRYABLE_STATUS_CODES = {502, 503, 504, 429}

# Connection pool shared across invocations
_http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    retries=False,  # We handle retries ourselves for better control
)


def forward_request(
    target_url: str,
    http_method: str,
    headers: dict[str, str],
    body: str | None,
    timeout: float = 29.0,
) -> dict[str, Any]:
    """
    Forward an HTTP request to the target URL with retry logic.

    Implements exponential backoff for transient failures (connection errors,
    502/503/504, 429). Non-retryable errors are returned immediately.

    Args:
        target_url: Full URL to forward the request to.
        http_method: HTTP method (GET, POST, etc.).
        headers: Request headers to forward.
        body: Request body (may be None or empty).
        timeout: Per-attempt timeout in seconds (default: API Gateway's 29s).

    Returns:
        API Gateway proxy response dict with statusCode, headers, and body.
    """
    encoded_body = body.encode("utf-8") if body else None
    last_exception: Exception | None = None
    last_status: int | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = _http.request(
                http_method,
                target_url,
                headers=headers,
                body=encoded_body,
                timeout=timeout,
            )

            # Return immediately on success or non-retryable status
            if response.status not in _RETRYABLE_STATUS_CODES:
                return _build_success_response(response)

            # Retryable status — log and retry
            last_status = response.status
            logger.warning(
                "Retryable status %d on attempt %d/%d for %s %s",
                response.status,
                attempt + 1,
                _MAX_RETRIES,
                http_method,
                target_url,
            )

            # On last attempt, return whatever we got
            if attempt == _MAX_RETRIES - 1:
                return _build_success_response(response)

        except urllib3.exceptions.MaxRetryError as e:
            last_exception = e
            logger.warning(
                "Connection failed on attempt %d/%d for %s %s: %s",
                attempt + 1,
                _MAX_RETRIES,
                http_method,
                target_url,
                e,
            )
        except urllib3.exceptions.TimeoutError as e:
            last_exception = e
            logger.warning(
                "Timeout on attempt %d/%d for %s %s: %s",
                attempt + 1,
                _MAX_RETRIES,
                http_method,
                target_url,
                e,
            )
        except Exception as e:
            # Unknown error — don't retry
            logger.error("Unexpected error forwarding request: %s", e)
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Internal server error", "message": str(e)}),
            }

        # Exponential backoff: 0.3s, 0.6s, 1.2s, ...
        if attempt < _MAX_RETRIES - 1:
            backoff = _RETRY_BACKOFF_BASE * (2**attempt)
            time.sleep(backoff)

    # All retries exhausted
    if last_exception:
        status_code = 503 if isinstance(last_exception, urllib3.exceptions.MaxRetryError) else 504
        return {
            "statusCode": status_code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "error": "Service unavailable" if status_code == 503 else "Gateway timeout",
                    "message": f"Failed after {_MAX_RETRIES} attempts: {last_exception}",
                }
            ),
        }

    # Shouldn't reach here, but just in case
    return {
        "statusCode": last_status or 500,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "Request failed after retries"}),
    }


def _build_success_response(response: urllib3.BaseHTTPResponse) -> dict[str, Any]:
    """Build an API Gateway proxy response from a urllib3 response."""
    response_headers = dict(response.headers)

    # Remove hop-by-hop headers that shouldn't be forwarded
    hop_by_hop = [
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    ]
    for h in hop_by_hop:
        response_headers.pop(h, None)
        response_headers.pop(h.title(), None)

    return {
        "statusCode": response.status,
        "headers": response_headers,
        "body": response.data.decode("utf-8"),
    }


def build_target_url(endpoint: str, path: str, query_params: dict[str, str] | None) -> str:
    """Build the target URL from endpoint, path, and query parameters."""
    query_str = "&".join(f"{k}={v}" for k, v in query_params.items()) if query_params else ""
    url = f"http://{endpoint}{path}"
    if query_str:
        url += f"?{query_str}"
    return url
