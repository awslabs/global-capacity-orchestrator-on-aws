"""
API Gateway proxy Lambda for forwarding authenticated requests to Global Accelerator.

This Lambda function acts as a bridge between API Gateway (with IAM authentication)
and the Global Accelerator endpoint. It adds a secret header to prove the request
came through the authenticated API Gateway path.

Security Flow:
    1. User sends request with AWS SigV4 signature
    2. API Gateway validates IAM credentials
    3. This Lambda receives the authenticated request
    4. Lambda retrieves secret token from Secrets Manager
    5. Lambda adds X-GCO-Auth-Token header
    6. Lambda forwards request to Global Accelerator
    7. Global Accelerator routes to nearest healthy ALB
    8. Backend services validate the secret header

Environment Variables:
    GLOBAL_ACCELERATOR_ENDPOINT: DNS name of the Global Accelerator
    SECRET_ARN: ARN of the Secrets Manager secret containing the auth token
    PROXY_MAX_RETRIES: Max retry attempts for transient failures (default: 3)
    PROXY_RETRY_BACKOFF_BASE: Base backoff in seconds (default: 0.3)
    SECRET_CACHE_TTL_SECONDS: Secret cache TTL in seconds (default: 300)
"""

import os
from typing import Any

from proxy_utils import build_target_url, forward_request, get_secret_token


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Proxy authenticated API Gateway requests to Global Accelerator.

    Args:
        event: API Gateway proxy event containing HTTP request details
        context: Lambda context object

    Returns:
        API Gateway proxy response with status code, headers, and body
    """
    ga_endpoint = os.environ["GLOBAL_ACCELERATOR_ENDPOINT"]
    secret_token = get_secret_token()

    # Extract request details
    http_method = event["httpMethod"]
    path = event["path"]
    query_string = event.get("queryStringParameters") or {}
    headers = dict(event.get("headers") or {})
    body = event.get("body") or ""

    # Build target URL (HTTP — Global Accelerator handles TLS termination)
    target_url = build_target_url(ga_endpoint, path, query_string)

    # Add secret header to prove request came through API Gateway
    headers["X-GCO-Auth-Token"] = secret_token

    return forward_request(target_url, http_method, headers, body)
