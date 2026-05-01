"""Documentation resources (docs:// scheme) for the GCO MCP server."""

from pathlib import Path

from server import mcp

PROJECT_ROOT = Path(__file__).parent.parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
EXAMPLES_DIR = PROJECT_ROOT / "examples"

# ---------------------------------------------------------------------------
# Example metadata — used by both the index and the per-example resource to
# give the LLM rich context about what each manifest does and how to adapt it.
# ---------------------------------------------------------------------------

EXAMPLE_METADATA: dict[str, dict[str, str]] = {
    "simple-job": {
        "category": "Jobs & Training",
        "summary": "Basic Kubernetes Job that runs a command and completes. Start here to verify your cluster.",
        "gpu": "no",
        "opt_in": "",
        "submission": "gco jobs submit-sqs examples/simple-job.yaml --region us-east-1",
    },
    "gpu-job": {
        "category": "Jobs & Training",
        "summary": "Requests GPU resources and runs on GPU-enabled nodes.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco jobs submit-sqs examples/gpu-job.yaml --region us-east-1",
    },
    "gpu-timeslicing-job": {
        "category": "Jobs & Training",
        "summary": "Fractional GPU via NVIDIA time-slicing — multiple pods share one physical GPU.",
        "gpu": "NVIDIA (time-sliced)",
        "opt_in": "NVIDIA device plugin time-slicing ConfigMap",
        "submission": "kubectl apply -f examples/gpu-timeslicing-job.yaml",
    },
    "multi-gpu-training": {
        "category": "Jobs & Training",
        "summary": "PyTorch DistributedDataParallel (DDP) across multiple GPUs with indexed pods and headless service.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "kubectl apply -f examples/multi-gpu-training.yaml",
    },
    "efa-distributed-training": {
        "category": "Jobs & Training",
        "summary": "Elastic Fabric Adapter (EFA) for high-bandwidth inter-node communication (up to 3.2 Tbps on P5, 28.8 Tbps on P6e). For p4d/p5/p6/trn instances.",
        "gpu": "NVIDIA + EFA",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/efa-distributed-training.yaml -r us-east-1",
    },
    "megatrain-sft-job": {
        "category": "Jobs & Training",
        "summary": "SFT fine-tuning of Qwen2.5-1.5B on a single GPU using MegaTrain. Downloads weights to EFS.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/megatrain-sft-job.yaml -r us-east-1",
    },
    "model-download-job": {
        "category": "Jobs & Training",
        "summary": "Pre-downloads HuggingFace model weights to shared EFS for inference endpoints.",
        "gpu": "no",
        "opt_in": "",
        "submission": "kubectl apply -f examples/model-download-job.yaml",
    },
    "sqs-job-submission": {
        "category": "Jobs & Training",
        "summary": "Demonstrates SQS-based submission (recommended). Contains CPU and GPU job examples.",
        "gpu": "optional",
        "opt_in": "",
        "submission": "gco jobs submit-sqs examples/sqs-job-submission.yaml --region us-east-1",
    },
    "trainium-job": {
        "category": "Accelerator Jobs",
        "summary": "AWS Trainium instance with Neuron SDK. Lower cost than GPU for training.",
        "gpu": "Trainium",
        "opt_in": "",
        "submission": "gco jobs submit examples/trainium-job.yaml --region us-east-1",
    },
    "inferentia-job": {
        "category": "Accelerator Jobs",
        "summary": "AWS Inferentia2 with Neuron SDK. Optimized for low-cost, high-throughput inference.",
        "gpu": "Inferentia",
        "opt_in": "",
        "submission": "gco jobs submit examples/inferentia-job.yaml --region us-east-1",
    },
    "inference-vllm": {
        "category": "Inference Serving",
        "summary": "vLLM OpenAI-compatible LLM serving with PagedAttention.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco inference deploy my-llm -i vllm/vllm-openai:v0.20.0 --gpu-count 1",
    },
    "inference-tgi": {
        "category": "Inference Serving",
        "summary": "HuggingFace Text Generation Inference — optimized transformer serving.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/inference-tgi.yaml -r us-east-1",
    },
    "inference-triton": {
        "category": "Inference Serving",
        "summary": "NVIDIA Triton Inference Server — multi-framework (PyTorch, TensorFlow, ONNX).",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/inference-triton.yaml -r us-east-1",
    },
    "inference-torchserve": {
        "category": "Inference Serving",
        "summary": "PyTorch TorchServe model serving.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/inference-torchserve.yaml -r us-east-1",
    },
    "inference-sglang": {
        "category": "Inference Serving",
        "summary": "SGLang high-throughput serving with RadixAttention for prefix caching.",
        "gpu": "NVIDIA",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/inference-sglang.yaml -r us-east-1",
    },
    "efs-output-job": {
        "category": "Storage & Persistence",
        "summary": "Writes output to shared EFS storage. Results persist after pod termination.",
        "gpu": "no",
        "opt_in": "",
        "submission": "gco jobs submit-direct examples/efs-output-job.yaml --region us-east-1 -n gco-jobs",
    },
    "fsx-lustre-job": {
        "category": "Storage & Persistence",
        "summary": "FSx for Lustre high-performance parallel storage (1000+ GB/s throughput).",
        "gpu": "no",
        "opt_in": "FSx (gco stacks fsx enable -y)",
        "submission": "gco jobs submit-direct examples/fsx-lustre-job.yaml --region us-east-1 -n gco-jobs",
    },
    "valkey-cache-job": {
        "category": "Caching & Databases",
        "summary": "Valkey Serverless cache for K/V caching, prompt caching, session state, feature stores.",
        "gpu": "no",
        "opt_in": 'Valkey ("valkey": {"enabled": true} in cdk.json)',
        "submission": "gco jobs submit-direct examples/valkey-cache-job.yaml -r us-east-1",
    },
    "aurora-pgvector-job": {
        "category": "Caching & Databases",
        "summary": "Aurora Serverless v2 PostgreSQL with pgvector for RAG and semantic search.",
        "gpu": "no",
        "opt_in": 'Aurora ("aurora_pgvector": {"enabled": true} in cdk.json)',
        "submission": "gco jobs submit-direct examples/aurora-pgvector-job.yaml -r us-east-1",
    },
    "volcano-gang-job": {
        "category": "Schedulers",
        "summary": "Volcano gang scheduling — all pods scheduled together or none. Master + workers topology.",
        "gpu": "no",
        "opt_in": "",
        "submission": "kubectl apply -f examples/volcano-gang-job.yaml",
    },
    "kueue-job": {
        "category": "Schedulers",
        "summary": "Kueue job queueing with ClusterQueue, LocalQueue, ResourceFlavors, and fair-sharing.",
        "gpu": "optional",
        "opt_in": "",
        "submission": "kubectl apply -f examples/kueue-job.yaml",
    },
    "yunikorn-job": {
        "category": "Schedulers",
        "summary": "Apache YuniKorn app-aware scheduling with hierarchical queues and gang scheduling.",
        "gpu": "no",
        "opt_in": 'YuniKorn ("helm": {"yunikorn": {"enabled": true}} in cdk.json)',
        "submission": "kubectl apply -f examples/yunikorn-job.yaml",
    },
    "keda-scaled-job": {
        "category": "Schedulers",
        "summary": "KEDA ScaledJob — custom SQS-triggered autoscaling. Template for custom consumers.",
        "gpu": "no",
        "opt_in": "",
        "submission": "kubectl apply -f examples/keda-scaled-job.yaml",
    },
    "slurm-cluster-job": {
        "category": "Schedulers",
        "summary": "Slinky Slurm Operator — sbatch submission on Kubernetes for HPC workloads.",
        "gpu": "no",
        "opt_in": 'Slurm ("helm": {"slurm": {"enabled": true}} in cdk.json)',
        "submission": "kubectl apply -f examples/slurm-cluster-job.yaml",
    },
    "ray-cluster": {
        "category": "Distributed Computing",
        "summary": "KubeRay RayCluster for distributed training, tuning, and serving. Auto-scaling workers.",
        "gpu": "no",
        "opt_in": "",
        "submission": "kubectl apply -f examples/ray-cluster.yaml",
    },
    "pipeline-dag": {
        "category": "DAG Pipelines",
        "summary": "Multi-step pipeline with dependency ordering. Preprocess → Train via shared EFS.",
        "gpu": "no",
        "opt_in": "",
        "submission": "gco dag run examples/pipeline-dag.yaml -r us-east-1",
    },
    "dag-step-preprocess": {
        "category": "DAG Pipelines",
        "summary": "DAG step 1: generates training data on shared EFS.",
        "gpu": "no",
        "opt_in": "",
        "submission": "(used by pipeline-dag.yaml)",
    },
    "dag-step-train": {
        "category": "DAG Pipelines",
        "summary": "DAG step 2: reads preprocess output, trains model, writes artifacts to EFS.",
        "gpu": "no",
        "opt_in": "",
        "submission": "(used by pipeline-dag.yaml)",
    },
}


@mcp.resource("docs://gco/index")
def docs_index() -> str:
    """List all available GCO documentation, examples, and configuration resources."""
    sections = ["# GCO Resource Index\n"]
    sections.append("## Project Overview")
    sections.append("- `docs://gco/README` — Project README and overview")
    sections.append("- `docs://gco/QUICKSTART` — Quick start guide (deploy in under 60 minutes)")
    sections.append("- `docs://gco/CONTRIBUTING` — Contributing guide\n")

    sections.append("## Documentation")
    for f in sorted(DOCS_DIR.glob("*.md")):
        sections.append(f"- `docs://gco/docs/{f.stem}` — {f.stem}")

    sections.append("\n## Example Manifests")
    sections.append("- `docs://gco/examples/README` — Examples overview and usage guide")
    sections.append("- `docs://gco/examples/guide` — How to create new job manifests (patterns & metadata)\n")

    # Categorize examples
    categories: dict[str, list[str]] = {}
    for f in sorted(EXAMPLES_DIR.glob("*.yaml")):
        name = f.stem
        meta = EXAMPLE_METADATA.get(name, {})
        cat = meta.get("category", "Other")
        summary = meta.get("summary", name)
        entry = f"- `docs://gco/examples/{name}` — {summary}"
        categories.setdefault(cat, []).append(entry)

    for cat, entries in categories.items():
        sections.append(f"### {cat}")
        sections.extend(entries)
        sections.append("")

    sections.append("## Other Resource Groups")
    sections.append("- `k8s://gco/manifests/index` — Kubernetes manifests deployed to EKS")
    sections.append("- `iam://gco/policies/index` — IAM policy templates")
    sections.append("- `infra://gco/index` — Dockerfiles, Helm charts, CI/CD config")
    sections.append("- `ci://gco/index` — GitHub Actions workflows, composite actions, templates")
    sections.append("- `source://gco/index` — Source code browser")
    sections.append("- `demos://gco/index` — Demo walkthroughs and presentation materials")
    sections.append("- `clients://gco/index` — API client examples (Python, curl, AWS CLI)")
    sections.append("- `scripts://gco/index` — Utility scripts")
    sections.append("- `tests://gco/index` — Test suite documentation and patterns")
    sections.append("- `config://gco/index` — CDK configuration, feature toggles, environment variables")
    return "\n".join(sections)


@mcp.resource("docs://gco/README")
def readme_resource() -> str:
    """The main project README with overview and quickstart information."""
    return (PROJECT_ROOT / "README.md").read_text()


@mcp.resource("docs://gco/QUICKSTART")
def quickstart_resource() -> str:
    """Quick start guide — get running in under 60 minutes."""
    path = PROJECT_ROOT / "QUICKSTART.md"
    if not path.is_file():
        return "QUICKSTART.md not found."
    return path.read_text()


@mcp.resource("docs://gco/CONTRIBUTING")
def contributing_resource() -> str:
    """Contributing guide — how to contribute to the project."""
    path = PROJECT_ROOT / "CONTRIBUTING.md"
    if not path.is_file():
        return "CONTRIBUTING.md not found."
    return path.read_text()


@mcp.resource("docs://gco/docs/{doc_name}")
def doc_resource(doc_name: str) -> str:
    """Read a documentation file by name (e.g. ARCHITECTURE, CLI, INFERENCE)."""
    path = DOCS_DIR / f"{doc_name}.md"
    if not path.is_file():
        available = [f.stem for f in DOCS_DIR.glob("*.md")]
        return f"Document '{doc_name}' not found. Available: {', '.join(available)}"
    return path.read_text()


@mcp.resource("docs://gco/examples/README")
def examples_readme_resource() -> str:
    """Examples README — overview of all example manifests with usage instructions."""
    path = EXAMPLES_DIR / "README.md"
    if not path.is_file():
        return "Examples README.md not found."
    return path.read_text()


@mcp.resource("docs://gco/examples/guide")
def examples_guide_resource() -> str:
    """How to create new job manifests — patterns, metadata, and best practices.

    Use this resource when you need to write a new Kubernetes manifest for GCO.
    It provides the metadata for every existing example so you can pick the
    closest one as a starting point and adapt it.
    """
    lines = ["# GCO Example Manifest Guide\n"]
    lines.append("Use this guide to create new Kubernetes manifests for GCO. Pick the closest")
    lines.append("existing example as a starting point, then adapt it.\n")
    lines.append("## All Examples with Metadata\n")
    lines.append("| Example | Category | GPU | Opt-in | How to Submit |")
    lines.append("|---------|----------|-----|--------|---------------|")
    for name, meta in EXAMPLE_METADATA.items():
        gpu = meta.get("gpu", "no")
        opt_in = meta.get("opt_in", "—") or "—"
        submission = meta.get("submission", "")
        lines.append(f"| `{name}` | {meta['category']} | {gpu} | {opt_in} | `{submission}` |")

    lines.append("\n## Common Patterns\n")
    lines.append("### Namespace")
    lines.append("All GCO jobs use `namespace: gco-jobs`. Inference uses `namespace: gco-inference`.\n")
    lines.append("### Security Context (required)")
    lines.append("```yaml")
    lines.append("securityContext:")
    lines.append("  runAsNonRoot: true")
    lines.append("  runAsUser: 1000")
    lines.append("  runAsGroup: 1000")
    lines.append("containers:")
    lines.append("- securityContext:")
    lines.append("    allowPrivilegeEscalation: false")
    lines.append("    capabilities:")
    lines.append('      drop: ["ALL"]')
    lines.append("```\n")
    lines.append("### GPU Resources")
    lines.append("```yaml")
    lines.append("resources:")
    lines.append("  requests:")
    lines.append('    nvidia.com/gpu: "1"')
    lines.append("  limits:")
    lines.append('    nvidia.com/gpu: "1"')
    lines.append("tolerations:")
    lines.append("- key: nvidia.com/gpu")
    lines.append("  operator: Equal")
    lines.append('  value: "true"')
    lines.append("  effect: NoSchedule")
    lines.append("```\n")
    lines.append("### EFS Shared Storage")
    lines.append("```yaml")
    lines.append("volumeMounts:")
    lines.append("- name: shared-storage")
    lines.append("  mountPath: /mnt/gco")
    lines.append("volumes:")
    lines.append("- name: shared-storage")
    lines.append("  persistentVolumeClaim:")
    lines.append("    claimName: gco-shared-storage")
    lines.append("```\n")
    lines.append("### Prevent Node Consolidation (long-running jobs)")
    lines.append("```yaml")
    lines.append("metadata:")
    lines.append("  annotations:")
    lines.append('    karpenter.sh/do-not-disrupt: "true"')
    lines.append("```\n")
    lines.append("### Submission Methods")
    lines.append("1. **SQS (recommended):** `gco jobs submit-sqs <manifest> --region <region>`")
    lines.append("2. **API Gateway:** `gco jobs submit <manifest>`")
    lines.append("3. **Direct kubectl:** `gco jobs submit-direct <manifest> -r <region>`")
    lines.append("4. **kubectl apply:** `kubectl apply -f <manifest>`")
    return "\n".join(lines)


@mcp.resource("docs://gco/examples/{example_name}")
def example_resource(example_name: str) -> str:
    """Read an example manifest by name, with metadata context for creating similar jobs.

    Returns the raw YAML manifest preceded by a metadata header that describes
    what the example does, its requirements, and how to submit it.
    """
    path = EXAMPLES_DIR / f"{example_name}.yaml"
    if not path.is_file():
        available = [f.stem for f in EXAMPLES_DIR.glob("*.yaml")]
        return f"Example '{example_name}' not found. Available: {', '.join(available)}"

    meta = EXAMPLE_METADATA.get(example_name, {})
    header_lines = []
    if meta:
        header_lines.append(f"# Example: {example_name}")
        header_lines.append(f"# Category: {meta.get('category', 'Unknown')}")
        header_lines.append(f"# Summary: {meta.get('summary', '')}")
        if meta.get("gpu", "no") != "no":
            header_lines.append(f"# GPU/Accelerator: {meta['gpu']}")
        if meta.get("opt_in"):
            header_lines.append(f"# Opt-in required: {meta['opt_in']}")
        header_lines.append(f"# Submit with: {meta.get('submission', 'kubectl apply -f examples/' + example_name + '.yaml')}")
        header_lines.append("#")
        header_lines.append("# --- Manifest begins below ---\n")

    manifest = path.read_text()
    if header_lines:
        return "\n".join(header_lines) + manifest
    return manifest
