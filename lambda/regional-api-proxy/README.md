# Regional API Proxy

Proxies authenticated requests from the regional API Gateway directly to the internal ALB via VPC networking. Used when public access is disabled and the ALB is internal-only.

## Table of Contents

- [Trigger](#trigger)
- [How It Works](#how-it-works)
- [Environment Variables](#environment-variables)
- [IAM Permissions](#iam-permissions)
- [Dependencies](#dependencies)

## Trigger

Regional API Gateway (proxy integration) — all routes are forwarded through this Lambda.

## How It Works

1. Regional API Gateway validates the caller's IAM credentials (SigV4)
2. This Lambda (running inside the VPC) retrieves the auth token from Secrets Manager (cached with 5-min TTL)
3. Adds `X-GCO-Auth-Token` header to the request
4. Strips hop-by-hop and forwarding headers (`Host`, `X-Forwarded-*`)
5. Forwards the request to the internal ALB over HTTP (TLS not needed inside VPC)
6. Returns the upstream response to the caller

Includes retry logic with exponential backoff for transient failures (502/503/504/429).

## Input

API Gateway proxy event (httpMethod, path, queryStringParameters, headers, body).

## Output

API Gateway proxy response (statusCode, headers, body).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ALB_ENDPOINT` | Yes | DNS name of the internal ALB |
| `SECRET_ARN` | Yes | ARN of the Secrets Manager secret |
| `PROXY_MAX_RETRIES` | No | Max retry attempts (default: 3) |
| `PROXY_RETRY_BACKOFF_BASE` | No | Base backoff in seconds (default: 0.3) |
| `SECRET_CACHE_TTL_SECONDS` | No | Secret cache TTL in seconds (default: 300) |

## IAM Permissions

- `secretsmanager:GetSecretValue` on the secret ARN
- VPC access (ENI creation) for reaching the internal ALB

## Dependencies

- `proxy_utils.py` — shared secret caching and HTTP forwarding utilities (copied from `proxy-shared/`)
