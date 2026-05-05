# GCO Infrastructure Diagrams

This directory contains tools and auto-generated architecture diagrams for the GCO infrastructure.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Generated Diagrams](#generated-diagrams)
- [Stack Overview](#stack-overview)
- [Requirements](#requirements)
- [Customization](#customization)

## Prerequisites

### Graphviz Installation

The diagram generator requires Graphviz to be installed globally for PNG and SVG output.

**macOS (Homebrew):**

```bash
brew install graphviz
```

**Ubuntu/Debian:**

```bash
sudo apt-get install graphviz
```

**Amazon Linux / RHEL / CentOS:**

```bash
sudo yum install graphviz
```

**Windows:**
Download from <https://graphviz.org/download/> and add to PATH.

Without Graphviz, only DOT format files will be generated.

## Quick Start

```bash
# Generate all diagrams
python diagrams/generate.py

# Generate specific stack diagram
python diagrams/generate.py --stack global
python diagrams/generate.py --stack api-gateway
python diagrams/generate.py --stack regional
python diagrams/generate.py --stack regional-api
python diagrams/generate.py --stack monitoring
python diagrams/generate.py --stack analytics
```

## Generated Diagrams

After running the generator, diagrams are saved to `diagrams/`:

| Diagram | Description |
|---------|-------------|
| `global-stack.png/svg` | Global Accelerator and endpoint groups |
| `api-gateway-stack.png/svg` | API Gateway with IAM authentication |
| `regional-stack.png/svg` | EKS cluster, ALB, SQS, EFS, and services |
| `regional-api-stack.png/svg` | Regional API Gateway with VPC Lambda (private access) |
| `monitoring-stack.png/svg` | CloudWatch dashboards, alarms, and SNS |
| `analytics-stack.png/svg` | SageMaker Studio, EMR Serverless, Cognito, and the presigned-URL Lambda |
| `full-architecture.png/svg` | Complete infrastructure (compact view) |
| `full-architecture-detailed.png/svg` | Complete infrastructure (detailed, dark theme) |

## Stack Overview

### Global Stack

- AWS Global Accelerator
- TCP Listeners (ports 80, 443)
- Endpoint groups per region
- SSM parameters for cross-region sharing

### API Gateway Stack

- REST API with IAM authentication
- Lambda proxy function
- Secrets Manager for API keys
- WAF WebACL with AWS managed rules
- CloudWatch logging

### Regional Stack

- EKS cluster with Auto Mode
- Application Load Balancer
- SQS job queue with DLQ
- EFS for persistent storage
- Manifest processor deployment
- Health monitor deployment
- KEDA for autoscaling
- Network policies

### Regional API Gateway Stack

- Regional REST API with IAM authentication
- VPC Lambda proxy function
- Direct access to internal ALB
- Used when public access is disabled

### Monitoring Stack

- CloudWatch dashboard
- Regional alarms (CPU, memory, SQS)
- Composite alarms
- SNS alert topic
- Log groups

### Analytics Stack

- SageMaker Studio domain (VPC-only, IAM auth)
- EMR Serverless Spark application
- Cognito user pool and hosted UI domain
- Analytics KMS key
- Private-isolated VPC with SageMaker, ECR, STS, CloudWatch Logs, and EFS endpoints
- Studio EFS file system for per-user home folders
- Studio-only S3 bucket plus its access-logs sidecar
- Presigned-URL Lambda that fronts `/studio/login`

## Requirements

The diagram generator requires:

- `aws-pdk` (included in `pyproject.toml`)
- Graphviz (see Prerequisites above)

## Customization

Edit `diagrams/generate.py` to customize:

- Diagram themes (`"dark"` or default light)
- Filter presets (`FilterPreset.COMPACT` or `FilterPreset.NONE`)
- Output formats (PNG, SVG, DOT)
