# Quick Start Guide

Get GCO (Global Capacity Orchestrator on AWS) running in under 60 minutes.

> **💡 Tip:** GCO includes an [MCP server](mcp/) you can connect to an agent for guided exploration. Ask questions like *"What do I need to deploy?"* or *"Explain the architecture"* and the agent will pull from the docs and source code. See [mcp/README.md](mcp/README.md) for setup.

## Table of Contents

- [Prerequisites Check](#prerequisites-check)
- [Step 1: Clone and Install](#step-1-clone-and-install)
- [Step 2: Install GCO CLI](#step-2-install-gco-cli)
- [Step 3: Bootstrap CDK](#step-3-bootstrap-cdk-optional)
- [Step 4: Deploy Infrastructure](#step-4-deploy-infrastructure)
- [Step 5: Configure Cluster Access](#step-5-configure-cluster-access)
- [Step 6: Run a Test Job](#step-6-run-a-test-job)
- [Step 7: Deploy an Inference Endpoint](#step-7-deploy-an-inference-endpoint-optional)
- [Next Steps](#next-steps)
- [Common Issues](#common-issues)
- [Clean Up](#clean-up)

## Prerequisites Check

```bash
# Verify AWS CLI
aws --version
aws sts get-caller-identity

# Verify CDK
cdk --version

# Verify Python
python3 --version

# Verify Docker/Finch
finch version  # or: docker --version

# Verify Node.js (LTS version recommended)
node --version
```

## Step 1: Clone and Install

```bash
# Clone repository
git clone <repository-url>
cd GCO

# Install CDK globally (if not already installed)
npm install -g aws-cdk
```

## Step 2: Install GCO CLI

Pick one of the following methods:

**Option A: Using pip with virtual environment (recommended for development):**

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the CLI with all dev tools
pip install -e ".[dev]"

# Verify
gco --version
```

**Option B: Using pipx (recommended for CLI-only usage):**

```bash
# Install pipx if you don't have it
brew install pipx && pipx ensurepath  # macOS

# Install GCO CLI (from the project directory)
pipx install -e .

# Verify installation
gco --version
```

## Step 3: Bootstrap CDK (Optional)

CDK bootstrap runs automatically during `deploy` and `deploy-all` if a region hasn't been bootstrapped yet. You can skip this step entirely.

If you prefer to bootstrap manually:

```bash
# Bootstrap CDK in your target region (optional — deploy will do this automatically)
gco stacks bootstrap -r us-east-1
```

## Step 4: Deploy Infrastructure

```bash
# Start Finch VM (if using Finch)
finch vm start

# Deploy all stacks
gco stacks deploy-all -y
```

Or deploy a single region:

```bash
gco stacks deploy gco-us-east-1 -y
```

> **Note:** The CLI automatically detects Docker or Finch. If you need to override, set `CDK_DOCKER=docker` or `CDK_DOCKER=finch`.

**What's being created:**

- VPC with public/private subnets
- EKS Auto Mode cluster
- Application Load Balancer
- API Gateway
- Lambda function for kubectl operations
- Health Monitor and Manifest Processor services

## Step 5: Configure Cluster Access

> **Important:** The default EKS endpoint mode is `PRIVATE`, which means kubectl access from outside the VPC is not available. Most users don't need this — you can submit jobs via SQS (`gco jobs submit-sqs`) or API Gateway (`gco jobs submit`) without kubectl access.
>
> If you do need direct kubectl access (e.g., for debugging or manual operations), you must first change the endpoint mode to `PUBLIC_AND_PRIVATE` in `cdk.json`:
>
> ```json
> "endpoint_access": "PUBLIC_AND_PRIVATE"
> ```
>
> Then redeploy the regional stack:
>
> ```bash
> gco stacks deploy gco-us-east-1 -y
> ```

Once the endpoint is set to `PUBLIC_AND_PRIVATE`:

```bash
# Setup kubectl access
./scripts/setup-cluster-access.sh gco-us-east-1 us-east-1
```

**What this script does:**

- Configures kubectl access to the cluster
- Adds your IAM principal to the EKS access entries
- Verifies all components are running

## Step 6: Run a Test Job

**Via API Gateway (recommended — works with the default PRIVATE endpoint):**

```bash
# Submit a job via the API Gateway (uses SigV4 auth, no kubectl needed)
gco jobs submit examples/simple-job.yaml -n gco-jobs

# Check job status
gco jobs list --all-regions

# View logs once the job completes
gco jobs logs hello-gco -n gco-jobs -r us-east-1

# Clean up
gco jobs delete hello-gco -n gco-jobs -r us-east-1 -y
```

**Other submission methods:**

```bash
# Via SQS — queues the job for pickup by a KEDA-scaled processor.
# Via SQS queue (recommended — processed automatically by the built-in queue processor)
gco jobs submit-sqs examples/simple-job.yaml --region us-east-1

# Via kubectl (requires PUBLIC_AND_PRIVATE endpoint mode — see Step 5)
kubectl apply -f examples/simple-job.yaml
```

## Success! 🎉

Your GCO cluster is ready. Here are some things to try:

```bash
# Check GPU capacity before submitting GPU jobs
gco capacity check --instance-type g4dn.xlarge --region us-east-1

# Get a region recommendation for your workload
gco capacity recommend --instance-type g5.xlarge --gpu-count 1

# View costs by region
gco costs summary

# Run a multi-step pipeline (DAG)
gco dag run examples/pipeline-dag.yaml --region us-east-1

# Check cluster health
gco capacity status
```

## Step 7: Deploy an Inference Endpoint (Optional)

GCO can also deploy long-running inference endpoints across regions. Here's a quick example:

```bash
# Deploy a vLLM inference endpoint
gco inference deploy my-llm \
  -i vllm/vllm-openai:v0.20.1 \
  --gpu-count 1 \
  -e MODEL=meta-llama/Llama-3.1-8B-Instruct \
  -r us-east-1

# Check deployment progress
gco inference status my-llm

# List all inference endpoints
gco inference list

# Clean up when done
gco inference delete my-llm -y
```

The inference_monitor in each target region automatically creates the Kubernetes Deployment, Service, and Ingress. See [docs/INFERENCE.md](docs/INFERENCE.md) for the full inference guide including model weight management, multi-region deployment, and supported frameworks.

## Next Steps

- Read [README.md](README.md) for full documentation
- See [docs/INFERENCE.md](docs/INFERENCE.md) for inference serving guide
- See [docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md) for customization options
- Review [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for architecture details
- Enable [Regional API](docs/CUSTOMIZATION.md#regional-api-gateway-private-access) for private cluster access

### MCP Server (for Kiro / LLM integration)

GCO includes an MCP server with 44 tools that wrap the CLI. To use it with Kiro:

```bash
# Install MCP dependencies
pip install -e ".[mcp]"

# Add to your Kiro MCP config (~/.kiro/settings/mcp.json):
# {
#   "mcpServers": {
#     "gco": {
#       "command": "python3",
#       "args": ["mcp/run_mcp.py"]
#     }
#   }
# }
```

## Common Issues

### CDK CLI version mismatch

If you see `Cloud assembly schema version mismatch`, your CDK CLI is too old. Install the latest version:

```bash
npm install -g aws-cdk@latest
cdk --version
```

### "Stack already exists"

If deployment fails partway through, destroy and redeploy:

```bash
gco stacks destroy-all -y
gco stacks deploy-all -y
```

### "Unauthorized" when using kubectl

Make sure you ran the cluster access setup script (Step 5) and that the endpoint mode is set to `PUBLIC_AND_PRIVATE` in `cdk.json`.

### Pods not starting

Check pod events for details:

```bash
kubectl describe pods -n gco-system
```

## Clean Up

When you're done testing:

```bash
# Destroy all stacks
gco stacks destroy-all -y
```

---

**Need help?** Check [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
