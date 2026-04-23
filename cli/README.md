# CLI

The `gco` command-line interface for managing GCO infrastructure, jobs, inference endpoints, and operations.

## Structure

| File | Description |
|------|-------------|
| `main.py` | CLI entry point and top-level command group registration |
| `aws_client.py` | AWS SDK client wrapper with region discovery and credential handling |
| `config.py` | CLI configuration loader (cdk.json, env vars, user config) |
| `output.py` | Output formatting (table, JSON, YAML) |
| `jobs.py` | Job submission, listing, logs, and lifecycle management |
| `inference.py` | Inference endpoint deployment, scaling, canary, and invocation |
| `models.py` | Model weight upload, listing, and S3 URI management |
| `stacks.py` | CDK stack deployment, destruction, and status |
| `costs.py` | Cost tracking via AWS Cost Explorer |
| `dag.py` | DAG pipeline execution with dependency ordering |
| `files.py` | EFS/FSx file listing and download |
| `nodepools.py` | Nodepool inspection and management |
| `kubectl_helpers.py` | kubectl command wrappers for direct cluster access |

### commands/

Click command definitions that wire CLI flags to the business logic above.

| File | Commands |
|------|----------|
| `capacity_cmd.py` | `gco capacity check`, `status`, `recommend`, `ai-recommend` |
| `config_cmd.py` | `gco config show`, `set` |
| `costs_cmd.py` | `gco costs summary`, `regions`, `trend`, `workloads`, `forecast` |
| `dag_cmd.py` | `gco dag run`, `status` |
| `files_cmd.py` | `gco files ls`, `download` |
| `inference_cmd.py` | `gco inference deploy`, `list`, `status`, `scale`, `invoke`, `canary`, etc. |
| `jobs_cmd.py` | `gco jobs submit`, `submit-sqs`, `submit-direct`, `list`, `logs`, `delete` |
| `models_cmd.py` | `gco models upload`, `list`, `uri`, `delete` |
| `nodepools_cmd.py` | `gco nodepools list`, `describe` |
| `queue_cmd.py` | `gco queue submit`, `list`, `get`, `stats` |
| `stacks_cmd.py` | `gco stacks deploy`, `deploy-all`, `destroy-all`, `list`, `bootstrap` |
| `templates_cmd.py` | `gco templates list`, `get`, `create`, `delete` |
| `webhooks_cmd.py` | `gco webhooks list`, `create`, `delete`, `test` |

### capacity/

GPU capacity checking, region recommendation, and AI-powered advisory.

| File | Description |
|------|-------------|
| `checker.py` | Spot placement scores, pricing, and availability checks |
| `advisor.py` | AI-powered capacity recommendations via Amazon Bedrock |
| `models.py` | Data models for capacity responses |
| `multi_region.py` | Cross-region capacity aggregation and comparison |

## Installation

```bash
pip install -e .        # Development (editable)
pipx install -e .       # CLI-only usage
```

## Reference

See [CLI Reference](../docs/CLI.md) for the full command documentation.
