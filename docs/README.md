# Documentation

Comprehensive guides for understanding, deploying, operating, and customizing GCO.

> **💡 Tip:** Connect the [MCP server](../mcp/) to an agent and explore the codebase through conversation. Ask things like *"What CDK stacks does GCO create?"* or *"How does the manifest processor validate jobs?"* — the agent reads the source code and docs to answer. See [mcp/README.md](../mcp/README.md) for setup.

## Guides

| Document | Audience | Description |
|----------|----------|-------------|
| [Core Concepts](CONCEPTS.md) | New users | What GCO is, the problems it solves, and how the key components work together |
| [Architecture](ARCHITECTURE.md) | Engineers | Deep dive into the multi-region infrastructure, security layers, data flow, and scale characteristics |
| [Quick Start](../QUICKSTART.md) | New users | Get running in under 60 minutes — install, deploy, submit your first job |
| [CLI Reference](CLI.md) | Operators | Complete command reference for all `gco` CLI commands |
| [Inference Guide](INFERENCE.md) | ML engineers | Deploy and manage multi-region GPU inference endpoints (vLLM, TGI, Triton, SGLang, TorchServe) |
| [API Reference](API.md) | Developers | REST API documentation for manifest submission, job management, and webhooks |
| [Customization](CUSTOMIZATION.md) | Platform teams | Add regions, tune nodepools, enable FSx/Valkey/EFA, configure queue processor |
| [Schedulers & Orchestrators](SCHEDULERS.md) | ML/HPC engineers | Overview of all supported schedulers and when to use each one |
| [Troubleshooting](TROUBLESHOOTING.md) | Operators | Common issues and solutions for deployment, networking, pods, and storage |
| [Operational Runbooks](RUNBOOKS.md) | Operators | Step-by-step incident response procedures for common failure scenarios |

## Schedulers & Orchestrators

| Document | Status | Description |
|----------|--------|-------------|
| [Schedulers Overview](SCHEDULERS.md) | — | Comparison, decision guide, and how tools combine |
| [Volcano](VOLCANO.md) | Enabled | Gang scheduling and batch job management for distributed training |
| [Kueue](KUEUE.md) | Enabled | Job queueing with resource quotas, fair sharing, and priority |
| [KubeRay](KUBERAY.md) | Enabled | Ray distributed computing for training, tuning, and serving |
| [KEDA](KEDA.md) | Enabled | Event-driven autoscaling from SQS, Prometheus, CloudWatch, and 60+ sources |
| [Slurm (Slinky)](SLURM_OPERATOR.md) | Opt-in | HPC-style scheduling with sbatch/srun on Kubernetes |
| [YuniKorn](YUNIKORN.md) | Opt-in | App-aware scheduler with hierarchical queues and multi-tenant fair sharing |

## Supplementary

| Directory | Description |
|-----------|-------------|
| [client-examples/](client-examples/) | API client examples in Python, curl, and AWS CLI |
| [iam-policies/](iam-policies/) | IAM policy templates for different access levels |

## Reading Order

If you're new to GCO:

1. [Core Concepts](CONCEPTS.md) — understand what it does
2. [Quick Start](../QUICKSTART.md) — get it running
3. [CLI Reference](CLI.md) — learn the commands
4. [Inference Guide](INFERENCE.md) — deploy inference endpoints
5. [Schedulers Overview](SCHEDULERS.md) — pick the right scheduler for your workload

If you're customizing or operating:

1. [Architecture](ARCHITECTURE.md) — understand the infrastructure
2. [Customization](CUSTOMIZATION.md) — tune for your needs
3. [Schedulers Overview](SCHEDULERS.md) — configure scheduling tools
4. [Troubleshooting](TROUBLESHOOTING.md) — fix issues
5. [Operational Runbooks](RUNBOOKS.md) — incident response procedures
