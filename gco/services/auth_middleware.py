"""
Authentication middleware for validating requests from API Gateway.

This middleware ensures all requests (except health checks) contain a valid
X-GCO-Auth-Token header that matches the secret stored in AWS Secrets Manager.
This proves the request came through the authenticated API Gateway path.

Security Flow:
    1. API Gateway validates IAM credentials (SigV4)
    2. Lambda proxy adds secret token header
    3. This middleware validates the token
    4. Invalid tokens result in 403 Forbidden

Secret Rotation Support:
    During rotation, the middleware validates against both AWSCURRENT and AWSPENDING
    versions of the secret. This ensures zero-downtime during the rotation window.
    The cache is refreshed periodically to pick up rotated secrets.

Environment Variables:
    AUTH_SECRET_ARN: ARN of the Secrets Manager secret containing the token
    GCO_DEV_MODE: Set to "true" to allow unauthenticated requests when no
        secret is configured. Without this flag, missing AUTH_SECRET_ARN
        causes 503 errors (fail-closed). This prevents accidental
        unauthenticated deployments due to misconfiguration.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import boto3
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Module-level cache for secret tokens and client
_cached_tokens: set[str] = set()
_cache_timestamp: float = 0
_secrets_client = None

# Cache TTL in seconds (5 minutes) - allows picking up rotated secrets
CACHE_TTL_SECONDS = 300

# Endpoints that bypass authentication (health checks for load balancers and
# Global Accelerator). /api/v1/health is included so GA can perform HTTP
# health checks for intelligent routing without the secret header.
UNAUTHENTICATED_PATHS = frozenset(["/healthz", "/readyz", "/metrics", "/api/v1/health"])


def get_secrets_client() -> Any:
    """
    Get Secrets Manager client with lazy initialization.

    The client is configured to use the region from the AUTH_SECRET_ARN
    environment variable, which may be different from the default region.

    Returns:
        boto3 Secrets Manager client instance
    """
    global _secrets_client
    if _secrets_client is None:
        # Extract region from the secret ARN
        # Format: arn:aws:secretsmanager:REGION:ACCOUNT:secret:NAME
        secret_arn = os.environ.get("AUTH_SECRET_ARN", "")
        region = None
        if secret_arn:
            parts = secret_arn.split(":")
            if len(parts) >= 4:
                region = parts[3]
        _secrets_client = boto3.client("secretsmanager", region_name=region)
    return _secrets_client


def _is_cache_valid() -> bool:
    """Check if the cached tokens are still valid based on TTL."""
    return bool(_cached_tokens) and (time.time() - _cache_timestamp) < CACHE_TTL_SECONDS


def _refresh_cache() -> None:
    """Refresh the token cache from Secrets Manager.

    On failure, keeps the existing (stale) cache to avoid rejecting all
    requests during a transient Secrets Manager outage. The next call
    after CACHE_TTL_SECONDS will retry the refresh.
    """
    global _cached_tokens, _cache_timestamp

    secret_arn = os.environ.get("AUTH_SECRET_ARN")
    if not secret_arn:
        return

    try:
        secrets = get_secrets_client()
        new_tokens: set[str] = set()

        # Get AWSCURRENT version (always present)
        try:
            response = secrets.get_secret_value(
                SecretId=secret_arn,
                VersionStage="AWSCURRENT",
            )
            secret_data = json.loads(response["SecretString"])
            new_tokens.add(secret_data["token"])
            logger.debug("Loaded AWSCURRENT token")
        except Exception as e:
            logger.error(f"Failed to load AWSCURRENT secret: {e}")

        # Get AWSPENDING version (only present during rotation)
        try:
            response = secrets.get_secret_value(
                SecretId=secret_arn,
                VersionStage="AWSPENDING",
            )
            secret_data = json.loads(response["SecretString"])
            new_tokens.add(secret_data["token"])
            logger.debug("Loaded AWSPENDING token (rotation in progress)")
        except secrets.exceptions.ResourceNotFoundException:
            # No pending version - not in rotation, this is normal
            pass
        except Exception as e:
            # Log but don't fail - AWSPENDING is optional
            logger.debug(f"No AWSPENDING version available: {e}")

        if new_tokens:
            _cached_tokens = new_tokens
            _cache_timestamp = time.time()
            logger.info(f"Token cache refreshed with {len(new_tokens)} valid token(s)")
        elif _cached_tokens:
            # Couldn't load any new tokens but have stale ones — extend the cache
            # to avoid rejecting all traffic during a transient SM outage
            _cache_timestamp = time.time()
            logger.warning("Token refresh returned empty set, keeping stale cache")

    except Exception as e:
        logger.error(f"Failed to refresh token cache: {e}")
        if _cached_tokens:
            # Extend stale cache on total failure — better to accept slightly-old
            # tokens than to reject everything
            _cache_timestamp = time.time()
            logger.warning("Extending stale token cache due to refresh failure")


def get_valid_tokens() -> set[str]:
    """
    Retrieve valid authentication tokens from AWS Secrets Manager.

    Returns both AWSCURRENT and AWSPENDING tokens to support zero-downtime
    rotation. The tokens are cached with a TTL to minimize API calls while
    still picking up rotated secrets in a reasonable time.

    Returns:
        Set of valid token strings, or empty set if not configured
    """
    if not _is_cache_valid():
        _refresh_cache()

    return _cached_tokens


def get_secret_token() -> str | None:
    """
    Retrieve the primary authentication token from AWS Secrets Manager.

    This is a compatibility function that returns the first valid token.
    For rotation support, use get_valid_tokens() instead.

    Returns:
        The secret token string, or None if not configured
    """
    tokens = get_valid_tokens()
    return next(iter(tokens), None) if tokens else None


def clear_token_cache() -> None:
    """
    Clear the token cache, forcing a refresh on next validation.

    Useful for testing or when you know the secret has been rotated.
    """
    global _cached_tokens, _cache_timestamp
    _cached_tokens = set()
    _cache_timestamp = 0
    logger.info("Token cache cleared")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware to validate X-GCO-Auth-Token header.

    This middleware ensures all API requests came through the authenticated
    API Gateway by validating a secret token header. Health check endpoints
    are excluded to allow load balancer health probes.

    During secret rotation, both AWSCURRENT and AWSPENDING tokens are accepted
    to ensure zero-downtime rotation.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        # Startup-time configuration check — surface misconfigurations early
        secret_arn = os.environ.get("AUTH_SECRET_ARN")
        if not secret_arn:
            dev_mode = os.environ.get("GCO_DEV_MODE", "").lower() == "true"
            if dev_mode:
                logger.warning(
                    "GCO_DEV_MODE=true with no AUTH_SECRET_ARN — "
                    "authentication is bypassed. Do NOT use in production."
                )
            else:
                logger.error(
                    "AUTH_SECRET_ARN is not configured and GCO_DEV_MODE is not enabled. "
                    "All non-health-check requests will be denied with 503."
                )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """
        Process incoming request and validate authentication.

        Args:
            request: The incoming FastAPI request
            call_next: The next middleware/handler in the chain

        Returns:
            Response from the next handler if authenticated

        Raises:
            HTTPException: 403 if authentication fails
        """
        # Skip authentication for health check endpoints
        if request.url.path in UNAUTHENTICATED_PATHS:
            return await call_next(request)

        valid_tokens = get_valid_tokens()

        # No tokens available — determine whether to fail open or closed
        if not valid_tokens:
            secret_arn = os.environ.get("AUTH_SECRET_ARN")
            if not secret_arn:
                # No secret configured. Only allow requests if the operator
                # explicitly opted into dev mode. This prevents accidental
                # unauthenticated deployments due to misconfiguration.
                dev_mode = os.environ.get("GCO_DEV_MODE", "").lower() == "true"
                if dev_mode:
                    logger.warning(
                        "Authentication bypassed - GCO_DEV_MODE=true, no secret configured"
                    )
                    return await call_next(request)
                # Fail closed: no secret + no dev mode = deny
                logger.error(
                    "No AUTH_SECRET_ARN configured and GCO_DEV_MODE is not enabled. "
                    "Set AUTH_SECRET_ARN for production or GCO_DEV_MODE=true for local development."
                )
                raise HTTPException(
                    status_code=503,
                    detail="Service unavailable - authentication not configured",
                )
            # Secret configured but couldn't load - deny access
            logger.error("Failed to load authentication tokens")
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable - authentication error",
            )

        # Validate the auth header against all valid tokens
        auth_header = request.headers.get("x-gco-auth-token", "")

        if auth_header not in valid_tokens:
            client_ip = request.client.host if request.client else "unknown"
            logger.warning(f"Invalid auth token from {client_ip} for {request.url.path}")
            raise HTTPException(
                status_code=403,
                detail="Forbidden - requests must come through authenticated API Gateway",
            )

        return await call_next(request)
