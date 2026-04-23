"""
Lambda@Edge function for ALB to validate secret header.
This ensures requests came through API Gateway.
"""

import json
import os
import time
from typing import Any, cast

import boto3

secrets_client = boto3.client("secretsmanager")
_cached_tokens: set[str] = set()
_cache_timestamp: float = 0.0

# Cache TTL in seconds (5 minutes) — matches proxy_utils.py and auth_middleware.py
# Ensures rotated secrets are picked up without requiring a Lambda cold start
_CACHE_TTL_SECONDS = int(os.environ.get("SECRET_CACHE_TTL_SECONDS", "300"))


def _refresh_token_cache() -> None:
    """Refresh the token cache from Secrets Manager, loading both AWSCURRENT and AWSPENDING."""
    global _cached_tokens, _cache_timestamp

    secret_arn = os.environ["SECRET_ARN"]
    new_tokens: set[str] = set()

    # Always load AWSCURRENT
    response = secrets_client.get_secret_value(SecretId=secret_arn, VersionStage="AWSCURRENT")
    secret_data = json.loads(response["SecretString"])
    new_tokens.add(secret_data["token"])

    # Also load AWSPENDING if a rotation is in progress
    try:
        response = secrets_client.get_secret_value(SecretId=secret_arn, VersionStage="AWSPENDING")
        secret_data = json.loads(response["SecretString"])
        new_tokens.add(secret_data["token"])
    except secrets_client.exceptions.ResourceNotFoundException:
        pass  # No pending version — not in rotation, this is normal

    _cached_tokens = new_tokens
    _cache_timestamp = time.time()


def get_valid_tokens() -> set[str]:
    """Get valid authentication tokens with TTL-based caching and rotation support."""
    if not _cached_tokens or (time.time() - _cache_timestamp) >= _CACHE_TTL_SECONDS:
        _refresh_token_cache()
    return _cached_tokens


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Validate that request has the correct secret header.
    Returns 403 if header is missing or invalid.

    Accepts both AWSCURRENT and AWSPENDING tokens for zero-downtime rotation.
    """
    request = event["Records"][0]["cf"]["request"]
    headers = request["headers"]

    # Get valid tokens (includes both AWSCURRENT and AWSPENDING during rotation)
    valid_tokens = get_valid_tokens()

    # Check for auth header
    auth_header = headers.get("x-gco-auth-token", [{}])[0].get("value", "")

    if auth_header not in valid_tokens:
        # Return 403 Forbidden
        return {
            "status": "403",
            "statusDescription": "Forbidden",
            "body": json.dumps({"error": "Unauthorized - requests must come through API Gateway"}),
        }

    # Header is valid, allow request to proceed
    return cast("dict[str, Any]", request)
