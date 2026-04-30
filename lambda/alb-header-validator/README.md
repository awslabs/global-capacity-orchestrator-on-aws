# ALB Header Validator

Validates the `X-GCO-Auth-Token` secret header on incoming requests to ensure they came through the authenticated API Gateway path. Deployed as a Lambda@Edge function on the ALB.

## Table of Contents

- [Trigger](#trigger)
- [How It Works](#how-it-works)
- [Environment Variables](#environment-variables)
- [IAM Permissions](#iam-permissions)

## Trigger

ALB (Lambda target) — invoked on every request hitting the ALB.

## How It Works

1. Extracts the `X-GCO-Auth-Token` header from the incoming request
2. Retrieves the expected token from Secrets Manager (cached in-memory)
3. Returns `403 Forbidden` if the header is missing or doesn't match
4. Passes the request through if valid

## Input

CloudFront/ALB origin request event (`event["Records"][0]["cf"]["request"]`).

## Output

- On success: the original request object (passthrough)
- On failure: `403 Forbidden` JSON response

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_ARN` | Yes | ARN of the Secrets Manager secret containing the auth token |

## IAM Permissions

- `secretsmanager:GetSecretValue` on the secret ARN
