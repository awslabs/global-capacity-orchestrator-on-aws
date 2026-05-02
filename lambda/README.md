# Lambda Functions

AWS Lambda functions that power GCO's infrastructure layer. These are deployed as part of the CDK stacks and handle cluster operations, API routing, security, and cross-region coordination.

## Contents

| Directory | Description |
|-----------|-------------|
| `kubectl-applier-simple/` | Applies Kubernetes manifests to EKS clusters during CDK deployment. Contains the nodepool, RBAC, service, and storage manifests in `manifests/`. |
| `helm-installer/` | Installs Helm charts (KEDA, Volcano, KubeRay, Kueue, GPU Operator, DRA) into EKS clusters during deployment. |
| `api-gateway-proxy/` | Proxies requests from the global API Gateway through Global Accelerator to regional ALBs. Injects the secret authentication header. |
| `regional-api-proxy/` | Proxies requests from regional API Gateways directly to the internal NLB via VPC Link. Used for private cluster access. |
| `cross-region-aggregator/` | Aggregates job status, health, and inference data across all deployed regions into a single response. Powers the global API endpoints. |
| `secret-rotation/` | Rotates the authentication secret in AWS Secrets Manager on a daily schedule. Ensures zero-downtime rotation. |
| `ga-registration/` | Registers regional ALB endpoints with AWS Global Accelerator during stack deployment. |
| `alb-header-validator/` | ALB Lambda target that validates the secret authentication header on incoming requests. |
| `proxy-shared/` | Shared utilities used by the API Gateway and regional proxy Lambda functions. |

## Build

The `kubectl-applier-simple` Lambda requires a build step to package dependencies:

```bash
rm -rf lambda/kubectl-applier-simple-build
mkdir -p lambda/kubectl-applier-simple-build
cp lambda/kubectl-applier-simple/handler.py lambda/kubectl-applier-simple-build/
cp -r lambda/kubectl-applier-simple/manifests lambda/kubectl-applier-simple-build/
pip3 install kubernetes pyyaml urllib3 -t lambda/kubectl-applier-simple-build/
```

The GCO CLI handles this automatically during `gco stacks deploy`.

## Architecture

```text
API Gateway → api-gateway-proxy → Global Accelerator → ALB → EKS
                                                         ↑
                                              alb-header-validator
                                              (validates secret header)

Regional API → regional-api-proxy → Internal NLB → EKS

CDK Deploy → kubectl-applier-simple → EKS (applies manifests)
           → helm-installer → EKS (installs Helm charts)
           → ga-registration → Global Accelerator (registers endpoints)

Scheduled → secret-rotation → Secrets Manager (daily rotation)

Global API → cross-region-aggregator → DynamoDB/SSM (aggregates all regions)
```
