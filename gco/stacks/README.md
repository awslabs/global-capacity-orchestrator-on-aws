# CDK Stacks

AWS CDK stack definitions that create the GCO cloud infrastructure. Each stack is a self-contained unit that can be deployed independently (respecting dependency order).

## Table of Contents

- [Overview](#overview)
- [Stack Dependency Order](#stack-dependency-order)
- [Files](#files)
- [Deployment](#deployment)
- [Adding a New Stack](#adding-a-new-stack)

## Overview

GCO deploys four stack layers in order: Global → API Gateway → Regional (per-region) → Monitoring. The regional stack is the largest (~3200 lines) and creates the EKS cluster, VPC, ALB, storage, Lambda functions, and container images for a single AWS region.

## Stack Dependency Order

```text
1. GCOGlobalStack          → Global Accelerator, DynamoDB tables, S3 model bucket
2. GCOApiGatewayGlobalStack → API Gateway, auth secret, Lambda proxy
3. GCORegionalStack (×N)   → VPC, EKS, ALB, EFS/FSx, Lambdas, container images
4. GCOMonitoringStack       → CloudWatch dashboards, alarms, SNS
```

## Files

| File | Description |
|------|-------------|
| `global_stack.py` | Global Accelerator, SSM parameters, S3 model bucket, DynamoDB tables (templates, webhooks, inference endpoints) |
| `api_gateway_global_stack.py` | Edge-optimized API Gateway with IAM auth (SigV4), Lambda proxy, Secrets Manager secret with daily rotation, multi-region replication |
| `regional_stack.py` | Per-region VPC (3 AZs), EKS Auto Mode cluster, ALB, EFS/FSx storage, ECR images, Lambda functions (kubectl-applier, helm-installer, GA registration), IRSA roles |
| `regional_api_gateway_stack.py` | Regional API Gateway for private VPC access via internal NLB |
| `monitoring_stack.py` | Cross-region CloudWatch dashboard (GA, API GW, Lambda, SQS, DynamoDB, EKS, ALB widgets), SNS alerting, CloudWatch alarms |
| `nag_suppressions.py` | CDK-nag compliance suppressions for five rule packs (AWS Solutions, HIPAA, NIST 800-53, PCI DSS, Serverless) |
| `constants.py` | Pinned versions for EKS addons, Lambda runtimes, Aurora engine, Helm charts |
| `__init__.py` | Package exports |

## Deployment

```bash
gco stacks deploy-all -y          # Deploy all stacks in dependency order
gco stacks deploy gco-us-east-1   # Deploy a single regional stack
gco stacks destroy-all -y         # Tear down everything
```

## Adding a New Stack

1. Create a new file in this directory (e.g. `my_stack.py`)
2. Subclass `aws_cdk.Stack`
3. Wire it into `app.py` with the correct dependency order
4. Add cdk-nag suppressions in `nag_suppressions.py` if needed
5. Add the stack to `tests/_cdk_config_matrix.py` so it's covered by the nag compliance gate
