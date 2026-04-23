# Kubernetes Manifest Examples

This directory contains example Kubernetes manifests you can use with GCO (Global Capacity Orchestrator on AWS).

## Available Examples

### 1. Simple Job (`simple-job.yaml`)

A basic Kubernetes Job that runs a simple command and completes.

**Usage:**
```bash
kubectl apply -f examples/simple-job.yaml
kubectl get jobs
kubectl logs job/hello-gco
```

**When to use:**
- Testing cluster connectivity
- Running one-off tasks
- Batch processing

---

### 2. GPU Job (`gpu-job.yaml`)

A job that requests GPU resources and runs on GPU-enabled nodes.

**Usage:**
```bash
kubectl apply -f examples/gpu-job.yaml
kubectl get jobs
kubectl logs job/gpu-test-job
```

**Requirements:**
- GPU nodepools must be configured (included by default)
- NVIDIA device plugin must be running (included by default)

**When to use:**
- ML model training
- GPU-accelerated workloads
- Testing GPU availability

---

### 3. GPU Time-Slicing Job (`gpu-timeslicing-job.yaml`)

A job that uses a fractional GPU via NVIDIA time-slicing. Multiple pods share a single physical GPU by taking turns, letting you run lightweight GPU workloads without dedicating a full GPU to each pod.

**Prerequisites:**

Time-slicing must be enabled by applying a ConfigMap to the NVIDIA device plugin. See the comments in the manifest for setup instructions.

**Usage:**
```bash
kubectl apply -f examples/gpu-timeslicing-job.yaml
kubectl get jobs -n gco-jobs
kubectl logs job/gpu-timeslice-job -n gco-jobs
```

**Requirements:**
- GPU nodepools must be configured (included by default)
- NVIDIA device plugin with time-slicing ConfigMap applied (not enabled by default — see manifest comments for setup)

**When to use:**
- Inference workloads that don't need a full GPU
- Dev/test GPU workloads
- Running multiple lightweight GPU jobs on a single node
- Reducing GPU costs by sharing hardware

---

### 4. EFS Output Job (`efs-output-job.yaml`)

A job that writes output to shared EFS storage, demonstrating how to persist job results.

**Usage:**
```bash
# Submit the job
gco jobs submit-direct examples/efs-output-job.yaml --region us-east-1 -n gco-jobs

# Check job status
kubectl get jobs -n gco-jobs

# View logs
kubectl logs job/efs-output-example -n gco-jobs

# List what's on EFS storage (discover output directories)
gco files ls -r us-east-1

# Download outputs (works even after job pod is deleted)
gco files download efs-output-example ./efs-results -r us-east-1

# View the downloaded results
cat ./efs-results/results.json
```

**Features:**
- Mounts shared EFS storage at `/outputs`
- Creates output directory using job name (not pod name)
- Results persist after job completion
- Other pods can read the outputs

**When to use:**
- ML model training with checkpoint saving
- Data processing pipelines
- Any job that needs to persist outputs
- Sharing data between jobs

---

### 5. FSx for Lustre Job (`fsx-lustre-job.yaml`)

A job that uses FSx for Lustre high-performance storage for I/O-intensive workloads.

**Prerequisites:**
```bash
# Enable FSx for Lustre
gco stacks fsx enable -y

# Redeploy the stack
gco stacks deploy gco-us-east-1 -y
```

**Usage:**
```bash
# Submit the job
gco jobs submit-direct examples/fsx-lustre-job.yaml --region us-east-1 -n gco-jobs

# Check job status
kubectl get jobs -n gco-jobs

# View logs
kubectl logs job/fsx-lustre-example -n gco-jobs

# Download outputs (works even after job pod is deleted)
gco files download fsx-lustre-example ./fsx-results -r us-east-1 -t fsx
```

**Features:**
- High-throughput parallel file system (hundreds of GB/s)
- Low-latency access (sub-millisecond)
- Ideal for large datasets and HPC workloads
- Supports checkpointing for long-running jobs
- Can integrate with S3 for data import/export

**When to use:**
- Large-scale ML training with big datasets
- High-performance computing (HPC) workloads
- Jobs requiring high I/O throughput
- Checkpoint/restart for long-running jobs
- Processing large files (videos, genomics, etc.)

**FSx vs EFS:**
| Feature | EFS | FSx for Lustre |
|---------|-----|----------------|
| Throughput | Up to 10 GB/s | Up to 1000+ GB/s |
| Latency | ~1-3ms | ~sub-ms |
| Cost | Pay per GB stored | Pay per GB provisioned |
| Best for | General purpose | HPC, ML training |

---

### 6. Inference Examples

GCO includes example manifests for multiple inference frameworks. Each creates a Deployment and Service in the `gco-inference` namespace.

| Example | Framework | Description |
|---------|-----------|-------------|
| `inference-vllm.yaml` | vLLM | OpenAI-compatible LLM serving |
| `inference-tgi.yaml` | TGI | HuggingFace optimized inference |
| `inference-triton.yaml` | Triton | NVIDIA multi-framework serving |
| `inference-torchserve.yaml` | TorchServe | PyTorch model serving |
| `inference-sglang.yaml` | SGLang | High-throughput serving with RadixAttention |

**Deploy via CLI (recommended for multi-region):**
```bash
gco inference deploy my-llm -i vllm/vllm-openai:v0.19.1 --gpu-count 1
```

**Deploy a manifest directly (single region):**
```bash
gco jobs submit-direct examples/inference-vllm.yaml -r us-east-1
```

---

### 7. Trainium Job (`trainium-job.yaml`)

A job that runs on an AWS Trainium instance using the Neuron SDK. Trainium is a purpose-built ML accelerator designed by AWS, offering lower cost than GPU instances for training workloads.

**Usage:**
```bash
gco jobs submit examples/trainium-job.yaml --region us-east-1
gco jobs logs trainium-test -r us-east-1
```

**Requirements:**
- Neuron device plugin (installed by default via Helm chart)
- Neuron nodepool (applied by default)
- Container images built with the Neuron SDK (not CUDA)

**When to use:**
- Cost-optimized model training
- Inference workloads on Neuron-supported models
- Distributed training with EFA on trn1.32xlarge or trn2.48xlarge

---

### 8. Inferentia Job (`inferentia-job.yaml`)

A job that runs on an AWS Inferentia2 instance using the Neuron SDK. Inferentia is optimized for low-cost, high-throughput inference.

**Usage:**
```bash
gco jobs submit examples/inferentia-job.yaml --region us-east-1
gco jobs logs inferentia-test -r us-east-1
```

**Requirements:**
- Neuron device plugin (installed by default via Helm chart)
- Neuron nodepool (applied by default)
- Container images built with the Neuron SDK (not CUDA)

**When to use:**
- Cost-optimized inference serving
- High-throughput batch inference
- Deploying Neuron-compiled models (HuggingFace, PyTorch)

---

### 9. Slurm Cluster Job (`slurm-cluster-job.yaml`)

A self-contained Kubernetes Job that writes a Slurm batch script into the login pod, submits it via `sbatch`, waits for completion, and prints the output. Just `kubectl apply` and watch the logs.

**Usage:**
```bash
kubectl apply -f examples/slurm-cluster-job.yaml
kubectl logs job/slurm-test -n gco-jobs -f
```

**Requirements:**
- Slinky Slurm Operator + Slurm cluster (opt-in — enable in `cdk.json`: `"helm": { "slurm": { "enabled": true } }`)
- cert-manager (installed by default via Helm chart)

**When to use:**
- Verifying the Slurm cluster is working end-to-end
- HPC workloads that need Slurm's deterministic scheduling
- Teams migrating from on-premises Slurm clusters

---

### 10. YuniKorn Job (`yunikorn-job.yaml`)

Demonstrates Apache YuniKorn's app-aware scheduling with hierarchical queues, gang scheduling, and GPU queue placement.

**Usage:**
```bash
kubectl apply -f examples/yunikorn-job.yaml
kubectl get pods -n gco-jobs -l app=yunikorn-demo -w

# Access the YuniKorn web UI
kubectl port-forward svc/yunikorn-service -n yunikorn 9889:9889
# Open http://localhost:9889
```

**Requirements:**
- Apache YuniKorn (opt-in — enable in `cdk.json`: `"helm": { "yunikorn": { "enabled": true } }`)

**When to use:**
- Multi-tenant clusters with competing teams
- Hierarchical resource quota management
- Fair-sharing with preemption for high-priority jobs
- Gang scheduling for distributed training

---

### 11. MegaTrain SFT Job (`megatrain-sft-job.yaml`)

Runs SFT fine-tuning of Qwen2.5-1.5B on a single GPU using [MegaTrain](https://github.com/DLYuanGod/MegaTrain). An init container downloads model weights to shared EFS (skipped if already cached), then the main container installs MegaTrain, builds its CUDA extension, and trains on the built-in alpaca demo dataset. Change the `MODEL_NAME` env var to target a different HuggingFace model.

**Usage:**
```bash
# Submit the MegaTrain training job (model downloads automatically)
gco jobs submit-direct examples/megatrain-sft-job.yaml -r us-east-1

# Monitor training
gco jobs logs megatrain-sft -r us-east-1 -f
```

**Requirements:**
- GPU node with large CPU RAM
- Shared EFS storage for model weights and checkpoints

**When to use:**
- Training very large models (14B–120B+) on a single GPU
- Full-precision training without model parallelism
- When multi-node distributed training is not available or cost-effective
- SFT fine-tuning of large HuggingFace models

---

## Customizing Examples

### Changing Resource Requests/Limits

Edit the `resources` section:

```yaml
resources:
  requests:
    cpu: "500m"      # Increase CPU request
    memory: "256Mi"  # Increase memory request
  limits:
    cpu: "1000m"     # Increase CPU limit
    memory: "1Gi"    # Increase memory limit
```

### Changing Replica Count

Edit the `replicas` field in Deployments:

```yaml
spec:
  replicas: 5  # Increase from 3 to 5
```

### Adding Environment Variables

Add to the container spec:

```yaml
containers:
- name: my-app
  image: my-image
  env:
  - name: MY_VAR
    value: "my-value"
  - name: CLUSTER_NAME
    value: "gco-us-east-1"
```

### Using ConfigMaps and Secrets

```yaml
# Create ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  config.json: |
    {"key": "value"}

---
# Use in Pod
containers:
- name: my-app
  volumeMounts:
  - name: config
    mountPath: /etc/config
volumes:
- name: config
  configMap:
    name: app-config
```

## Best Practices

### 1. Always Set Resource Limits

```yaml
resources:
  requests:
    cpu: "100m"
    memory: "128Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"
```

This helps Kubernetes schedule pods efficiently and prevents resource exhaustion.

### 2. Use Security Contexts

```yaml
securityContext:
  allowPrivilegeEscalation: false
  runAsNonRoot: true
  runAsUser: 1000
  capabilities:
    drop: ["ALL"]
```

This follows security best practices and reduces attack surface.

### 3. Add Labels

```yaml
metadata:
  labels:
    app: my-app
    version: v1.0
    environment: production
```

Labels help with organization, selection, and monitoring.

### 4. Use Namespaces

```yaml
metadata:
  namespace: my-namespace
```

Namespaces provide isolation and organization.

### 5. Set Restart Policies

For Jobs:
```yaml
spec:
  template:
    spec:
      restartPolicy: Never  # or OnFailure
```

For Deployments:
```yaml
spec:
  template:
    spec:
      restartPolicy: Always  # default
```

### 6. Prevent Node Consolidation for Long-Running Jobs

EKS Auto Mode (Karpenter) may consolidate underutilized nodes, which evicts running pods. For training jobs or other long-running workloads that should not be interrupted, add the `karpenter.sh/do-not-disrupt` annotation to the pod template:

```yaml
spec:
  template:
    metadata:
      annotations:
        karpenter.sh/do-not-disrupt: "true"
```

This tells the autoscaler to leave the node alone until the pod completes. Remove the annotation or omit it for short-lived jobs where eviction and retry is acceptable.

## Troubleshooting

### "Resource limits exceed maximum allowed values"

The GCO API validates manifest resource requests against configurable limits. If your job needs more CPU, memory, or GPUs than the defaults allow, update the limits in `cdk.json` and redeploy:

```json
{
  "manifest_processor": {
    "resource_quotas": {
      "max_cpu_per_manifest": "96",
      "max_memory_per_manifest": "192Gi",
      "max_gpu_per_manifest": 8
    }
  },
  "queue_processor": {
    "max_cpu_per_manifest": "96",
    "max_memory_per_manifest": "192Gi",
    "max_gpu_per_manifest": 8
  }
}
```

Then redeploy: `gco stacks deploy-all -y`

Both sections control independent submission paths — `manifest_processor` validates jobs submitted via the API Gateway, and `queue_processor` validates jobs submitted via SQS. Update whichever path you use, or both if you use both.

## Testing Your Manifests

### Dry Run

Test without actually creating resources:

```bash
kubectl apply -f your-manifest.yaml --dry-run=client
```

### Validate

Check for syntax errors:

```bash
kubectl apply -f your-manifest.yaml --validate=true --dry-run=server
```

### Check Status

After applying:

```bash
# Check pods
kubectl get pods -w

# Check events
kubectl get events --sort-by='.lastTimestamp'

# Describe resource
kubectl describe pod POD-NAME
```

## Cleaning Up

Delete resources created by examples:

```bash
# Delete specific resource
kubectl delete -f examples/simple-job.yaml

# Delete all jobs
kubectl delete jobs --all

# Delete all resources in a namespace
kubectl delete all --all -n default
```

## Additional Resources

- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Kubernetes Best Practices](https://kubernetes.io/docs/concepts/configuration/overview/)
- [GCO (Global Capacity Orchestrator on AWS) Documentation](../README.md)
- [Inference Serving Guide](../docs/INFERENCE.md) — Full inference guide

---

**Need help?** Check the [Troubleshooting Guide](../docs/TROUBLESHOOTING.md)
