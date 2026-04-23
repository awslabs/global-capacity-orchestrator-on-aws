<div align="center">

<h1>Global Capacity Orchestrator (GCO)</h1>

<table>
<tr>
<td><strong>CI/CD</strong></td>
<td>
  <img src="https://img.shields.io/badge/pytest-3610-blue?style=flat&logo=pytest&logoColor=white" alt="PyTest">
  <img src="https://img.shields.io/badge/BATS-167-blue?style=flat&logo=gnu-bash&logoColor=white" alt="BATS">
  <img src="https://img.shields.io/badge/coverage-85%25+-brightgreen?style=flat" alt="Coverage">
</td>
</tr>
<tr>
<td><strong>Security</strong></td>
<td>
  <img src="https://img.shields.io/badge/Bandit-SAST-4B8BBE?style=flat&logo=python&logoColor=white" alt="Bandit">
  <img src="https://img.shields.io/badge/Trivy-container%20scan-4B8BBE?style=flat&logo=aqua&logoColor=white" alt="Trivy">
  <img src="https://img.shields.io/badge/Checkov-IaC%20scan-4B8BBE?style=flat&logo=paloaltonetworks&logoColor=white" alt="Checkov">
  <img src="https://img.shields.io/badge/KICS-IaC%20scan-4B8BBE?style=flat" alt="KICS">
  <img src="https://img.shields.io/badge/Semgrep-SAST-4B8BBE?style=flat" alt="Semgrep">
  <img src="https://img.shields.io/badge/Gitleaks-secrets-4B8BBE?style=flat" alt="Gitleaks">
  <img src="https://img.shields.io/badge/TruffleHog-secrets-4B8BBE?style=flat" alt="TruffleHog">
  <img src="https://img.shields.io/badge/Safety-dependency%20scan-4B8BBE?style=flat&logo=pypi&logoColor=white" alt="Safety">
  <img src="https://img.shields.io/badge/pip--audit-dependency%20scan-4B8BBE?style=flat&logo=pypi&logoColor=white" alt="pip-audit">
  <img src="https://img.shields.io/badge/cdk--nag-AWS%20Solutions%20%7C%20HIPAA%20%7C%20NIST%20%7C%20PCI%20%7C%20Serverless%20packs-4B8BBE?style=flat&logo=amazonaws&logoColor=white" alt="cdk-nag">
</td>
</tr>
<tr>
<td><strong>Linting</strong></td>
<td>
  <img src="https://img.shields.io/badge/Ruff-linter-7B68AE?style=flat&logo=ruff&logoColor=white" alt="Ruff">
  <img src="https://img.shields.io/badge/Flake8-linter-7B68AE?style=flat&logo=python&logoColor=white" alt="Flake8">
  <img src="https://img.shields.io/badge/Black-formatter-7B68AE?style=flat&logo=python&logoColor=white" alt="Black">
  <img src="https://img.shields.io/badge/isort-imports-7B68AE?style=flat&logo=python&logoColor=white" alt="isort">
  <img src="https://img.shields.io/badge/mypy-strict-7B68AE?style=flat&logo=python&logoColor=white" alt="mypy strict">
  <img src="https://img.shields.io/badge/yamllint-YAML-7B68AE?style=flat" alt="yamllint">
  <img src="https://img.shields.io/badge/Hadolint-Dockerfile-7B68AE?style=flat&logo=docker&logoColor=white" alt="Hadolint">
  <img src="https://img.shields.io/badge/ShellCheck-shell%20lint-7B68AE?style=flat&logo=gnu-bash&logoColor=white" alt="ShellCheck">
</td>
</tr>
<tr>
<td><strong>Stack</strong></td>
<td>
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/CDK-2.248-FF9900?style=flat&logo=amazonaws&logoColor=white" alt="CDK">
  <img src="https://img.shields.io/badge/EKS-Auto%20Mode-FF9900?style=flat&logo=amazoneks&logoColor=white" alt="EKS Auto Mode">
  <img src="https://img.shields.io/badge/Kubernetes-1.35-326CE5?style=flat&logo=kubernetes&logoColor=white" alt="Kubernetes">
  <img src="https://img.shields.io/badge/EKS%20v2-L2%20Constructs-FF9900?style=flat&logo=amazonaws&logoColor=white" alt="EKS v2">
  <img src="https://img.shields.io/badge/Inference_Examples-vLLM%20%7C%20TGI%20%7C%20Triton%20%7C%20SGLang-10b981?style=flat&logo=nvidia&logoColor=white" alt="Inference Examples">
  <img src="https://img.shields.io/badge/MCP%20tools-44-10b981?style=flat&logo=anthropic&logoColor=white" alt="MCP Tools">
</td>
</tr>
</table>

**Multi-region EKS Auto Mode platform for AI/ML workload orchestration**



</div>

<div align="center">

![GCO Live Demo](demo/live_demo.gif)

*Cost tracking, capacity discovery, 5 schedulers (Volcano, Kueue, YuniKorn, Slurm, KEDA), FSx Lustre, Valkey cache, live LLM inference, and EFS storage — all from one CLI, on one cluster, deployed with one command. ([source](demo/live_demo.sh) · [re-record](demo/record_demo.sh))*

<details>
<summary>📦 Deploy recording (click to expand)</summary>

![GCO Deploy](demo/deploy.gif)

*Fresh `gco stacks deploy-all -y` from a clean account ([re-record](demo/record_deploy.sh))*

</details>

<details>
<summary>🗑️ Destroy recording (click to expand)</summary>

![GCO Destroy](demo/destroy.gif)

*Full teardown with `gco stacks destroy-all -y` ([re-record](demo/record_destroy.sh))*

</details>

</div>

GCO is an experimental platform that spins up EKS Auto Mode clusters across AWS regions, wired together with Global Accelerator for low-latency routing. It handles the heavy lifting of multi-region GPU orchestration for AI/ML workloads — capacity-aware scheduling, spot fallback, inference endpoint management — and exposes a simple REST API and CLI for submitting Kubernetes manifests. Think of it as a control plane for running GPU jobs wherever capacity is available.

> **💡 New to the codebase?** GCO ships with an [MCP server](mcp/) that exposes 44 tools and indexes the entire project — docs, examples, source code, K8s manifests, scripts, and more. Connect it to an AI-powered IDE with MCP support (like [Kiro](https://kiro.dev)) and ask questions in natural language: *"How does region recommendation work?"*, *"Walk me through the inference deployment flow"*, *"What NodePools are configured?"*. See [mcp/README.md](mcp/README.md) for setup.

## Table of Contents

- [Why GCO?](#why-gco)
- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Key Features](#key-features)
- [Documentation](#documentation)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [Support](#support)

## Why GCO?

Running GPU workloads at scale is hard. You need to find regions with available capacity, provision clusters, handle authentication, deal with failover, and persist outputs after pods terminate. GCO solves all of this with a single deployable platform.

| Challenge | Traditional Approach | With GCO |
|-----------|---------------------|--------------|
| GPU availability | Manually check each region | Auto-routes to available capacity |
| Node provisioning | Pre-provision or wait for scaling | EKS Auto Mode provisions on-demand |
| Multi-region ops | Manage clusters separately | Single API, automatic routing |
| Authentication | Configure per-cluster access | IAM-based, uses existing AWS credentials |
| Job outputs | Lost when pods terminate | Persisted to EFS/FSx storage |
| Inference serving | Deploy and manage per-region | Deploy once, serve globally |
| Failover | Manual intervention required | Automatic via Global Accelerator |

**When to use GCO:**
- You need to run GPU workloads (training, inference, batch processing)
- You want to deploy inference endpoints across multiple regions with a single command
- You want multi-region redundancy without managing multiple clusters
- You prefer IAM authentication over kubeconfig management
- You need job outputs to persist after completion

## Quick Start

### Install and Deploy

```bash
# Install the CLI
brew install pipx && pipx ensurepath  # macOS
pipx install -e .

# Deploy everything (CDK bootstrap runs automatically)
gco stacks deploy-all -y

# Optional: configure kubectl access (requires PUBLIC_AND_PRIVATE endpoint mode)
# The default endpoint mode is PRIVATE — see docs/CUSTOMIZATION.md for details.
# Most users don't need this; submit jobs via SQS or API Gateway instead.
# gco stacks access -r us-east-1
```

### Submit Your First Job

```bash
# Check GPU capacity
gco capacity check --instance-type g4dn.xlarge --region us-east-1

# Submit a job (pick your preferred method)
gco jobs submit-sqs examples/simple-job.yaml --region us-east-1    # via SQS (recommended)
gco queue submit examples/simple-job.yaml --region us-east-1       # via Global DynamoDB queue
gco jobs submit examples/simple-job.yaml -n gco-jobs               # via API Gateway
gco jobs submit-direct examples/simple-job.yaml -r us-east-1       # via kubectl

# Check status and get logs
gco jobs list --all-regions
gco jobs logs hello-gco -n gco-jobs -r us-east-1
```

### Deploy an Inference Endpoint

```bash
gco inference deploy my-llm -i vllm/vllm-openai:v0.19.1 --gpu-count 1
gco inference status my-llm
gco inference scale my-llm --replicas 3
```

See the [Quick Start Guide](QUICKSTART.md) for the full step-by-step walkthrough, or the [CLI Reference](docs/CLI.md) for all available commands.

## Architecture Overview

<details>
<summary>📊 Full Architecture Diagram (click to expand)</summary>

![Full Architecture](diagrams/diagram.full-architecture.png)

</details>

> The regional stack can be deployed to any AWS region. Add or remove regions by editing the `deployment_regions.regional` array in `cdk.json`.

```
┌───────────────────────────────────────────────────┐
│              User Request                         │
│        (AWS SigV4 Authentication)                 │
└────────────────────┬──────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────┐
│      API Gateway (Edge-Optimized, Global)         │
│      ✓ IAM Authentication Required                │
│      ✓ CloudFront Edge Caching                    │
└────────────────────┬──────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────┐
│              AWS Global Accelerator               │
│         Routes to nearest healthy region          │
└────────────────────┬──────────────────────────────┘
                     │
        ┌────────────┼────────────┬────────────┐
        │            │            │            │
   ┌────▼────┐  ┌────▼────┐  ┌────▼────┐  ┌────▼────┐
   │us-east-1│  │us-west-2│  │eu-west-1│  │  More   │
   │   ALB   │  │   ALB   │  │   ALB   │  │ Regions │
   │(GA IPs  │  │(GA IPs  │  │(GA IPs  │  |(GA IPs  │
   │  only)  │  │  only)  │  │  only)  │  |  only)  │
   └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘
        │            │            │            │
   ┌────▼────────────▼────────────▼────────────▼────┐
   │    EKS Auto Mode Cluster (per region)          │
   │  ┌─────────────────────────────────────────┐   │
   │  │  Nodepools: System, General, GPU (x86   │   │
   │  │  + ARM), Inference                      │   │
   │  ├─────────────────────────────────────────┤   │
   │  │  Services: Health Monitor, Manifest     │   │
   │  │  Processor, Inference Monitor           │   │
   │  ├─────────────────────────────────────────┤   │
   │  │  Storage: EFS (shared) + FSx (optional) │   │
   │  └─────────────────────────────────────────┘   │
   └────────────────────────────────────────────────┘
```

### Security Model

Five layers protect every request:

1. **IAM Authentication** — API Gateway validates AWS credentials (SigV4)
2. **Secret Header** — Lambda injects a rotating token from Secrets Manager
3. **IP Restriction** — ALBs only accept Global Accelerator IPs
4. **Header Validation** — Backend services verify the secret token
5. **IRSA** — Pods use IAM roles for AWS access (no static credentials)

```
Request flow: User → API Gateway (SigV4) → Lambda (adds secret) → Global Accelerator
  → ALB (GA IPs only) → Services (validate secret)
```

For private clusters, [Regional API Gateways](docs/CUSTOMIZATION.md#regional-api-gateway-private-access) provide direct VPC access without public ALB exposure.

See [Architecture Details](docs/ARCHITECTURE.md) for the full deep dive.

## Key Features

### Compute & Orchestration
- **EKS Auto Mode** with automatic node provisioning — no pre-scaling needed
- **GPU support** for x86_64 (g4dn, g5) and ARM64 (g5g) via Karpenter nodepools
- **Multiple submission methods**: API Gateway, SQS queues, DynamoDB job queue, or direct kubectl
- **Job pipelines (DAGs)**: Multi-step ML pipelines with dependency ordering and failure handling
- **Helm-managed ecosystem**: KEDA, Volcano, KubeRay, Kueue, GPU Operator, DRA, and more — configurable via `cdk.json`

### Inference Serving
- **Multi-region inference**: Deploy endpoints (vLLM, TGI, Triton, TorchServe, SGLang) across regions with a single command
- **Canary deployments**: A/B test new model versions with weighted traffic routing
- **Model weight management**: Central S3 bucket with KMS encryption, automatic sync to each region
- **Spot instance support**: Run inference on spot GPUs for significant cost savings
- **Autoscaling**: HPA-based scaling with CPU/memory metrics

### Networking & Security
- **Global Accelerator**: Single anycast endpoint with automatic failover
- **IAM authentication**: SigV4 at the API Gateway — no kubeconfig distribution
- **Compliance validated**: CDK-nag checks for AWS Solutions, HIPAA, NIST 800-53, PCI DSS
- **Network policies**: Default-deny with explicit allow rules for all service communication
- **EFA support**: Optional Elastic Fabric Adapter for high-bandwidth distributed training and NIXL-based inference (toggle on/off)

### Storage & Data
- **EFS**: Shared elastic storage for job outputs that persist after pod termination
- **FSx for Lustre**: Optional high-performance parallel file system for ML training (toggle on/off)
- **Valkey cache**: Optional serverless key-value cache for prompt caching and session state

### Operations
- **Cost visibility**: Track spend by service, region, and workload via Cost Explorer integration
- **Auto-bootstrap**: CDK bootstrap runs automatically for new regions during deploy
- **Multi-region monitoring**: CloudWatch dashboards, alarms, and SNS alerts across all regions

## Documentation

**New to GCO?** Start here:

| Your Goal | Read This |
|-----------|-----------|
| Understand what GCO does | [Core Concepts](docs/CONCEPTS.md) |
| Get running in under 60 minutes | [Quick Start Guide](QUICKSTART.md) |
| Learn the architecture | [Architecture Details](docs/ARCHITECTURE.md) |

**Day-to-day operations:**

| Your Goal | Read This |
|-----------|-----------|
| CLI commands and usage | [CLI Reference](docs/CLI.md) |
| Deploy inference endpoints | [Inference Guide](docs/INFERENCE.md) |
| Use the REST API directly | [API Reference](docs/API.md) |
| Fix issues | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Respond to incidents | [Operational Runbooks](docs/RUNBOOKS.md) |

**Customization and development:**

| Your Goal | Read This |
|-----------|-----------|
| Add regions, tune nodepools, enable FSx | [Customization Guide](docs/CUSTOMIZATION.md) |
| Choose a scheduler for your workload | [Schedulers & Orchestrators](docs/SCHEDULERS.md) |
| Configure the SQS queue processor | [Queue Processor Config](docs/CUSTOMIZATION.md#queue-processor-sqs-consumer) |
| Contribute to the project | [Contributing](CONTRIBUTING.md) |
| API client examples (Python, curl, AWS CLI) | [Client Examples](docs/client-examples/README.md) |
| IAM policy templates | [IAM Policies](docs/iam-policies/README.md) |
| Presentation slides and demo scripts | [Demo Starter Kit](demo/README.md) |

### Prerequisites

- AWS CLI configured with appropriate credentials
- Python 3.10+ and Node.js LTS (v20 or v22)
- AWS CDK CLI (`npm install -g aws-cdk`)
- Docker or Finch (for building container images)

Or skip local setup entirely with the dev container:

```bash
docker build -f Dockerfile.dev -t gco-dev .
docker run -it --rm -v ~/.aws:/root/.aws:ro -v $(pwd):/workspace -w /workspace gco-dev
```

## Project Structure

```
.
├── app.py                               # CDK app entry point
├── cdk.json                             # CDK configuration (regions, features, thresholds)
├── pyproject.toml                       # Project metadata, dependencies, and CLI installation
│
├── cli/                                 # GCO CLI (jobs, stacks, capacity, inference, costs, DAGs)
├── diagrams/                            # Auto-generated architecture diagrams
├── docs/                                # Documentation (architecture, CLI, API, inference, customization)
├── examples/                            # Example manifests (jobs, inference, Ray, Volcano, Kueue, Slurm, YuniKorn)
├── gco/
│   ├── config/                          # Configuration loader with validation
│   ├── models/                          # Data models for k8s clusters, health monitor, inference monitor and manifest processor
│   ├── services/                        # K8s services (health monitor, inference monitor, manifest processor, queue processor)
│   └── stacks/                          # CDK stacks (global, regional, API gateway, monitoring)
│
├── lambda/                              # Lambda functions
│   ├── alb-header-validator/            # ALB header validation for auth tokens
│   ├── api-gateway-proxy/               # API Gateway → Global Accelerator proxy
│   ├── cross-region-aggregator/         # Cross-region job/health aggregation
│   ├── ga-registration/                 # Global Accelerator endpoint registration
│   ├── helm-installer/                  # Installs Helm charts (schedulers, GPU operators, cert-manager)
│   │   └── charts.yaml                  # Helm chart configuration (schedulers, GPU operators, cert-manager)
│   ├── kubectl-applier-simple/          # Applies K8s manifests during deployment
│   │   └── manifests/                   # Kubernetes manifests (nodepools, RBAC, services, storage)
│   ├── proxy-shared/                    # Shared utilities for proxy Lambdas
│   ├── regional-api-proxy/              # Regional API Gateway → internal ALB proxy
│   └── secret-rotation/                 # Daily secret rotation
│
├── mcp/                                 # MCP server for LLM interaction (44 tools wrapping the CLI)
├── scripts/                             # Utility scripts (version bump, cluster access setup)
└── tests/                               # 3,610 PyTest tests with 85%+ coverage and 167 BATS tests for shell scripts
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, CI/CD pipeline details, release process, and dependency scanning schedules.

Quick start for contributors:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v --cov=gco --cov=cli
```

## License

See the [LICENSE](LICENSE) file for details.

## Support

- Check [Troubleshooting](docs/TROUBLESHOOTING.md) for common issues
- Review CloudWatch logs for Lambda and EKS errors
- Open an issue on [GitHub](https://github.com/awslabs/global-capacity-orchestrator-on-aws/issues)

---
