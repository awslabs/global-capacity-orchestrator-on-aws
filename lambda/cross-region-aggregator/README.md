# Cross-Region Aggregator

Aggregates data (jobs, health, status) from all regional GCO clusters into a single response. Powers the global API endpoints by querying each regional ALB in parallel and merging results.

## Trigger

API Gateway (proxy integration) — handles `/api/v1/global/*` routes.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/global/jobs` | List jobs across all regions |
| GET | `/api/v1/global/health` | Health status across all regions |
| GET | `/api/v1/global/status` | Cluster status across all regions |
| DELETE | `/api/v1/global/jobs` | Bulk delete jobs across all regions |

## How It Works

1. Discovers regional ALB endpoints from SSM Parameter Store (`/{project_name}/alb-hostname-{region}`)
2. Queries all regions in parallel (ThreadPoolExecutor, up to 10 workers)
3. Merges and sorts results (jobs sorted by creation time descending)
4. Returns aggregated response with per-region summaries and any errors

## Input

API Gateway proxy event with path, method, query parameters, and optional body.

## Output

JSON response with aggregated data, region summaries, and error details.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_ARN` | Yes | ARN of the Secrets Manager secret containing the auth token |
| `PROJECT_NAME` | No | Project name for SSM parameter paths (default: `gco`) |
| `GLOBAL_REGION` | No | Region where SSM parameters are stored (default: `us-east-2`) |

## IAM Permissions

- `secretsmanager:GetSecretValue` on the secret ARN
- `ssm:GetParametersByPath` on `/{project_name}/` in the global region

## Dependencies

- `boto3`, `urllib3` (see `requirements.txt`)
