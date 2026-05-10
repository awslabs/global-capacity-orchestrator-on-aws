# GCO MCP Server

> ⚠️ **Capacity Block purchasing is disabled by default.** The `reserve_capacity` MCP tool can purchase GPU capacity and incur AWS charges. To enable it, set the environment variable `GCO_ENABLE_CAPACITY_PURCHASE=true` in your MCP server config:
>
> ```json
> {
>   "mcpServers": {
>     "gco": {
>       "command": "python3",
>       "args": ["/path/to/global-capacity-orchestrator-on-aws/mcp/run_mcp.py"],
>       "env": {
>         "GCO_ENABLE_CAPACITY_PURCHASE": "true"
>       }
>     }
>   }
> }
> ```

An MCP (Model Context Protocol) server that exposes the GCO CLI as tools for LLM interaction. This lets you manage your multi-region EKS infrastructure through natural language in an AI-powered IDE with MCP support like [Kiro](https://kiro.dev).

## Table of Contents

- [Overview](#overview)
  - [Screenshots](#screenshots)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Available Tools](#available-tools)
  - [Job Management](#job-management)
  - [Capacity](#capacity)
  - [Inference Endpoints](#inference-endpoints)
  - [Cost Tracking](#cost-tracking)
  - [Infrastructure](#infrastructure)
  - [Storage](#storage)
  - [Model Weights](#model-weights)
- [Available Resources](#available-resources)
  - [Documentation](#documentation-docs)
  - [Kubernetes Manifests](#kubernetes-manifests-k8s)
  - [IAM Policies](#iam-policies-iam)
  - [Infrastructure](#infrastructure-infra)
  - [Source Code](#source-code-source)
  - [Demos & Walkthroughs](#demos--walkthroughs-demos)
  - [API Client Examples](#api-client-examples-clients)
  - [Utility Scripts](#utility-scripts-scripts)
  - [Test Suite](#test-suite-tests)
  - [Configuration](#configuration-config)
- [Getting Started with the MCP Server](#getting-started-with-the-mcp-server)
- [Architecture](#architecture)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)

## Overview

The MCP server wraps the `gco` CLI, exposing 44 tools that cover the full lifecycle of GPU workload management:

- Submit and monitor jobs across regions
- Deploy and manage inference endpoints with canary deployments
- Check GPU capacity and get region recommendations
- Track costs by service, region, and workload
- Manage infrastructure stacks and storage

### Screenshots

<details>
<summary>GCO MCP tools connected in Kiro</summary>

![GCO MCP in Kiro](../images/gco_mcp_kiro.png)
</details>

<details>
<summary>Listing stacks via natural language</summary>

![List Stacks](../images/gco_mcp_list_stacks.png)
</details>

<details>
<summary>Checking GPU capacity</summary>

![Check Capacity](../images/gco_mcp_check_capacity.png)
</details>

<details>
<summary>Calculating PI on available capacity</summary>

![Calculating PI](../images/gco_mcp_calculating_pi.png)
</details>

<details>
<summary>PI calculation manifest</summary>

![PI Manifest](../images/pi_calculation_manifest.png)
</details>

<details>
<summary>AI-powered capacity recommendation</summary>

![AI Recommend](../images/gco_mcp_ai_recommend.png)
</details>

<details>
<summary>Viewing cost summary</summary>

![Cost Summary](../images/gco_mcp_cost_summary.png)
</details>

## Prerequisites

The simplest setup is to use GCO's [dev container](../QUICKSTART.md#step-1-clone-and-build-the-dev-container) — it has the `gco` CLI and the `[mcp]` extras (including `fastmcp`) pre-installed at the right versions, so you only need to point your MCP client at `python3 mcp/run_mcp.py` running inside the container. This avoids the dependency-resolver issues that often hit users installing GCO's many pinned packages on top of an existing Python environment.

If you'd rather install on your host:

- Python 3.10+
- GCO CLI installed (`pipx install -e .` from the project root)
- AWS credentials configured (the CLI handles SigV4 auth)
- `fastmcp` package (`pip install -e ".[mcp]"` from the project root, in a fresh venv if possible)

> If `pip install -e ".[mcp]"` errors out with `ResolutionImpossible`, see [Troubleshooting → Installation Issues](../docs/TROUBLESHOOTING.md#pip-install-fails-with-dependency-conflicts).

## Setup

The most portable config — works across Cursor, Kiro, Claude Desktop, and anything else that speaks stdio MCP — passes the **absolute path** to `run_mcp.py` directly in `args`. This avoids relying on any client-specific `cwd` handling.

### Cursor

Add to your MCP config at `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "gco": {
      "command": "python3",
      "args": ["/path/to/global-capacity-orchestrator-on-aws/mcp/run_mcp.py"]
    }
  }
}
```

Replace `/path/to/global-capacity-orchestrator-on-aws` with the absolute path to your GCO clone. After saving, hit the reload icon next to the `gco` server in Cursor → Settings → MCP so the tool descriptors get picked up.

### Kiro

Add to your MCP config at `~/.kiro/settings/mcp.json`. Kiro additionally honors a `cwd` field, so you can either use the absolute-path form above or the `cwd` shorthand:

```json
{
  "mcpServers": {
    "gco": {
      "command": "python3",
      "args": ["mcp/run_mcp.py"],
      "cwd": "/path/to/global-capacity-orchestrator-on-aws"
    }
  }
}
```

If the server fails to start in Kiro, switch to the absolute-path form — `cwd` handling differs between clients.

### Other MCP Clients

The server uses stdio transport (the MCP default). Any MCP client that supports stdio can launch it with:

```bash
python3 /absolute/path/to/global-capacity-orchestrator-on-aws/mcp/run_mcp.py
```

## Available Tools

### Job Management

| Tool | Description |
|------|-------------|
| `list_jobs` | List jobs across GCO clusters (all regions or specific) |
| `submit_job_sqs` | Submit a job via SQS queue (recommended for production) |
| `submit_job_api` | Submit a job via API Gateway with SigV4 auth |
| `get_job` | Get details of a specific job |
| `get_job_logs` | Get logs from a job |
| `get_job_events` | Get Kubernetes events for a job (debugging) |
| `delete_job` | Delete a job |
| `cluster_health` | Get health status of clusters |
| `queue_status` | View SQS queue status (pending, in-flight, DLQ) |

### Capacity

| Tool | Description |
|------|-------------|
| `check_capacity` | Check spot and on-demand capacity for an instance type |
| `capacity_status` | View capacity across all deployed regions |
| `recommend_region` | Get optimal region recommendation (supports instance-type-aware weighted scoring) |
| `spot_prices` | Get current spot prices for an instance type |
| `ai_recommend` | Get AI-powered capacity recommendation using Amazon Bedrock |
| `list_reservations` | List On-Demand Capacity Reservations (ODCRs) across regions |
| `reservation_check` | Check reservation availability and Capacity Block offerings |
| `reserve_capacity` | Purchase a Capacity Block offering by ID (supports dry-run) |

### Inference Endpoints

| Tool | Description |
|------|-------------|
| `deploy_inference` | Deploy an inference endpoint across regions |
| `list_inference_endpoints` | List all inference endpoints |
| `inference_status` | Get detailed status with per-region breakdown |
| `scale_inference` | Scale an endpoint's replica count |
| `update_inference_image` | Rolling update to a new container image |
| `stop_inference` | Stop an endpoint (scales to zero, keeps config) |
| `start_inference` | Start a stopped endpoint |
| `delete_inference` | Delete an endpoint |
| `canary_deploy` | A/B test a new image version with weighted traffic |
| `promote_canary` | Promote canary to primary (100% traffic) |
| `rollback_canary` | Rollback canary (100% traffic to primary) |

### Cost Tracking

| Tool | Description |
|------|-------------|
| `cost_summary` | Total spend broken down by AWS service |
| `cost_by_region` | Cost breakdown by AWS region |
| `cost_trend` | Daily cost trend |
| `cost_forecast` | Forecast costs for the next N days |

### Infrastructure

| Tool | Description |
|------|-------------|
| `list_stacks` | List all GCO CDK stacks |
| `stack_status` | Get detailed CloudFormation stack status |
| `fsx_status` | Check FSx for Lustre configuration |

### Storage

| Tool | Description |
|------|-------------|
| `list_storage_contents` | List contents of shared EFS storage |
| `list_file_systems` | List EFS and FSx file systems |

### Model Weights

| Tool | Description |
|------|-------------|
| `list_models` | List uploaded model weights in S3 |
| `get_model_uri` | Get S3 URI for a model |

## Available Resources

Beyond tools, the MCP server exposes documentation, source code, examples, and operational resources as MCP resources. This means an agent can read GCO's docs, code, manifests, and config on demand to answer in-depth questions about how the platform works.

### Documentation (`docs://`)

| Resource | Description |
|----------|-------------|
| `docs://gco/index` | Browse all available docs, examples, and resource groups |
| `docs://gco/README` | Project README and overview |
| `docs://gco/QUICKSTART` | Quick start guide — deploy in under 60 minutes |
| `docs://gco/CONTRIBUTING` | Contributing guide |
| `docs://gco/docs/{name}` | Any doc by name (ARCHITECTURE, CLI, INFERENCE, CONCEPTS, etc.) |
| `docs://gco/examples/README` | Examples overview with usage instructions |
| `docs://gco/examples/guide` | How to create new job manifests — patterns, metadata, submission methods |
| `docs://gco/examples/{name}` | Example manifests with metadata headers (category, GPU, opt-in, submission) |

### Kubernetes Manifests (`k8s://`)

| Resource | Description |
|----------|-------------|
| `k8s://gco/manifests/index` | List all manifests applied during stack deployment |
| `k8s://gco/manifests/{filename}` | Read a specific manifest (RBAC, NodePools, services, etc.) |

### IAM Policies (`iam://`)

| Resource | Description |
|----------|-------------|
| `iam://gco/policies/index` | List IAM policy templates |
| `iam://gco/policies/{filename}` | Read a policy template (full-access, read-only, namespace-restricted) |

### Infrastructure (`infra://`)

| Resource | Description |
|----------|-------------|
| `infra://gco/index` | Browse Dockerfiles, Helm charts, CI/CD, and security config |
| `infra://gco/dockerfiles/{filename}` | Read a Dockerfile or its README |
| `infra://gco/helm/charts.yaml` | Helm chart versions and configuration |

### CI / GitHub Actions (`ci://`)

Everything under `.github/` — workflows, composite actions, issue/PR templates, scripts, and policy files. Useful when an agent needs to reason about or explain a CI job, debug a workflow failure, or look up which action caused a pipeline step to fail.

| Resource | Description |
|----------|-------------|
| `ci://gco/index` | Browse workflows, composite actions, scripts, templates, and policy files |
| `ci://gco/workflows/{filename}` | Read a workflow YAML (unit-tests.yml, security.yml, cve-scan.yml, etc.) |
| `ci://gco/actions/{name}` | Read a composite action's `action.yml` (e.g. `build-lambda-package`) |
| `ci://gco/scripts/{filename}` | Read a helper script invoked by the workflows (e.g. `dependency-scan.sh`) |
| `ci://gco/templates/{filename}` | Read an issue template or `pull_request_template.md` |
| `ci://gco/codeql/{filename}` | Read CodeQL configuration (query filters, scanned paths) |
| `ci://gco/kind/{filename}` | Read kind-cluster configuration used by integration tests |
| `ci://gco/config/{filename}` | Read a top-level config file (`CI.md`, `CODEOWNERS`, `SECURITY.md`, `release.yml`, `dependabot.yml`) |

### Source Code (`source://`)

| Resource | Description |
|----------|-------------|
| `source://gco/index` | Browse all source files grouped by package |
| `source://gco/config/{filename}` | Project config files (pyproject.toml, cdk.json, .gitlab-ci.yml, linter configs, etc.) |
| `source://gco/file/{path}` | Any source file by relative path |

Source code resources cover `gco/`, `cli/`, `lambda/`, `mcp/`, `scripts/`, `demo/`, and `dockerfiles/`. Build artifacts and caches are filtered out. Path traversal outside the project is blocked.

### Demos & Walkthroughs (`demos://`)

| Resource | Description |
|----------|-------------|
| `demos://gco/index` | Browse demo walkthroughs and scripts |
| `demos://gco/README` | Demo starter kit overview |
| `demos://gco/DEMO_WALKTHROUGH` | Step-by-step infrastructure and jobs demo |
| `demos://gco/INFERENCE_WALKTHROUGH` | End-to-end inference demo (deploy, invoke, scale, autoscale) |
| `demos://gco/LIVE_DEMO` | Automated live demo documentation |
| `demos://gco/{script}` | Demo scripts (live_demo.sh, lib_demo.sh, record_*.sh) |

### API Client Examples (`clients://`)

| Resource | Description |
|----------|-------------|
| `clients://gco/index` | Browse API client examples |
| `clients://gco/README` | Client examples overview, setup, and API reference |
| `clients://gco/python_boto3_example.py` | Python example code with boto3 + SigV4 |
| `clients://gco/aws_cli_examples.sh` | AWS CLI with manual SigV4 signing |
| `clients://gco/curl_sigv4_proxy_example.sh` | curl with aws-sigv4-proxy |

### Utility Scripts (`scripts://`)

| Resource | Description |
|----------|-------------|
| `scripts://gco/index` | Browse utility scripts |
| `scripts://gco/README` | Scripts overview and usage |
| `scripts://gco/setup-cluster-access.sh` | Configure kubectl access to EKS |
| `scripts://gco/bump_version.py` | Version bumping across all locations |
| `scripts://gco/test_cdk_synthesis.py` | CDK synthesis matrix testing |
| `scripts://gco/dump_nag_findings.py` | cdk-nag compliance debugging helper |
| `scripts://gco/test_webhook_delivery.py` | Webhook dispatcher testing |

### Test Suite (`tests://`)

| Resource | Description |
|----------|-------------|
| `tests://gco/index` | Browse test files, infrastructure, and BATS shell tests |
| `tests://gco/README` | Test suite overview, patterns, mocking guide, and coverage requirements |
| `tests://gco/{filepath}` | Read any test file (e.g. `test_mcp_server.py`, `conftest.py`, `BATS/README.md`) |

### Configuration (`config://`)

| Resource | Description |
|----------|-------------|
| `config://gco/index` | Browse CDK configuration, feature toggles, and environment variables |
| `config://gco/cdk.json` | Current CDK deployment configuration |
| `config://gco/feature-toggles` | All feature toggles with their current values and defaults |
| `config://gco/env-vars` | Environment variables used by the MCP server and services |

### Try it

Ask your agent questions like:

- "How does GCO decide which region to recommend for a job?"
- "Walk me through the inference deployment flow"
- "What CDK stacks does GCO create and what's in each one?"
- "How does the manifest processor handle job submissions?"
- "Show me the RBAC configuration applied to the cluster"
- "What IAM policy do I need for read-only access?"
- "How do I set up the live demo?"
- "Show me the Python example for calling the API"

The agent will pull the relevant docs and source code to give you a grounded answer.

## Getting Started with the MCP Server

A great way to get familiar with GCO is through the capacity recommendation system. It touches several core concepts — multi-region awareness, GPU capacity, spot pricing, and job scheduling — and gives you a practical feel for how the platform thinks about workload placement.

Try asking:

1. **"Check GPU capacity for g5.xlarge across all regions"** — this calls `check_capacity` and shows you how GCO queries EC2 spot placement scores, spot price history, and on-demand availability.

2. **"Which region should I use for a GPU job?"** — this triggers `recommend_region`, which aggregates queue depth, GPU utilization, and running job counts across all deployed regions, then ranks them. Pass an instance type (e.g. `g5.xlarge`) for weighted multi-signal scoring that also factors in spot placement scores, pricing trends, and capacity block availability.

3. **"Explain how the capacity recommendation works under the hood"** — the agent will read `cli/capacity/` via the source resources and walk you through the three-layer architecture:
   - `CapacityChecker` — core AWS queries (spot scores, pricing, instance offerings)
   - `MultiRegionCapacityChecker` — cross-region aggregation and weighted scoring
   - `BedrockCapacityAdvisor` — optional AI-powered recommendations via Bedrock

From there, you can branch into job submission, inference deployments, or cost tracking — all through natural conversation.

## Architecture

The MCP server is organized as a modular package under `mcp/`:

```text
mcp/
├── run_mcp.py         — Thin entrypoint (python mcp/run_mcp.py)
├── server.py          — FastMCP instance and instructions
├── audit.py           — Audit logging, sanitization, decorator
├── iam.py             — IAM role assumption
├── cli_runner.py      — _run_cli() subprocess wrapper
├── version.py         — Project version management
├── tools/             — MCP tool definitions (one file per domain)
│   ├── jobs.py        — Job submission, listing, logs, events
│   ├── capacity.py    — Capacity checking, recommendations, reservations
│   ├── inference.py   — Inference deployment, scaling, canary, invocation
│   ├── costs.py       — Cost tracking and forecasting
│   ├── stacks.py      — CDK stack management
│   ├── storage.py     — EFS/FSx file operations
│   └── models.py      — Model weight management
└── resources/         — MCP resource definitions (one file per scheme)
    ├── docs.py        — docs:// (documentation + examples with metadata)
    ├── source.py      — source:// (full source code browser)
    ├── k8s.py         — k8s:// (cluster manifests)
    ├── iam_policies.py — iam:// (IAM policy templates)
    ├── infra.py       — infra:// (Dockerfiles, Helm, CI/CD)
    ├── ci.py          — ci:// (GitHub Actions, workflows)
    ├── demos.py       — demos:// (walkthroughs, scripts)
    ├── clients.py     — clients:// (API client examples)
    ├── scripts.py     — scripts:// (utility scripts)
    ├── tests.py       — tests:// (test suite docs and patterns)
    └── config.py      — config:// (CDK config, feature toggles, env vars)
```

Each tool shells out to the `gco` CLI. This approach:

- Reuses all existing auth (SigV4), error handling, and retry logic
- Stays in sync with CLI updates automatically
- Avoids duplicating complex AWS client setup
- Uses `--output json` for structured responses where supported

```text
LLM ←→ MCP Protocol (stdio) ←→ run_mcp.py ←→ gco CLI ←→ AWS APIs
```

## Examples

Once connected, you can interact naturally:

- "What jobs are running in us-east-1?"
- "Check GPU capacity for g5.xlarge in us-west-2"
- "Deploy a vLLM inference endpoint with 2 GPUs"
- "What's my cost this month?"
- "Scale my-llm endpoint to 3 replicas"
- "Submit examples/simple-job.yaml to the region with the most capacity"

## Troubleshooting

### Server not connecting

1. Verify the path in your MCP config is correct (case-sensitive on macOS)
2. Check that `python3 mcp/run_mcp.py` runs without errors from the project root
3. Ensure `fastmcp` is installed: `pip install -e ".[mcp]"` (from the project root)
4. Ensure `gco` CLI is on your PATH: `which gco`

### Tools returning errors

- Check AWS credentials: `aws sts get-caller-identity`
- Verify infrastructure is deployed: `gco stacks list`
- Check the tool's error message — it includes the CLI's stderr output

### Timeout on long operations

The default timeout is 120 seconds. Stack deployments and destroys exceed this — use the CLI directly for those operations.
