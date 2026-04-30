# Secret Rotation

Rotates the GCO API Gateway authentication token in AWS Secrets Manager. Follows the standard 4-step Secrets Manager rotation protocol.

## Table of Contents

- [Trigger](#trigger)
- [How It Works](#how-it-works)
- [IAM Permissions](#iam-permissions)
- [Dependencies](#dependencies)

## Trigger

Secrets Manager automatic rotation (daily schedule).

## How It Works

Implements the 4-step rotation protocol:

1. **createSecret** — Generates a new 64-character alphanumeric token and stores it as `AWSPENDING`
2. **setSecret** — No-op (no external system to update; services read directly from Secrets Manager)
3. **testSecret** — Validates the pending secret can be retrieved and has the correct structure
4. **finishSecret** — Atomically moves `AWSPENDING` to `AWSCURRENT`

Multi-region replication ensures all regions receive the new token automatically. Services validate against both `AWSCURRENT` and `AWSPENDING` during the rotation window for zero-downtime rotation.

## Input

Secrets Manager rotation event (`SecretId`, `ClientRequestToken`, `Step`).

## Output

None (raises on failure).

## IAM Permissions

- `secretsmanager:GetSecretValue` on the secret
- `secretsmanager:PutSecretValue` on the secret
- `secretsmanager:DescribeSecret` on the secret
- `secretsmanager:UpdateSecretVersionStage` on the secret

## Dependencies

- `boto3` (see `requirements.txt`)
