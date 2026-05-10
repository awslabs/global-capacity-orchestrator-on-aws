# Quick Start Guide

Get GCO (Global Capacity Orchestrator on AWS) running in under 60 minutes.

> **💡 Tip:** GCO includes an [MCP server](mcp/) you can connect to an agent for guided exploration. Ask questions like *"What do I need to deploy?"* or *"Explain the architecture"* and the agent will pull from the docs and source code. See [mcp/README.md](mcp/README.md) for setup.
>
> **🐳 Use the dev container.** GCO pins exact versions of a lot of Python packages so CI is reproducible. That makes installing on top of an existing Python environment a frequent source of `ResolutionImpossible` / dependency-resolver errors. **The supported, fast path is the [dev container](#step-1-clone-and-build-the-dev-container)** — it ships Python, Node.js, CDK, kubectl, AWS CLI, and every Python dep at the exact versions CI uses. The host-install path is kept for contributors who specifically want to develop on their host; if you just want to deploy GCO, skip it.

## Table of Contents

- [Prerequisites Check](#prerequisites-check)
- [Step 1: Clone and Build the Dev Container](#step-1-clone-and-build-the-dev-container)
- [Step 2: Run the GCO CLI](#step-2-run-the-gco-cli)
- [Step 3: Bootstrap CDK](#step-3-bootstrap-cdk-optional)
- [Step 4: Deploy Infrastructure](#step-4-deploy-infrastructure)
- [Step 5: Configure Cluster Access](#step-5-configure-cluster-access)
- [Step 6: Run a Test Job](#step-6-run-a-test-job)
- [Step 7: Deploy an Inference Endpoint](#step-7-deploy-an-inference-endpoint-optional)
- [Next Steps](#next-steps)
- [Common Issues](#common-issues)
- [Clean Up](#clean-up)

## Prerequisites Check

The only host-side requirements for the recommended (container) path are AWS credentials and Docker:

```bash
# Verify AWS CLI is configured (or just have ~/.aws populated to mount in)
aws --version
aws sts get-caller-identity

# Verify Docker/Finch is running (Colima also works — see Dockerfile.dev)
docker --version    # or: finch version
docker info         # confirms the daemon is running
```

<details>
<summary>Installing on your host instead? (advanced)</summary>

You'll additionally need:

```bash
# Python 3.10+ (3.14 used in CI)
python3 --version

# Node.js LTS (v24) and CDK CLI
node --version
cdk --version    # npm install -g aws-cdk if missing
```

You should install GCO into a **fresh** virtual environment or via pipx. Mixing it into an existing Python environment will frequently fail dependency resolution because of the project's pinned versions.
</details>

## Step 1: Clone and Build the Dev Container

```bash
# Clone repository
git clone <repository-url>
cd global-capacity-orchestrator-on-aws

# Build the dev container (cached on subsequent runs; ~2 min the first time)
docker build -f Dockerfile.dev -t gco-dev .
```

The image bundles Python 3.14, Node.js 24, CDK, kubectl, AWS CLI, and all GCO Python dependencies at the exact versions CI uses. The Dockerfile is multi-arch — it builds natively on both `linux/amd64` (Intel/x86_64 hosts and CI) and `linux/arm64` (Apple Silicon Macs, Graviton Linux, etc.) by selecting the right kubectl / AWS CLI / Docker CLI binary via `$TARGETARCH`. No `--platform` flag needed.

## Step 2: Run the GCO CLI

The `gco` CLI is pre-installed inside the container. The `docker.sock` mount lets `cdk deploy` bundle Lambda assets through your host's Docker daemon.

```bash
# Drop into an interactive shell with everything wired up
docker run -it --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd):/workspace \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w /workspace \
  gco-dev

# From inside the container
gco --version
```

> **Tip:** save yourself some typing with a shell function on the host. We use a function (rather than a plain alias mounting `$(pwd)`) so that the GCO clone is always mounted at `/workspace` no matter where you call it from — `gco stacks *` and other commands that need `cdk.json` / `app.py` / `gco/` at the workspace root will keep working from any subdirectory of the repo, and from anywhere on disk if you `export GCO_HOME=/path/to/your/clone`:
>
> ```bash
> gco-dev() {
>     local project_root="${GCO_HOME:-$(git rev-parse --show-toplevel 2>/dev/null)}"
>     # Check for both Dockerfile.dev *and* the gco/ namespace package
>     # so we don't accidentally bind-mount an unrelated repo that
>     # happens to have a Dockerfile.dev at its root.
>     if [[ -z "$project_root" \
>         || ! -f "$project_root/Dockerfile.dev" \
>         || ! -d "$project_root/gco" ]]; then
>         echo "gco-dev: not inside the GCO repo. cd into your clone, or set GCO_HOME." >&2
>         return 1
>     fi
>     docker run --rm \
>         -v ~/.aws:/root/.aws:ro \
>         -v "$project_root:/workspace" \
>         -v /var/run/docker.sock:/var/run/docker.sock \
>         -w /workspace \
>         gco-dev "$@"
> }
> # Then run any command directly: gco-dev gco stacks list
> ```
>
> **Colima/Finch users:** the host Docker socket may live somewhere other than `/var/run/docker.sock` — see the header of [`Dockerfile.dev`](Dockerfile.dev) for the right `-v` flag.
>
> **Security note:** mounting `/var/run/docker.sock` gives the container root-equivalent access to your host's Docker daemon. Only use this on trusted hosts.

<details>
<summary>Installing the CLI on your host instead (advanced)</summary>

If you've decided you really want to install on your host (e.g., you're contributing changes to the CLI itself), use a clean isolated environment.

**Option A: pipx (CLI-only):**

```bash
brew install pipx && pipx ensurepath  # macOS

pipx install -e .

gco --version
```

**Option B: pip in a fresh virtualenv (development):**

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

gco --version
```

If pip fails with `ResolutionImpossible` or similar resolver errors, this is the pinned-versions issue called out at the top of this guide. Either start from a fresh venv or switch to the dev container — please don't try to relax the pins on your end.
</details>

## Step 3: Bootstrap CDK (Optional)

CDK bootstrap runs automatically during `deploy` and `deploy-all` if a region hasn't been bootstrapped yet. You can skip this step entirely.

If you prefer to bootstrap manually:

```bash
# Bootstrap CDK in your target region (optional — deploy will do this automatically)
gco stacks bootstrap -r us-east-1
```

## Step 4: Deploy Infrastructure

Run this from inside the dev container shell you started in [Step 2](#step-2-run-the-gco-cli) (or non-interactively, e.g. `gco-dev gco stacks deploy-all -y` using the alias from Step 2):

```bash
# Start Finch VM (if using Finch on the host — Docker Desktop & Colima need no equivalent)
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

### MCP Server (for Cursor / Kiro / LLM integration)

GCO includes an MCP server with 44 tools that wrap the CLI. The dev container already has the `[mcp]` extras installed, so all you need is the client-side config. The most portable form passes an absolute path in `args` (works in Cursor, Kiro, Claude Desktop, etc.):

```jsonc
// ~/.cursor/mcp.json  (or ~/.kiro/settings/mcp.json)
{
  "mcpServers": {
    "gco": {
      "command": "python3",
      "args": ["/absolute/path/to/global-capacity-orchestrator-on-aws/mcp/run_mcp.py"]
    }
  }
}
```

After saving, reload the `gco` server in your MCP client's settings UI so the tool descriptors get picked up. If you're running outside the dev container, install the MCP extras into your venv first: `pip install -e ".[mcp]"`. See [`mcp/README.md`](mcp/README.md) for the full setup including a `cwd`-shorthand variant for Kiro.

## Common Issues

### `pip install` fails with `ResolutionImpossible` or dependency conflicts

GCO pins exact versions of many Python packages (CDK, AWS SDKs, FastAPI, mypy, Ruff, etc.) so CI is reproducible. Installing on top of an existing Python environment frequently triggers resolver errors.

**Fix:** use the [dev container](#step-1-clone-and-build-the-dev-container) — it ships every dep at the correct version and has no overlap with your host Python. If you must install on the host, start from a brand-new virtual environment or use `pipx install -e .` (which gives the CLI its own isolated env).

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
