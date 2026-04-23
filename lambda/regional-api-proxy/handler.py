"""
Regional API proxy Lambda for forwarding authenticated requests to internal ALB.

This Lambda function runs inside the VPC and forwards requests directly to the
internal ALB. It's used when public access is disabled and the ALB is internal-only.

Security Flow:
    1. User sends request with AWS SigV4 signature
    2. Regional API Gateway validates IAM credentials
    3. This Lambda (in VPC) receives the authenticated request
    4. Lambda retrieves secret token from Secrets Manager
    5. Lambda adds X-GCO-Auth-Token header
    6. Lambda forwards request to internal ALB (via VPC network)
    7. Backend services validate the secret header

Environment Variables:
    ALB_ENDPOINT: DNS name of the internal ALB
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
    Proxy authenticated API Gateway requests to internal ALB.

    This Lambda runs inside the VPC and can reach the internal ALB directly.

    Args:
        event: API Gateway proxy event containing HTTP request details
        context: Lambda context object

    Returns:
        API Gateway proxy response with status code, headers, and body
    """
    alb_endpoint = os.environ["ALB_ENDPOINT"]
    secret_token = get_secret_token()

    # Extract request details
    http_method = event["httpMethod"]
    path = event["path"]
    query_string = event.get("queryStringParameters") or {}
    headers = dict(event.get("headers") or {})
    body = event.get("body") or ""

    # Build target URL (HTTP — internal ALB, TLS not needed inside VPC)
    target_url = build_target_url(alb_endpoint, path, query_string)

    # Add secret header to prove request came through API Gateway
    headers["X-GCO-Auth-Token"] = secret_token

    # Remove headers that shouldn't be forwarded to internal ALB
    for h in ["Host", "host", "X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Port"]:
        headers.pop(h, None)

    return forward_request(target_url, http_method, headers, body)
