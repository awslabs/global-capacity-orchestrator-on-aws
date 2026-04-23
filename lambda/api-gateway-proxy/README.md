# API Gateway Proxy

Proxies authenticated requests from the global API Gateway through Global Accelerator to regional ALBs. Injects the secret `X-GCO-Auth-Token` header to prove the request came through the IAM-authenticated API Gateway path.

## Trigger

API Gateway (proxy integration) — all routes are forwarded through this Lambda.

## How It Works

1. API Gateway validates the caller's IAM credentials (SigV4)
2. This Lambda retrieves the auth token from Secrets Manager (cached with 5-min TTL)
3. Adds `X-GCO-Auth-Token` header to the request
4. Forwards the request to the Global Accelerator endpoint over HTTP
5. Returns the upstream response to the caller

Includes retry logic with exponential backoff for transient failures (502/503/504/429).

## Input

API Gateway proxy event (httpMethod, path, queryStringParameters, headers, body).

## Output

API Gateway proxy response (statusCode, headers, body).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GLOBAL_ACCELERATOR_ENDPOINT` | Yes | DNS name of the Global Accelerator |
| `SECRET_ARN` | Yes | ARN of the Secrets Manager secret |
| `PROXY_MAX_RETRIES` | No | Max retry attempts (default: 3) |
| `PROXY_RETRY_BACKOFF_BASE` | No | Base backoff in seconds (default: 0.3) |
| `SECRET_CACHE_TTL_SECONDS` | No | Secret cache TTL in seconds (default: 300) |

## IAM Permissions

- `secretsmanager:GetSecretValue` on the secret ARN

## Dependencies

- `urllib3`, `boto3` (see `requirements.txt`)
- `proxy_utils.py` — shared secret caching and HTTP forwarding utilities
