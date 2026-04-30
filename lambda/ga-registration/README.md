# GA Registration

Registers the Ingress-created ALB with AWS Global Accelerator during stack deployment. Also stores the ALB hostname in SSM Parameter Store for cross-region discovery.

## Table of Contents

- [Trigger](#trigger)
- [How It Works](#how-it-works)
- [CloudFormation Properties](#cloudformation-properties)
- [IAM Permissions](#iam-permissions)

## Trigger

CloudFormation Custom Resource — runs on stack Create, Update, and Delete.

## How It Works

### Create/Update
1. Waits for the ALB to be created by the AWS Load Balancer Controller (up to 14 minutes)
2. Detects the ALB using multiple methods: tags, Ingress status, name prefix
3. Registers the ALB with Global Accelerator (idempotent)
4. Stores the ALB hostname in SSM at `/{project_name}/alb-hostname-{region}`

### Delete
1. Removes all endpoints from the GA endpoint group
2. Deletes the Kubernetes Ingress to trigger ALB cleanup
3. Waits for ALB deletion (up to 3 minutes)
4. Removes the ALB hostname from SSM

## Input

CloudFormation Custom Resource event (RequestType, ResourceProperties).

## Output

CloudFormation response with `AlbArn` and `AlbHostname` on success.

## CloudFormation Properties

| Property | Required | Description |
|----------|----------|-------------|
| `ClusterName` | Yes | EKS cluster name |
| `Region` | Yes | AWS region for this cluster |
| `EndpointGroupArn` | Yes | Global Accelerator endpoint group ARN |
| `IngressName` | No | Kubernetes Ingress name (default: `gco-ingress`) |
| `Namespace` | No | Kubernetes namespace (default: `gco-system`) |
| `GlobalRegion` | No | Region for SSM parameters (default: `us-east-2`) |
| `ProjectName` | No | Project name for SSM paths (default: `gco`) |

## IAM Permissions

- `eks:DescribeCluster` on the EKS cluster
- `sts:GetCallerIdentity` (for EKS token generation)
- `elasticloadbalancing:DescribeLoadBalancers`, `elasticloadbalancing:DescribeTags`
- `globalaccelerator:DescribeEndpointGroup`, `globalaccelerator:AddEndpoints`, `globalaccelerator:RemoveEndpoints`
- `ssm:PutParameter`, `ssm:DeleteParameter` in the global region
