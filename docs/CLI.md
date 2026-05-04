# GCO CLI Reference

Complete command-line interface documentation for GCO (Global Capacity Orchestrator on AWS).

## Table of Contents

- [Installation](#installation)
- [Global Options](#global-options)
- [Commands](#commands)
  - [jobs](#jobs-commands)
  - [queue](#queue-commands)
  - [templates](#templates-commands)
  - [webhooks](#webhooks-commands)
  - [stacks](#stacks-commands)
  - [capacity](#capacity-commands)
  - [inference](#inference-commands)
  - [models](#models-commands)
  - [files](#files-commands)
  - [nodepools](#nodepools-commands)
  - [analytics](#analytics-commands)
- [Configuration](#configuration)
- [Environment Variables](#environment-variables)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)

## Installation

### Using pipx (Recommended)

```bash
# Install pipx if not already installed
brew install pipx && pipx ensurepath  # macOS
# or
pip install pipx && pipx ensurepath   # Linux/Windows

# Install GCO CLI
pipx install -e .
```

### Using pip

```bash
pip install -e .
```

### Verify Installation

```bash
gco --version
gco --help
```

## Global Options

These options are available for all commands:

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to config file |
| `--region` | `-r` | Default AWS region |
| `--output` | `-o` | Output format: `table`, `json`, `yaml` |
| `--verbose` | `-v` | Enable verbose output |
| `--regional-api` | | Use regional API endpoints (for private access) |
| `--help` | | Show help message |
| `--version` | | Show version |

### Regional API Mode

When `--regional-api` is enabled (or `GCO_REGIONAL_API=true` environment variable is set), the CLI routes requests through regional API Gateways instead of the global API Gateway. This is required when:

- The ALB is internal-only (no public exposure)
- Public access is disabled on the EKS cluster
- Maximum security posture is required

```bash
# Use regional API for a single command
gco --regional-api jobs list --region us-east-1

# Or set environment variable for all commands
export GCO_REGIONAL_API=true
gco jobs list --region us-east-1
```

## Commands

### Jobs Commands

Manage jobs across GCO clusters.

#### `gco jobs submit`

Submit a job via API Gateway (SigV4 authenticated).

```bash
gco jobs submit MANIFEST_PATH [OPTIONS]
```

**Arguments:**

- `MANIFEST_PATH` - Path to YAML manifest file

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--namespace` | `-n` | Fallback namespace for manifests that don't declare their own (manifest `metadata.namespace` takes precedence) |
| `--region` | `-r` | Target specific region |
| `--dry-run` | | Validate without applying |
| `--label` | `-l` | Add labels (key=value), can be repeated |
| `--wait` | `-w` | Wait for job completion |
| `--timeout` | | Wait timeout in seconds (default: 3600) |

**Example:**

```bash
gco jobs submit examples/simple-job.yaml -n gco-jobs
gco jobs submit job.yaml --dry-run
gco jobs submit job.yaml -l team=ml -l priority=high
```

#### `gco jobs submit-sqs`

Submit a job via SQS queue (recommended for production).

```bash
gco jobs submit-sqs MANIFEST_PATH [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region for SQS queue |
| `--auto-region` | | Auto-select optimal region based on capacity |
| `--priority` | `-p` | Job priority (0-100, higher = more important) |
| `--namespace` | `-n` | Fallback namespace for manifests that don't declare their own (manifest `metadata.namespace` takes precedence) |

**Example:**

```bash
gco jobs submit-sqs examples/simple-job.yaml --region us-east-1
gco jobs submit-sqs job.yaml --auto-region --priority 10
```

#### `gco jobs submit-direct`

Submit a job directly via kubectl (requires EKS access).

If a job with the same name already exists:

- Completed or failed jobs are silently deleted and replaced
- Active (running/pending) jobs are preserved, and the new submission is auto-renamed with a `-{5char}` suffix

```bash
gco jobs submit-direct MANIFEST_PATH [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region |
| `--namespace` | `-n` | Fallback namespace for manifests that don't declare their own (manifest `metadata.namespace` takes precedence) |

**Example:**

```bash
gco jobs submit-direct examples/simple-job.yaml --region us-east-1 -n gco-jobs
```

#### `gco jobs submit-queue`

Submit a job to the global DynamoDB queue for regional pickup.

```bash
gco jobs submit-queue MANIFEST_PATH [OPTIONS]
```

Jobs are stored in DynamoDB and picked up by the target region's manifest processor. This enables global job submission with centralized tracking and status history.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region for job execution (required) |
| `--namespace` | `-n` | Kubernetes namespace |
| `--priority` | `-p` | Job priority (0-100, higher = more important) |
| `--label` | `-l` | Add labels (key=value), can be repeated |

**Example:**

```bash
gco jobs submit-queue examples/simple-job.yaml --region us-east-1
gco jobs submit-queue job.yaml -r us-west-2 --priority 50
gco jobs submit-queue job.yaml -r us-east-1 -l team=ml -l project=training
```

**Note:** Use `gco queue list` or `gco queue get <job_id>` to track job status.

#### `gco jobs list`

List jobs in GCO clusters.

```bash
gco jobs list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region (required unless --all-regions) |
| `--all-regions` | `-a` | Query all regions via global API |
| `--namespace` | `-n` | Filter by namespace |
| `--status` | `-s` | Filter by status |
| `--limit` | `-l` | Maximum results (default: 50) |

**Example:**

```bash
gco jobs list --region us-east-1
gco jobs list --all-regions
gco jobs list -r us-west-2 -n gco-jobs --status running
```

#### `gco jobs get`

Get details of a specific job.

```bash
gco jobs get JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |

**Example:**

```bash
gco jobs get my-job --region us-east-1
gco jobs get training-job -r us-west-2 -n ml-jobs
```

#### `gco jobs logs`

Get logs from a job.

```bash
gco jobs logs JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |
| `--tail` | `-t` | Number of lines to show |
| `--container` | `-c` | Container name (for multi-container pods) |

**Example:**

```bash
gco jobs logs my-job --region us-east-1
gco jobs logs my-job -r us-east-1 --tail 500
gco jobs logs multi-container-job -r us-east-1 --container sidecar
```

#### `gco jobs delete`

Delete a job.

```bash
gco jobs delete JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco jobs delete my-job --region us-east-1
gco jobs delete old-job -r us-west-2 -n ml-jobs -y
```

#### `gco jobs events`

Get Kubernetes events for a job.

```bash
gco jobs events JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |

**Example:**

```bash
gco jobs events my-job --region us-east-1
gco jobs events training-job -r us-west-2 -n ml-jobs
```

#### `gco jobs pods`

Get pod details for a job.

```bash
gco jobs pods JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |

**Example:**

```bash
gco jobs pods my-job --region us-east-1
gco jobs pods training-job -r us-west-2 -n ml-jobs
```

#### `gco jobs metrics`

Get resource usage metrics for a job.

```bash
gco jobs metrics JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |

**Example:**

```bash
gco jobs metrics my-job --region us-east-1
gco jobs metrics training-job -r us-west-2 -n ml-jobs
```

#### `gco jobs retry`

Retry a failed job.

```bash
gco jobs retry JOB_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Job region (required) |
| `--namespace` | `-n` | Job namespace |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco jobs retry failed-job --region us-east-1
gco jobs retry training-job -r us-west-2 -n ml-jobs -y
```

#### `gco jobs bulk-delete`

Bulk delete jobs based on filters.

```bash
gco jobs bulk-delete [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region (required unless --all-regions) |
| `--all-regions` | `-a` | Delete across all regions |
| `--namespace` | `-n` | Filter by namespace |
| `--status` | `-s` | Filter by status |
| `--older-than-days` | `-d` | Delete jobs older than N days |
| `--label-selector` | `-l` | Kubernetes label selector |
| `--dry-run` | | Only show what would be deleted (default) |
| `--execute` | | Actually delete (disables dry-run) |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco jobs bulk-delete --region us-east-1 --status completed --older-than-days 7
gco jobs bulk-delete -r us-west-2 -n gco-jobs -s failed --execute -y
gco jobs bulk-delete --all-regions --status failed --older-than-days 30 --execute
```

#### `gco jobs health`

Get health status of GCO clusters.

```bash
gco jobs health [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region (required unless --all-regions) |
| `--all-regions` | `-a` | Get health across all regions |

**Example:**

```bash
gco jobs health --region us-east-1
gco jobs health --all-regions
```

#### `gco jobs queue-status`

View SQS queue status across regions.

```bash
gco jobs queue-status [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Filter by region |
| `--all-regions` | | Show all regions |

**Example:**

```bash
gco jobs queue-status --all-regions
gco jobs queue-status -r us-east-1
```

---

### Queue Commands

Manage the global job queue (DynamoDB-backed). The job queue provides centralized job submission and tracking across all regions.

#### `gco queue submit`

Submit a job to the global queue for regional pickup.

```bash
gco queue submit MANIFEST_PATH [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region for job execution (required) |
| `--namespace` | `-n` | Kubernetes namespace |
| `--priority` | `-p` | Job priority (0-100, higher = more important) |
| `--label` | `-l` | Add labels (key=value), can be repeated |

**Example:**

```bash
gco queue submit job.yaml --region us-east-1
gco queue submit job.yaml -r us-west-2 --priority 50
gco queue submit job.yaml -r us-east-1 -l team=ml -l project=training
```

#### `gco queue list`

List jobs in the global queue.

```bash
gco queue list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Filter by target region |
| `--status` | `-s` | Filter by status (queued, claimed, running, succeeded, failed, cancelled) |
| `--namespace` | `-n` | Filter by namespace |
| `--limit` | `-l` | Maximum results (default: 50) |

**Example:**

```bash
gco queue list
gco queue list --region us-east-1 --status queued
gco queue list -s running
```

#### `gco queue get`

Get details of a queued job including status history.

```bash
gco queue get JOB_ID [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to query (any region works) |

**Example:**

```bash
gco queue get abc123-def456
gco queue get abc123-def456 --region us-east-1
```

#### `gco queue cancel`

Cancel a queued job (only works for jobs not yet running).

```bash
gco queue cancel JOB_ID [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--reason` | | Cancellation reason |
| `--region` | `-r` | Region to query (any region works) |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco queue cancel abc123-def456
gco queue cancel abc123-def456 --reason "No longer needed" -y
```

#### `gco queue stats`

Get job queue statistics by region and status.

```bash
gco queue stats [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to query (any region works) |

**Example:**

```bash
gco queue stats
```

---

### Templates Commands

Manage job templates. Templates are reusable job configurations stored in DynamoDB with parameter substitution support.

#### `gco templates list`

List all job templates.

```bash
gco templates list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to query |

**Example:**

```bash
gco templates list
```

#### `gco templates get`

Get details of a specific template.

```bash
gco templates get TEMPLATE_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to query |

**Example:**

```bash
gco templates get gpu-training-template
```

#### `gco templates create`

Create a new job template from a manifest file.

```bash
gco templates create MANIFEST_PATH [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | Template name (required) |
| `--description` | `-d` | Template description |
| `--param` | `-p` | Default parameter (key=value), can be repeated |
| `--region` | `-r` | Region to create in |

**Example:**

```bash
gco templates create job.yaml --name gpu-training -d "GPU training template"
gco templates create job.yaml -n my-template -p image=pytorch:latest -p gpus=4
```

#### `gco templates delete`

Delete a job template.

```bash
gco templates delete TEMPLATE_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco templates delete old-template -y
```

#### `gco templates run`

Create and run a job from a template.

```bash
gco templates run TEMPLATE_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | Job name (required) |
| `--region` | `-r` | Target region (required) |
| `--namespace` | | Kubernetes namespace |
| `--param` | `-p` | Parameter override (key=value), can be repeated |

**Example:**

```bash
gco templates run gpu-training --name my-job --region us-east-1
gco templates run gpu-template -n my-job -r us-east-1 -p image=custom:v1 -p gpus=8
```

---

### Webhooks Commands

Manage webhooks for job event notifications. Webhooks receive HTTP POST notifications when job events occur.

#### `gco webhooks list`

List all registered webhooks.

```bash
gco webhooks list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--namespace` | `-n` | Filter by namespace |
| `--region` | `-r` | Region to query |

**Example:**

```bash
gco webhooks list
gco webhooks list --namespace gco-jobs
```

#### `gco webhooks create`

Register a new webhook for job events.

```bash
gco webhooks create [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--url` | `-u` | Webhook URL (required) |
| `--event` | `-e` | Event type (job.started, job.completed, job.failed), can be repeated |
| `--namespace` | `-n` | Filter events by namespace |
| `--secret` | `-s` | HMAC secret for signature verification |
| `--region` | `-r` | Region to create in |

**Example:**

```bash
gco webhooks create --url https://example.com/webhook -e job.completed -e job.failed
gco webhooks create -u https://slack.com/webhook -e job.failed -n gco-jobs
```

#### `gco webhooks delete`

Delete a webhook.

```bash
gco webhooks delete WEBHOOK_ID [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco webhooks delete abc12345 -y
```

---

### Stacks Commands

Manage CDK infrastructure stacks.

#### `gco stacks list`

List all GCO stacks.

```bash
gco stacks list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Filter by region |
| `--all-regions` | | List from all regions |

#### `gco stacks status`

Get detailed status of a stack.

```bash
gco stacks status STACK_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Stack region |

**Example:**

```bash
gco stacks status gco-us-east-1 --region us-east-1
```

#### `gco stacks deploy`

Deploy a single stack. Automatically bootstraps CDK in the target region if needed.

```bash
gco stacks deploy STACK_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Stack region |
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco stacks deploy gco-us-east-1 -y
```

#### `gco stacks deploy-all`

Deploy all stacks in correct order. Automatically bootstraps CDK in any un-bootstrapped regions before deploying.

```bash
gco stacks deploy-all [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |
| `--parallel` | `-p` | Deploy regional stacks in parallel |
| `--max-workers` | `-w` | Max parallel workers (default: 4) |

**Example:**

```bash
gco stacks deploy-all -y
gco stacks deploy-all -y --parallel --max-workers 8
```

#### `gco stacks destroy`

Destroy a single stack.

```bash
gco stacks destroy STACK_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Stack region |
| `--yes` | `-y` | Skip confirmation |

#### `gco stacks destroy-all`

Destroy all stacks in correct order.

```bash
gco stacks destroy-all [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |
| `--parallel` | `-p` | Destroy regional stacks in parallel |
| `--max-workers` | `-w` | Max parallel workers (default: 4) |

#### `gco stacks bootstrap`

Bootstrap CDK in a region. This is run automatically by `deploy` and `deploy-all` when needed, so manual bootstrapping is optional.

```bash
gco stacks bootstrap [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to bootstrap |

#### `gco stacks access`

Configure kubectl access to a GCO EKS cluster. Updates kubeconfig, creates an EKS access entry for your IAM principal, and associates the cluster admin policy. Handles assumed roles automatically.

```bash
gco stacks access [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--cluster` | `-c` | Cluster name (default: gco-{region}) |
| `--region` | `-r` | AWS region (default: first deployment region) |

**Examples:**

```bash
gco stacks access                             # Auto-detect region from cdk.json
gco stacks access -r us-west-2                # Specific region
gco stacks access -c my-cluster -r eu-west-1  # Custom cluster name
```

#### `gco stacks fsx`

Manage FSx for Lustre storage.

```bash
gco stacks fsx COMMAND [OPTIONS]
```

**Subcommands:**

- `status` - Show FSx status
- `enable` - Enable FSx for Lustre
- `disable` - Disable FSx for Lustre

**Example:**

```bash
gco stacks fsx status
gco stacks fsx enable --storage-capacity 1200 -y
gco stacks fsx disable -y
```

#### `gco stacks valkey`

Manage Valkey Serverless cache.

```bash
gco stacks valkey COMMAND [OPTIONS]
```

**Subcommands:**

- `status` - Show Valkey configuration status
- `enable` - Enable Valkey Serverless cache
- `disable` - Disable Valkey Serverless cache

**Example:**

```bash
gco stacks valkey status
gco stacks valkey enable --max-storage 10 --max-ecpu 10000 -y
gco stacks valkey disable -y
```

#### `gco stacks aurora`

Manage Aurora PostgreSQL (pgvector) database.

```bash
gco stacks aurora COMMAND [OPTIONS]
```

**Subcommands:**

- `status` - Show Aurora pgvector configuration status
- `enable` - Enable Aurora Serverless v2 with pgvector
- `disable` - Disable Aurora pgvector

**Example:**

```bash
gco stacks aurora status
gco stacks aurora enable --min-acu 2 --max-acu 32 --deletion-protection -y
gco stacks aurora disable -y
```

---

### DAG Commands

Run multi-step job pipelines with dependencies. Define a DAG in YAML, and GCO runs steps in dependency order, skipping downstream steps if a dependency fails.

#### `gco dag run`

Execute a DAG pipeline.

```bash
gco dag run DAG_FILE [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to run in (default: from DAG file or first deployed) |
| `--timeout` | `-t` | Timeout per step in seconds (default: 3600) |
| `--dry-run` | | Validate and show execution order without running |

**Examples:**

```bash
# Run a pipeline
gco dag run pipeline.yaml -r us-east-1

# Preview execution order
gco dag run pipeline.yaml --dry-run
```

#### `gco dag validate`

Validate a DAG definition without running it. Checks for cycles, missing dependencies, and missing manifest files.

```bash
gco dag validate DAG_FILE
```

**Example:**

```bash
gco dag validate examples/pipeline-dag.yaml
```

#### DAG File Format

```yaml
name: my-pipeline
region: us-east-1          # optional, auto-detects if omitted
namespace: gco-jobs    # optional, defaults to gco-jobs

steps:
  - name: preprocess
    manifest: examples/preprocess-job.yaml

  - name: train
    manifest: examples/train-job.yaml
    depends_on: [preprocess]

  - name: evaluate
    manifest: examples/evaluate-job.yaml
    depends_on: [train]
```

Steps without `depends_on` run first. Steps with dependencies wait until all dependencies succeed. If a step fails, all downstream steps are automatically skipped.

Use shared EFS storage (`/mnt/shared`) to pass data between steps.

---

### Costs Commands

View cost breakdowns and estimates for GCO resources. Uses AWS Cost Explorer filtered by the `Project: GCO` tag applied to all resources.

**Setup (one-time):** To filter costs by the `Project` tag, you must activate cost allocation tags in your AWS account:

1. Go to the [AWS Billing Console → Cost Allocation Tags](https://us-east-1.console.aws.amazon.com/billing/home#/tags)
2. Search for the `Project` tag under "User-defined cost allocation tags"
3. Select it and click "Activate"
4. Wait ~24 hours for tag data to appear in Cost Explorer

Until the tag is activated, use `--all` to see total account costs:

```bash
gco costs summary --all
```

You can also activate the `Environment` and `Owner` tags for more granular filtering in the AWS Cost Explorer console.

#### `gco costs summary`

Show total GCO spend broken down by AWS service.

```bash
gco costs summary [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--days` | `-d` | Number of days to look back (default: 30) |
| `--all` | | Show all account costs, not filtered by GCO tag |

**Examples:**

```bash
# Last 30 days (default)
gco costs summary

# Last 7 days
gco costs summary --days 7

# All account costs (before tags are activated)
gco costs summary --all

# JSON output
gco --output json costs summary
```

#### `gco costs regions`

Show cost breakdown by AWS region.

```bash
gco costs regions [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--days` | `-d` | Number of days to look back (default: 30) |

**Examples:**

```bash
gco costs regions
gco costs regions --days 7
```

#### `gco costs trend`

Show daily cost trend with a visual bar chart.

```bash
gco costs trend [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--days` | `-d` | Number of days to show (default: 14) |
| `--all` | | Show all account costs, not filtered by GCO tag |

**Examples:**

```bash
gco costs trend
gco costs trend --days 7
gco costs trend --all
```

#### `gco costs workloads`

Estimate costs for currently running workloads (jobs and inference endpoints) based on instance pricing and runtime.

```bash
gco costs workloads [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region to check (default: all deployment regions) |

**Examples:**

```bash
# All regions
gco costs workloads

# Specific region
gco costs workloads -r us-east-1
```

#### `gco costs forecast`

Forecast GCO costs for the next N days based on historical spending patterns.

```bash
gco costs forecast [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--days` | `-d` | Days to forecast ahead (default: 30) |

**Examples:**

```bash
gco costs forecast
gco costs forecast --days 60
```

> **Note:** Cost Explorer needs at least 14 days of historical data to generate forecasts.

---

### Capacity Commands

Check and manage cluster capacity.

#### `gco capacity check`

Check capacity for a specific instance type.

```bash
gco capacity check [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--instance-type` | `-i` | Instance type to check |
| `--region` | `-r` | Region to check |
| `--type` | `-t` | Capacity type: `spot`, `on-demand`, or `both` |

**Example:**

```bash
gco capacity check --instance-type g4dn.xlarge --region us-east-1
gco capacity check -i g5.xlarge -r us-west-2 -t spot
```

#### `gco capacity status`

View capacity status across regions.

```bash
gco capacity status [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Filter by region |

#### `gco capacity recommend`

Get capacity recommendation for an instance type.

```bash
gco capacity recommend [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--instance-type` | `-i` | Instance type |
| `--region` | `-r` | Region |

#### `gco capacity recommend-region`

Get optimal region recommendation.

```bash
gco capacity recommend-region [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--gpu` | | Recommend for GPU workloads |
| `--instance-type` | `-i` | Specific instance type (enables weighted scoring) |
| `--gpu-count` | | Number of GPUs required |
| `--min-gpus` | | Minimum GPUs required |

When `--instance-type` is provided, the recommendation uses weighted multi-signal
scoring that combines spot placement scores, spot-vs-on-demand pricing, queue depth,
GPU utilization, and running job counts. Without it, a simpler composite score is used.

**Example:**

```bash
gco capacity recommend-region --gpu
gco capacity recommend-region -i g5.xlarge
gco capacity recommend-region -i p4d.24xlarge --gpu-count 8
```

#### `gco capacity ai-recommend`

Get AI-powered capacity recommendation using Amazon Bedrock.

⚠️ **DISCLAIMER**: Recommendations are AI-generated and should be validated before making production decisions. Capacity availability and pricing can change rapidly.

```bash
gco capacity ai-recommend [OPTIONS]
```

This command gathers comprehensive capacity data including:

- Spot placement scores and pricing across regions
- On-demand availability and pricing
- Current cluster utilization (queue depth, GPU/CPU usage)
- Running and pending job counts

The data is analyzed by an LLM (Claude by default) to provide intelligent recommendations.

**Requirements:**

- AWS credentials with `bedrock:InvokeModel` permission
- The specified Bedrock model must be enabled in your account

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--workload` | `-w` | Description of your workload |
| `--instance-type` | `-i` | Instance types to consider (can specify multiple) |
| `--region` | `-r` | Regions to consider (can specify multiple) |
| `--gpu` | | Workload requires GPUs |
| `--min-gpus` | | Minimum GPUs required |
| `--min-memory-gb` | | Minimum memory in GB |
| `--fault-tolerance` | `-f` | Fault tolerance level: `high`, `medium`, `low` |
| `--max-cost` | | Maximum cost per hour in USD |
| `--model` | `-m` | Bedrock model ID to use |
| `--raw` | | Show raw AI response |

**Example:**

```bash
# Basic recommendation
gco capacity ai-recommend --workload "Training a large language model"

# GPU workload with specific requirements
gco capacity ai-recommend -w "Inference workload" --gpu --min-gpus 4

# Compare specific instance types and regions
gco capacity ai-recommend -i g5.xlarge -i g5.2xlarge -r us-east-1 -r us-west-2

# Cost-constrained recommendation
gco capacity ai-recommend --fault-tolerance high --max-cost 5.00

# Use a different model
gco capacity ai-recommend -w "ML training" --model us.anthropic.claude-3-haiku-20240307-v1:0
```

#### `gco capacity reservations`

List On-Demand Capacity Reservations (ODCRs) across deployed regions.

```bash
gco capacity reservations [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-i, --instance-type` | Filter by instance type |
| `-r, --region` | Specific region (default: all deployed regions) |

```bash
# List all active reservations
gco capacity reservations

# Filter by instance type
gco capacity reservations -i p5.48xlarge

# Check a specific region
gco capacity reservations -r us-east-1
```

#### `gco capacity reservation-check`

Check reservation availability and Capacity Block offerings for ML workloads. Checks both existing ODCRs and purchasable Capacity Blocks (guaranteed GPU capacity for a fixed duration at a known price).

```bash
gco capacity reservation-check [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-i, --instance-type` | Instance type to check (required) |
| `-r, --region` | Specific region (default: all deployed regions) |
| `-c, --count` | Minimum instances needed (default: 1) |
| `--include-blocks/--no-blocks` | Include Capacity Block offerings (default: yes) |
| `--block-duration` | Capacity Block duration in hours (default: 24) |

```bash
# Check for p5.48xlarge reservations and block offerings
gco capacity reservation-check -i p5.48xlarge

# Check with specific count and duration
gco capacity reservation-check -i p4d.24xlarge -c 2 --block-duration 48

# ODCRs only, no block offerings
gco capacity reservation-check -i g5.48xlarge -r us-east-1 --no-blocks
```

---

### Inference Commands

Manage multi-region inference endpoints. Endpoints are stored in DynamoDB and reconciled by the `inference_monitor` in each target region.

See [Inference Guide](INFERENCE.md) for architecture details and workflows.

#### `gco inference deploy`

Deploy an inference endpoint to one or more regions.

```bash
gco inference deploy ENDPOINT_NAME [OPTIONS]
```

**Arguments:**

- `ENDPOINT_NAME` - Unique name for the endpoint

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--image` | `-i` | Container image (required) |
| `--region` | `-r` | Target region(s), repeatable (default: all deployed regions) |
| `--replicas` | | Replicas per region (default: 1) |
| `--gpu-count` | | GPUs per replica (default: 1) |
| `--gpu-type` | | GPU instance type hint (e.g. g5.xlarge) |
| `--port` | | Container port (default: 8000) |
| `--model-path` | | EFS path for model weights |
| `--model-source` | | S3 URI for model weights (auto-synced via init container) |
| `--health-path` | | Health check endpoint path (default: /health) |
| `--env` | `-e` | Environment variable (KEY=VALUE), repeatable |
| `--namespace` | `-n` | Kubernetes namespace (default: gco-inference) |
| `--label` | `-l` | Label (key=value), repeatable |
| `--min-replicas` | | Autoscaling: minimum replicas |
| `--max-replicas` | | Autoscaling: maximum replicas |
| `--autoscale-metric` | | Autoscaling metric (e.g. `cpu:70`, `memory:80`), repeatable. Enables HPA. |
| `--capacity-type` | | Node capacity type: `on-demand` (default) or `spot` |
| `--accelerator` | `nvidia` | Accelerator type: `nvidia` for GPU instances, `neuron` for Trainium/Inferentia |
| `--node-selector` | | Node selector (key=value), repeatable. E.g. `eks.amazonaws.com/instance-family=inf2` |
| `--extra-args` | | Extra arguments passed to the container (e.g. `--kv-transfer-config {...}`). Repeatable |

**Example:**

```bash
gco inference deploy my-llm -i vllm/vllm-openai:v0.20.0
gco inference deploy llama3-70b \
  -i vllm/vllm-openai:v0.20.0 \
  -r us-east-1 -r eu-west-1 \
  --replicas 2 --gpu-count 4 \
  --model-source s3://bucket/models/llama3-70b \
  -e MODEL=/models/llama3-70b

# Deploy with autoscaling (creates a Kubernetes HPA)
gco inference deploy my-llm \
  -i vllm/vllm-openai:v0.20.0 \
  --replicas 2 --gpu-count 1 \
  --min-replicas 1 --max-replicas 8 \
  --autoscale-metric cpu:70 --autoscale-metric memory:80
```

#### `gco inference list`

List inference endpoints.

```bash
gco inference list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--state` | `-s` | Filter by state (deploying, running, stopped, deleted) |
| `--region` | `-r` | Filter by target region |

**Example:**

```bash
gco inference list
gco inference list --state running
gco inference list -r us-east-1
```

#### `gco inference status`

Show detailed status of an inference endpoint including per-region sync state.

```bash
gco inference status ENDPOINT_NAME
```

**Example:**

```bash
gco inference status my-llm
```

#### `gco inference scale`

Scale an inference endpoint to a new replica count (applied across all target regions).

```bash
gco inference scale ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--replicas` | `-r` | New replica count (required) |

**Example:**

```bash
gco inference scale my-llm --replicas 4
```

#### `gco inference stop`

Stop an inference endpoint (scales to zero, keeps configuration).

```bash
gco inference stop ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco inference stop my-llm -y
```

#### `gco inference start`

Start a stopped inference endpoint.

```bash
gco inference start ENDPOINT_NAME
```

**Example:**

```bash
gco inference start my-llm
```

#### `gco inference delete`

Delete an inference endpoint from all regions. The inference_monitor in each region cleans up K8s resources.

```bash
gco inference delete ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco inference delete my-llm -y
```

#### `gco inference update-image`

Update the container image for an endpoint. Triggers a rolling update across all target regions.

```bash
gco inference update-image ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--image` | `-i` | New container image (required) |

**Example:**

```bash
gco inference update-image my-llm -i vllm/vllm-openai:v0.20.0
```

#### `gco inference invoke`

Send a request to an inference endpoint via the API Gateway. Auto-detects the framework (vLLM, TGI, Triton) and builds the appropriate request body.

```bash
gco inference invoke ENDPOINT_NAME [OPTIONS]
```

**Arguments:**

- `ENDPOINT_NAME` - Name of the inference endpoint

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--prompt` | `-p` | Text prompt to send |
| `--data` | `-d` | Raw JSON body (overrides --prompt) |
| `--path` | | API sub-path (default: auto-detected from image) |
| `--region` | `-r` | Target region for the request |
| `--max-tokens` | | Max tokens to generate (default: 100) |
| `--stream/--no-stream` | | Stream the response |

**Example:**

```bash
# Simple prompt (auto-detects vLLM OpenAI-compatible format)
gco inference invoke my-llm -p "What is GPU orchestration?"

# With max tokens
gco inference invoke my-llm -p "Explain Kubernetes" --max-tokens 200

# Raw JSON body
gco inference invoke my-llm -d '{"prompt": "Hello", "max_tokens": 50}'

# Explicit API path
gco inference invoke my-llm -p "Hello" --path /v1/chat/completions
```

#### `gco inference health`

Check if an inference endpoint is healthy and ready to serve requests. Hits the endpoint's health check path and reports HTTP status and round-trip latency.

```bash
gco inference health ENDPOINT_NAME [OPTIONS]
```

**Arguments:**

- `ENDPOINT_NAME` - Name of the inference endpoint

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region to check |

**Example:**

```bash
# Check health (nearest region via Global Accelerator)
gco inference health my-llm

# Check health in a specific region
gco inference health my-llm -r us-east-1
```

#### `gco inference models`

List models loaded on an inference endpoint. Queries the `/v1/models` path (OpenAI-compatible) to discover which models are available.

```bash
gco inference models ENDPOINT_NAME [OPTIONS]
```

**Arguments:**

- `ENDPOINT_NAME` - Name of the inference endpoint

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Target region to query |

**Example:**

```bash
# List loaded models
gco inference models my-llm

# Query a specific region
gco inference models my-llm -r eu-west-1
```

#### `gco inference canary`

Start a canary deployment with a new image. Routes a percentage of traffic to the canary while the primary continues serving the rest.

```bash
gco inference canary ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--image` | `-i` | New container image for canary (required) |
| `--weight` | `-w` | Percentage of traffic to canary, 1-99 (default: 10) |
| `--replicas` | `-r` | Number of canary replicas (default: 1) |

**Examples:**

```bash
# 10% traffic to new version
gco inference canary my-llm -i vllm/vllm-openai:v0.20.0

# 25% traffic with 2 canary replicas
gco inference canary my-llm -i vllm/vllm-openai:v0.20.0 -w 25 -r 2
```

#### `gco inference promote`

Promote the canary to primary. Replaces the primary image with the canary image and removes the canary deployment. All traffic goes to the new image.

```bash
gco inference promote ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco inference promote my-llm -y
```

#### `gco inference rollback`

Remove the canary deployment, keeping the primary unchanged. All traffic returns to the primary.

```bash
gco inference rollback ENDPOINT_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco inference rollback my-llm -y
```

---

### Models Commands

Manage model weights in the central S3 bucket. Models uploaded here are automatically available to inference endpoints across all regions via init container sync.

See [Inference Guide](INFERENCE.md) for details on model weight management.

#### `gco models upload`

Upload model weights to the central S3 bucket.

```bash
gco models upload LOCAL_PATH [OPTIONS]
```

**Arguments:**

- `LOCAL_PATH` - Local file or directory path

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | Model name in the registry (required) |

**Example:**

```bash
gco models upload ./my-model-weights/ --name llama3-8b
gco models upload ./weights.safetensors --name my-model
```

#### `gco models list`

List models in the central S3 bucket.

```bash
gco models list
```

**Example:**

```bash
gco models list
```

#### `gco models delete`

Delete a model and all its files from the S3 bucket.

```bash
gco models delete MODEL_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip confirmation |

**Example:**

```bash
gco models delete llama3-8b -y
```

#### `gco models uri`

Get the S3 URI for a model (for use with `--model-source` in inference deploy).

```bash
gco models uri MODEL_NAME
```

**Example:**

```bash
gco models uri llama3-8b
# Output: s3://gco-models-xxx/models/llama3-8b
```

---

### Files Commands

Manage file systems and download job outputs.

#### `gco files list` / `gco files ls`

List files on shared storage.

```bash
gco files list [OPTIONS]
gco files ls [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region |
| `--type` | `-t` | Storage type: `efs` or `fsx` |
| `--path` | `-p` | Path to list |

**Example:**

```bash
gco files ls -r us-east-1
gco files list -r us-east-1 -t fsx -p /scratch
```

#### `gco files download`

Download files from shared storage.

```bash
gco files download REMOTE_PATH LOCAL_PATH [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region |
| `--type` | `-t` | Storage type: `efs` or `fsx` |

**Example:**

```bash
gco files download my-job/outputs ./results -r us-east-1
gco files download training-run ./checkpoints -r us-west-2 -t fsx
```

---

### Nodepools Commands

Manage Karpenter NodePools.

#### `gco nodepools list`

List NodePools in a cluster.

```bash
gco nodepools list [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region |

#### `gco nodepools describe`

Describe a specific NodePool.

```bash
gco nodepools describe NODEPOOL_NAME [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | Region |

#### `gco nodepools create-odcr`

Generate NodePool manifest for ODCR (On-Demand Capacity Reservation).

```bash
gco nodepools create-odcr [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | NodePool name |
| `--capacity-reservation-id` | | ODCR ID |
| `--instance-type` | `-i` | Instance type |
| `--output` | `-o` | Output file path |

**Example:**

```bash
gco nodepools create-odcr \
  --name gpu-reserved \
  --capacity-reservation-id cr-0123456789abcdef0 \
  --instance-type g5.xlarge \
  --output nodepool.yaml
```

---

### Analytics Commands

Manage the optional GCO analytics environment (SageMaker Studio + EMR
Serverless + Cognito). The feature is **off by default**; enable it only
when you want interactive notebook analytics. See the
[Analytics Guide](ANALYTICS.md) for end-to-end workflows.

All `gco analytics *` commands auto-discover the Cognito user-pool ID
and API Gateway endpoint from the `gco-analytics` and `gco-api-gateway`
CloudFormation outputs, so no manual ID wiring is needed.

#### `gco analytics enable`

Flip `analytics_environment.enabled` to `true` in `cdk.json`. Prints
the follow-up `gco stacks deploy gco-analytics` command — does not
deploy automatically.

```bash
gco analytics enable [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--hyperpod` | | Also set `analytics_environment.hyperpod.enabled=true` (adds HyperPod training-job permissions to the SageMaker execution role). |
| `--yes` | `-y` | Skip the confirmation prompt. |

**Example:**

```bash
gco analytics enable
gco analytics enable --hyperpod
gco analytics enable --hyperpod -y

# Follow-up to actually deploy the stack:
gco stacks deploy gco-analytics
```

#### `gco analytics disable`

Flip `analytics_environment.enabled` to `false` in `cdk.json`. Leaves
the `hyperpod`, `cognito`, and `efs` sub-blocks untouched so a later
`enable` preserves your preferences. Run `gco stacks destroy
gco-analytics` afterward to tear down the deployed resources.

```bash
gco analytics disable [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--yes` | `-y` | Skip the confirmation prompt. |

**Example:**

```bash
gco analytics disable
gco analytics disable -y
gco stacks destroy gco-analytics
```

#### `gco analytics status`

Show the current `analytics_environment.*` toggle state from `cdk.json`
plus the deployment state of `gco-analytics`.

```bash
gco analytics status
```

**Example:**

```bash
gco analytics status
```

#### `gco analytics users add`

Create a Cognito user in the analytics user pool. Calls
`cognito-idp:AdminCreateUser` and prints the temporary password to
stdout exactly once.

```bash
gco analytics users add [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--username` | Cognito username to create (required). |
| `--email` | Email address for the new user. |
| `--no-email` | Suppress the Cognito welcome email (`MessageAction=SUPPRESS`). |

**Example:**

```bash
gco analytics users add --username alice --email alice@example.com
gco analytics users add --username bob --email bob@example.com --no-email
```

#### `gco analytics users list`

List Cognito users in the analytics user pool. Default output is a
formatted table via the existing `OutputFormatter`.

```bash
gco analytics users list [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--as-json` | Emit JSON instead of a table. |

**Example:**

```bash
gco analytics users list
gco analytics users list --as-json
```

#### `gco analytics users remove`

Delete a Cognito user from the analytics user pool. Does not delete
the user's Studio user profile or EFS home folder — use
`aws sagemaker delete-user-profile` for that.

```bash
gco analytics users remove [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--username` | Cognito username to remove (required). |
| `--yes` | Skip the confirmation prompt. |

**Example:**

```bash
gco analytics users remove --username alice
gco analytics users remove --username alice --yes
```

#### `gco analytics studio login`

Sign in to SageMaker Studio via Cognito SRP and print a presigned
Studio URL on its own line on stdout (pipe-friendly). The password,
`IdToken`, and URL are never written to disk.

```bash
gco analytics studio login [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--username` | Cognito username (required). |
| `--password` | Password. Defaults to prompt (`click.prompt(..., hide_input=True)`). Also read from `$GCO_STUDIO_PASSWORD` if set. |
| `--api-url` | Override the API Gateway base URL (otherwise auto-discovered from CloudFormation). |
| `--open` | Launch the default browser on the presigned URL after printing it. |

**Example:**

```bash
# Interactive (prompts for password)
gco analytics studio login --username alice

# Non-interactive
export GCO_STUDIO_PASSWORD='...'
gco analytics studio login --username alice

# Open browser automatically
gco analytics studio login --username alice --open

# Custom API endpoint
gco analytics studio login \
  --username alice \
  --api-url https://abc123.execute-api.us-east-2.amazonaws.com
```

#### `gco analytics doctor`

Run pre-flight checks before `gco stacks deploy gco-analytics`. Each
check prints `✓`/`✗` plus a short remediation line. Exits `1` on any
failing check.

Checks performed:

- `cdk.json` is present and parses as JSON
- `gco-global`, `gco-api-gateway`, and every regional stack are
  `CREATE_COMPLETE`
- The three `/gco/cluster-shared-bucket/*` SSM parameters are
  present in the global region
- No orphaned retained analytics resources are left from a previous
  `retain`-policy destroy

```bash
gco analytics doctor
```

**Example:**

```bash
gco analytics doctor
```

#### `gco analytics iterate`

Thin wrapper over `scripts/test_analytics_lifecycle.py` that drives
the analytics deploy → test → destroy → verify-clean iteration loop.
Exits with the underlying script's return code. Never touches
`gco-global`, `gco-api-gateway`, `gco-<region>`, or `gco-monitoring` —
the loop is scoped strictly to `gco-analytics`.

```bash
gco analytics iterate PHASE [OPTIONS]
```

**Arguments:**

- `PHASE` - One of `status`, `deploy`, `test`, `destroy`,
  `verify-clean`, or `all`.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--region` | `-r` | AWS region (default: `deployment_regions.api_gateway` from `cdk.json`). |
| `--dry-run` | | Print the planned action without executing it. |
| `--json` | | Emit machine-readable JSON instead of human-readable text. |

**Example:**

```bash
# Check current state without changing anything
gco analytics iterate status --dry-run --json

# Individual phases
gco analytics iterate deploy
gco analytics iterate test
gco analytics iterate destroy
gco analytics iterate verify-clean

# Full cycle
gco analytics iterate all

# Target a specific region
gco analytics iterate deploy -r us-east-2
```

---

## Configuration

### Config File

Create `~/.gco/config.yaml`:

```yaml
default_region: us-east-1
output_format: table
verbose: false
regions:
  - us-east-1
  - us-west-2
  - eu-west-1
```

### cdk.json

Project configuration in `cdk.json`:

```json
{
  "context": {
    "project_name": "gco",
    "deployment_regions": {
      "global": "us-east-2",
      "api_gateway": "us-east-2",
      "monitoring": "us-east-2",
      "regional": ["us-east-1", "us-west-2"]
    },
    "resource_thresholds": {
      "cpu_threshold": 60,
      "memory_threshold": 60,
      "gpu_threshold": -1,
      "pending_pods_threshold": 10,
      "pending_requested_cpu_vcpus": 100,
      "pending_requested_memory_gb": 200,
      "pending_requested_gpus": -1
    },
    "fsx_lustre": {
      "enabled": false,
      "storage_capacity_gib": 1200
    }
  }
}
```

Set any threshold to `-1` to disable that health check. This is useful when running GPU inference endpoints that naturally saturate GPU resources.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | Default AWS region |
| `AWS_PROFILE` | AWS credentials profile |
| `GCO_CONFIG` | Path to config file |
| `GCO_REGIONAL_API` | Use regional API endpoints (`true`/`false`) |
| `CDK_DOCKER` | Docker command (`docker` or `finch`) |

## Examples

### Complete Workflow

```bash
# 1. Deploy (bootstrap runs automatically if needed)
export CDK_DOCKER=finch
gco stacks deploy-all -y

# 2. Check capacity
gco capacity status
gco capacity recommend-region --gpu

# 3. Submit jobs
gco jobs submit-sqs examples/simple-job.yaml --region us-east-1
gco jobs queue-status --all-regions

# 4. Monitor jobs
gco jobs list --all-regions
gco jobs logs my-job -r us-east-1 -n gco-jobs

# 5. Download outputs
gco files ls -r us-east-1
gco files download my-job/outputs ./results -r us-east-1

# 6. Cleanup
gco stacks destroy-all -y
```

### Inference Endpoint Workflow

```bash
# 1. Upload model weights
gco models upload ./llama3-weights/ --name llama3-8b

# 2. Deploy inference endpoint
gco inference deploy my-llm \
  -i vllm/vllm-openai:v0.20.0 \
  --gpu-count 1 \
  --model-source $(gco models uri llama3-8b) \
  -e MODEL=/models/my-llm \
  -r us-east-1

# 3. Monitor deployment
gco inference status my-llm

# 4. Scale for production
gco inference scale my-llm --replicas 3

# Or enable autoscaling
gco inference deploy my-llm \
  -i vllm/vllm-openai:v0.20.0 \
  --replicas 2 --gpu-count 1 \
  --min-replicas 1 --max-replicas 8 \
  --autoscale-metric cpu:70

# 5. Rolling update
gco inference update-image my-llm -i vllm/vllm-openai:v0.20.0

# 6. Cleanup
gco inference delete my-llm -y
gco models delete llama3-8b -y
```

### GPU Job Submission

```bash
# Check GPU capacity
gco capacity check -i g5.xlarge -r us-east-1

# Submit GPU job
gco jobs submit-sqs examples/gpu-job.yaml --auto-region

# Monitor
gco jobs list --all-regions
gco jobs logs gpu-test-job -r us-east-1 -n gco-jobs
```

### Multi-Region Deployment

```bash
# Deploy to multiple regions
gco stacks deploy-all -y --parallel --max-workers 4

# Check status across regions
gco stacks list --all-regions
gco capacity status
```

## Troubleshooting

### Common Issues

**"No credentials found"**

```bash
# Ensure AWS credentials are configured
aws sts get-caller-identity
```

**"Endpoint request timed out"**

- Wait 1-2 minutes after deployment for ALB targets to become healthy
- Use `submit-sqs` or `submit-direct` instead of `submit`

**"kubectl access denied"**

- Add your IAM principal to EKS access entries:

```bash
aws eks create-access-entry \
  --cluster-name gco-us-east-1 \
  --principal-arn arn:aws:iam::ACCOUNT:user/YOUR-USER \
  --region us-east-1

aws eks associate-access-policy \
  --cluster-name gco-us-east-1 \
  --principal-arn arn:aws:iam::ACCOUNT:user/YOUR-USER \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster \
  --region us-east-1
```

**"CDK bootstrap required"**

This should resolve automatically — `deploy` and `deploy-all` auto-bootstrap un-bootstrapped regions. If it persists:

```bash
gco stacks bootstrap --region us-east-1
```

### Debug Mode

```bash
# Enable verbose output
gco -v jobs list --all-regions

# Check AWS configuration
aws sts get-caller-identity
aws eks list-clusters --region us-east-1
```

---

For more help, see:

- [Troubleshooting Guide](TROUBLESHOOTING.md)
- [Inference Guide](INFERENCE.md)
- [Architecture Documentation](ARCHITECTURE.md)
- [Customization Guide](CUSTOMIZATION.md)
