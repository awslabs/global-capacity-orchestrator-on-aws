#!/usr/bin/env python3
"""
GCO MCP Server — Exposes the GCO CLI as MCP tools for LLM interaction.

Run with:
    python mcp/run_mcp.py

Add to Kiro MCP config (.kiro/settings/mcp.json):
    {
        "mcpServers": {
            "gco": {
                "command": "python3",
                "args": ["mcp/run_mcp.py"],
                "cwd": "/path/to/GCO"
            }
        }
    }
"""

import functools
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# Ensure the project root is on the path so CLI modules can be imported
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

mcp = FastMCP(
    "GCO",
    instructions=(
        "Multi-region EKS Auto Mode platform for AI/ML workload orchestration. "
        "Submit jobs, manage inference endpoints, check capacity, track costs, "
        "and manage infrastructure across AWS regions.\n\n"
        "Resources available:\n"
        "- docs:// — Documentation, architecture guides, and example job/inference manifests\n"
        "- k8s:// — Kubernetes manifests deployed to the cluster (RBAC, deployments, NodePools, etc.)\n"
        "- iam:// — IAM policy templates for access control\n"
        "- infra:// — Dockerfiles, Helm charts, CI/CD config\n"
        "- source:// — Full source code of the platform\n"
        "- demos:// — Demo walkthroughs, live demo scripts, and presentation materials\n"
        "- clients:// — API client examples (Python, curl, AWS CLI)\n"
        "- scripts:// — Utility scripts for cluster access, versioning, testing\n\n"
        "Start with docs://gco/index or k8s://gco/manifests/index to explore."
    ),
)


# =============================================================================
# AUDIT LOGGING
# =============================================================================

_MCP_SERVER_VERSION = "1.0.0"

audit_logger = logging.getLogger("gco.mcp.audit")

# Patterns for sensitive argument key names (case-insensitive)
_SENSITIVE_KEY_PATTERNS = [
    re.compile(r".*token.*", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*password.*", re.IGNORECASE),
    re.compile(r".*key.*", re.IGNORECASE),
]

_MAX_ARG_VALUE_BYTES = 1024  # 1KB


def _sanitize_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Sanitize tool arguments for audit logging.

    - Redact values whose key name matches sensitive patterns (token, secret, password, key).
    - Truncate string values longer than 1KB to first 100 chars + '[truncated]'.
    """
    sanitized = {}
    for k, v in kwargs.items():
        # Check if the key name matches any sensitive pattern
        if any(pattern.match(k) for pattern in _SENSITIVE_KEY_PATTERNS):
            sanitized[k] = "[REDACTED]"
            continue

        # Truncate large string values
        str_val = str(v) if not isinstance(v, str) else v
        if len(str_val.encode("utf-8", errors="replace")) > _MAX_ARG_VALUE_BYTES:
            sanitized[k] = str_val[:100] + "[truncated]"
        else:
            sanitized[k] = v
    return sanitized


def audit_logged(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that logs structured JSON audit entries for MCP tool invocations."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        sanitized_args = _sanitize_arguments(kwargs)
        try:
            result = func(*args, **kwargs)
            duration_ms = (time.time() - start) * 1000
            audit_logger.info(
                json.dumps(
                    {
                        "event": "mcp.tool.invocation",
                        "tool": func.__name__,
                        "arguments": sanitized_args,
                        "status": "success",
                        "duration_ms": round(duration_ms, 2),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            )
            return result
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            audit_logger.info(
                json.dumps(
                    {
                        "event": "mcp.tool.invocation",
                        "tool": func.__name__,
                        "arguments": sanitized_args,
                        "status": "error",
                        "error": str(e)[:200],
                        "duration_ms": round(duration_ms, 2),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            )
            raise

    return wrapper


# Startup audit log entry
audit_logger.info(
    json.dumps(
        {
            "event": "mcp.server.startup",
            "version": _MCP_SERVER_VERSION,
            "audit_log_level": logging.getLevelName(audit_logger.getEffectiveLevel()),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
)


# =============================================================================
# IAM ROLE ASSUMPTION
# =============================================================================


def _assume_mcp_role() -> None:
    """Assume the dedicated MCP IAM role if ``GCO_MCP_ROLE_ARN`` is set.

    When the environment variable is set, this function:

    1. Uses ambient credentials (via a transient ``boto3.Session``) to call
       ``sts:AssumeRole`` with the configured role ARN.
    2. Builds a new ``boto3.Session`` from the temporary credentials and
       installs it as the default session (``boto3.setup_default_session``)
       so that every subsequent boto3/botocore client in this process uses
       the least-privilege role automatically.
    3. Logs a sanitized audit entry (role ARN + expiration) via the audit
       logger. **Credentials themselves are never logged.**

    When the environment variable is not set, a debug-level message is
    logged and the process continues with ambient credentials. This keeps
    local development convenient while allowing production deployments to
    enforce a dedicated role by simply setting the env var.

    Any failure (bad ARN, access denied, network error) is logged as an
    error and re-raised — the MCP server should not silently fall back to
    ambient credentials if the operator explicitly requested role
    assumption.
    """
    role_arn = os.environ.get("GCO_MCP_ROLE_ARN", "").strip()
    if not role_arn:
        audit_logger.debug(
            json.dumps(
                {
                    "event": "mcp.server.role_assumption.skipped",
                    "reason": "GCO_MCP_ROLE_ARN not set; using ambient credentials",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        )
        return

    try:
        # Import lazily so the MCP server can still start in environments
        # where boto3 isn't installed (boto3 is listed in the mcp extras,
        # but the import is cheap and isolated here).
        import boto3
    except ImportError:
        audit_logger.error(
            json.dumps(
                {
                    "event": "mcp.server.role_assumption.error",
                    "role_arn": role_arn,
                    "error": "boto3 is not installed; cannot assume role",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        )
        raise

    session_name = os.environ.get("GCO_MCP_ROLE_SESSION_NAME", "gco-mcp-server")
    # Default duration: 1 hour. boto3 will attempt refresh when using
    # RefreshableCredentials via botocore.credentials.
    duration_seconds = int(os.environ.get("GCO_MCP_ROLE_DURATION_SECONDS", "3600"))

    try:
        ambient_session = boto3.Session()
        sts = ambient_session.client("sts")
        response = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=duration_seconds,
        )
    except Exception as e:
        audit_logger.error(
            json.dumps(
                {
                    "event": "mcp.server.role_assumption.error",
                    "role_arn": role_arn,
                    "error": str(e)[:200],
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        )
        raise

    credentials = response["Credentials"]
    expiration = credentials["Expiration"]
    expiration_iso = expiration.isoformat() if hasattr(expiration, "isoformat") else str(expiration)

    # Install a new default session backed by the assumed-role credentials.
    # All subsequent boto3 clients/resources (across every module) will
    # automatically pick up this session.
    boto3.setup_default_session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    # Audit the successful assumption. NEVER log the credentials themselves.
    audit_logger.info(
        json.dumps(
            {
                "event": "mcp.server.role_assumption.success",
                "role_arn": role_arn,
                "session_name": session_name,
                "expiration": expiration_iso,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
    )


# Attempt role assumption at module import time so every boto3 client
# created downstream uses the reduced-scope credentials.
try:
    _assume_mcp_role()
except Exception:
    # The error has already been audit-logged inside _assume_mcp_role.
    # Re-raising here would crash the server at import time; letting it
    # propagate to the caller is preferable only when GCO_MCP_ROLE_ARN
    # is explicitly set. If we got here, the env var was set — fail hard
    # so operators notice that role assumption broke.
    raise


def _run_cli(*args: str) -> str:
    """Run a gco CLI command and return its output.

    All args are passed as separate list elements to subprocess (shell=False),
    so shell metacharacters in user-provided values are treated as literals
    and cannot cause command injection. Path arguments are validated to prevent
    traversal outside the project root.
    """
    # Validate any path-like arguments to prevent directory traversal.
    # Non-path args (region names, job names, etc.) are safe as literal argv elements.
    for arg in args:
        if arg.startswith("-"):
            continue  # flag, not a path
        if ".." in arg.split("/"):
            return json.dumps({"error": f"Invalid argument: path traversal not allowed: {arg}"})

    cmd = ["gco", "--output", "json", *args]
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit - shell=False; args are validated above and passed as literal argv elements
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            error = result.stderr.strip() or output
            return json.dumps({"error": error, "exit_code": result.returncode})
        return output if output else json.dumps({"status": "ok"})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Command timed out after 120 seconds"})
    except FileNotFoundError:
        return json.dumps({"error": "gco CLI not found. Install with: pipx install -e ."})


# =============================================================================
# JOB TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def list_jobs(
    region: str | None = None, namespace: str | None = None, status: str | None = None
) -> str:
    """List jobs across GCO clusters.

    Args:
        region: AWS region (e.g. us-east-1). If omitted, lists across all regions.
        namespace: Filter by Kubernetes namespace.
        status: Filter by job status (pending, running, completed, succeeded, failed).
    """
    args = ["jobs", "list"]
    if region:
        args += ["-r", region]
    else:
        args += ["--all-regions"]
    if namespace:
        args += ["-n", namespace]
    if status:
        args += ["-s", status]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def submit_job_sqs(
    manifest_path: str, region: str, namespace: str | None = None, priority: int | None = None
) -> str:
    """Submit a job via SQS queue (recommended for production).

    Args:
        manifest_path: Path to the YAML manifest file (relative to project root).
        region: Target AWS region for the SQS queue.
        namespace: Override the namespace in the manifest.
        priority: Job priority (0-100, higher = more important).
    """
    args = ["jobs", "submit-sqs", manifest_path, "-r", region]
    if namespace:
        args += ["-n", namespace]
    if priority is not None:
        args += ["--priority", str(priority)]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def submit_job_api(manifest_path: str, namespace: str | None = None) -> str:
    """Submit a job via the authenticated API Gateway (SigV4).

    Args:
        manifest_path: Path to the YAML manifest file.
        namespace: Override the namespace in the manifest.
    """
    args = ["jobs", "submit", manifest_path]
    if namespace:
        args += ["-n", namespace]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def get_job(job_name: str, region: str, namespace: str = "gco-jobs") -> str:
    """Get details of a specific job.

    Args:
        job_name: Name of the job.
        region: AWS region where the job is running.
        namespace: Kubernetes namespace.
    """
    return _run_cli("jobs", "get", job_name, "-r", region, "-n", namespace)


@mcp.tool()
@audit_logged
def get_job_logs(job_name: str, region: str, namespace: str = "gco-jobs", tail: int = 100) -> str:
    """Get logs from a job.

    Args:
        job_name: Name of the job.
        region: AWS region.
        namespace: Kubernetes namespace.
        tail: Number of log lines to return.
    """
    return _run_cli("jobs", "logs", job_name, "-r", region, "-n", namespace, "--tail", str(tail))


@mcp.tool()
@audit_logged
def delete_job(job_name: str, region: str, namespace: str = "gco-jobs") -> str:
    """Delete a job.

    Args:
        job_name: Name of the job to delete.
        region: AWS region.
        namespace: Kubernetes namespace.
    """
    return _run_cli("jobs", "delete", job_name, "-r", region, "-n", namespace, "-y")


@mcp.tool()
@audit_logged
def get_job_events(job_name: str, region: str, namespace: str = "gco-jobs") -> str:
    """Get Kubernetes events for a job (useful for debugging).

    Args:
        job_name: Name of the job.
        region: AWS region.
        namespace: Kubernetes namespace.
    """
    return _run_cli("jobs", "events", job_name, "-r", region, "-n", namespace)


@mcp.tool()
@audit_logged
def cluster_health(region: str | None = None) -> str:
    """Get health status of GCO clusters.

    Args:
        region: Specific region, or omit for all regions.
    """
    args = ["jobs", "health"]
    if region:
        args += ["-r", region]
    else:
        args += ["--all-regions"]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def queue_status(region: str | None = None) -> str:
    """View SQS queue status (pending, in-flight, DLQ counts).

    Args:
        region: Specific region, or omit for all regions.
    """
    args = ["jobs", "queue-status"]
    if region:
        args += ["-r", region]
    else:
        args += ["--all-regions"]
    return _run_cli(*args)


# =============================================================================
# CAPACITY TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def check_capacity(instance_type: str, region: str) -> str:
    """Check spot and on-demand capacity for a specific instance type.

    Args:
        instance_type: EC2 instance type (e.g. g4dn.xlarge, g5.2xlarge, p4d.24xlarge).
        region: AWS region to check.
    """
    return _run_cli("capacity", "check", "-i", instance_type, "-r", region)


@mcp.tool()
@audit_logged
def capacity_status(region: str | None = None) -> str:
    """View capacity status across all deployed regions.

    Args:
        region: Specific region, or omit for all regions.
    """
    args = ["capacity", "status"]
    if region:
        args += ["-r", region]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def recommend_region(
    gpu: bool = False, instance_type: str | None = None, gpu_count: int = 0
) -> str:
    """Get optimal region recommendation based on capacity.

    Args:
        gpu: Whether the workload requires GPUs.
        instance_type: Specific instance type to check. When provided, uses weighted
            multi-signal scoring (spot placement scores, pricing, queue depth, etc.).
        gpu_count: Number of GPUs required for the workload.
    """
    args = ["capacity", "recommend-region"]
    if gpu:
        args.append("--gpu")
    if instance_type:
        args += ["-i", instance_type]
    if gpu_count:
        args += ["--gpu-count", str(gpu_count)]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def spot_prices(instance_type: str, region: str) -> str:
    """Get current spot prices for an instance type.

    Args:
        instance_type: EC2 instance type.
        region: AWS region.
    """
    return _run_cli("capacity", "spot-prices", "-i", instance_type, "-r", region)


@mcp.tool()
@audit_logged
def ai_recommend(
    workload: str,
    instance_type: str | None = None,
    region: str | None = None,
    gpu: bool = False,
    min_gpus: int = 0,
    min_memory_gb: int = 0,
    fault_tolerance: str = "low",
    max_cost: float | None = None,
    model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
) -> str:
    """Get AI-powered capacity recommendation using Amazon Bedrock.

    Gathers comprehensive capacity data (spot scores, pricing, cluster
    utilization, queue depth) and sends it to an LLM for analysis.
    Returns a recommended region, instance type, capacity type, and reasoning.

    Requires AWS credentials with bedrock:InvokeModel permission and the
    specified model enabled in your account.

    Args:
        workload: Description of the workload (e.g. "Fine-tuning a 20B parameter LLM").
        instance_type: Specific instance type(s) to consider (e.g. "p4d.24xlarge").
        region: Specific region(s) to consider (e.g. "us-east-1").
        gpu: Whether the workload requires GPUs.
        min_gpus: Minimum number of GPUs required.
        min_memory_gb: Minimum GPU memory in GB.
        fault_tolerance: Tolerance for interruptions ("low", "medium", "high").
        max_cost: Maximum acceptable cost per hour in USD.
        model: Bedrock model ID to use for analysis.
    """
    args = ["capacity", "ai-recommend", "-w", workload]
    if instance_type:
        args += ["-i", instance_type]
    if region:
        args += ["-r", region]
    if gpu:
        args.append("--gpu")
    if min_gpus > 0:
        args += ["--min-gpus", str(min_gpus)]
    if min_memory_gb > 0:
        args += ["--min-memory-gb", str(min_memory_gb)]
    if fault_tolerance != "low":
        args += ["--fault-tolerance", fault_tolerance]
    if max_cost is not None:
        args += ["--max-cost", str(max_cost)]
    if model != "anthropic.claude-sonnet-4-20250514-v1:0":
        args += ["--model", model]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def list_reservations(
    instance_type: str | None = None,
    region: str | None = None,
) -> str:
    """List On-Demand Capacity Reservations (ODCRs) across regions.

    Shows all active capacity reservations with utilization details.

    Args:
        instance_type: Filter by instance type (e.g. p5.48xlarge).
        region: Filter by specific region.
    """
    args = ["capacity", "reservations"]
    if instance_type:
        args += ["-i", instance_type]
    if region:
        args += ["-r", region]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def reservation_check(
    instance_type: str,
    region: str | None = None,
    count: int = 1,
    include_blocks: bool = True,
    block_duration: int = 24,
) -> str:
    """Check reservation availability and Capacity Block offerings.

    Checks both existing ODCRs and purchasable Capacity Blocks for ML
    workloads. Capacity Blocks provide guaranteed GPU capacity for a
    fixed duration at a known price.

    Args:
        instance_type: GPU instance type (e.g. p4d.24xlarge, p5.48xlarge).
        region: Specific region to check (omit for all deployed regions).
        count: Minimum number of instances needed.
        include_blocks: Whether to include Capacity Block offerings.
        block_duration: Capacity Block duration in hours.
    """
    args = ["capacity", "reservation-check", "-i", instance_type, "-c", str(count)]
    if region:
        args += ["-r", region]
    if not include_blocks:
        args.append("--no-blocks")
    if block_duration != 24:
        args += ["--block-duration", str(block_duration)]
    return _run_cli(*args)


# Capacity Block purchasing — disabled by default.
# Set GCO_ENABLE_CAPACITY_PURCHASE=true to enable.
if os.environ.get("GCO_ENABLE_CAPACITY_PURCHASE", "").lower() == "true":

    @mcp.tool()
    @audit_logged
    def reserve_capacity(
        offering_id: str,
        region: str,
        dry_run: bool = False,
    ) -> str:
        """Purchase a Capacity Block offering by its ID.

        Use reservation_check first to find available offerings and their IDs,
        then purchase with this tool. Use dry_run=True to validate without purchasing.

        Args:
            offering_id: Capacity Block offering ID (cb-xxx) from reservation_check.
            region: AWS region where the offering exists.
            dry_run: If True, validate the offering without purchasing (no cost).
        """
        args = ["capacity", "reserve", "-o", offering_id, "-r", region]
        if dry_run:
            args.append("--dry-run")
        return _run_cli(*args)


# =============================================================================
# INFERENCE TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def deploy_inference(
    name: str,
    image: str,
    gpu_count: int = 1,
    replicas: int = 1,
    port: int = 8000,
    region: str | None = None,
    env_vars: list[str] | None = None,
) -> str:
    """Deploy an inference endpoint across regions.

    Args:
        name: Endpoint name (e.g. my-llm).
        image: Container image (e.g. vllm/vllm-openai:v0.19.1).
        gpu_count: GPUs per replica.
        replicas: Number of replicas per region.
        port: Container port.
        region: Target region(s). Omit for all deployed regions.
        env_vars: Environment variables as KEY=VALUE strings.
    """
    args = [
        "inference",
        "deploy",
        name,
        "-i",
        image,
        "--gpu-count",
        str(gpu_count),
        "--replicas",
        str(replicas),
        "--port",
        str(port),
    ]
    if region:
        args += ["-r", region]
    for env in env_vars or []:
        args += ["-e", env]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def list_inference_endpoints(state: str | None = None, region: str | None = None) -> str:
    """List all inference endpoints.

    Args:
        state: Filter by state (deploying, running, stopped, deleted).
        region: Filter by region.
    """
    args = ["inference", "list"]
    if state:
        args += ["--state", state]
    if region:
        args += ["-r", region]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def inference_status(name: str) -> str:
    """Get detailed status of an inference endpoint including per-region breakdown.

    Args:
        name: Endpoint name.
    """
    return _run_cli("inference", "status", name)


@mcp.tool()
@audit_logged
def scale_inference(name: str, replicas: int) -> str:
    """Scale an inference endpoint.

    Args:
        name: Endpoint name.
        replicas: Target replica count.
    """
    return _run_cli("inference", "scale", name, "--replicas", str(replicas))


@mcp.tool()
@audit_logged
def update_inference_image(name: str, image: str) -> str:
    """Rolling update of an inference endpoint's container image.

    Args:
        name: Endpoint name.
        image: New container image.
    """
    return _run_cli("inference", "update-image", name, "-i", image)


@mcp.tool()
@audit_logged
def stop_inference(name: str) -> str:
    """Stop an inference endpoint (scales to zero, keeps config).

    Args:
        name: Endpoint name.
    """
    return _run_cli("inference", "stop", name, "-y")


@mcp.tool()
@audit_logged
def start_inference(name: str) -> str:
    """Start a stopped inference endpoint.

    Args:
        name: Endpoint name.
    """
    return _run_cli("inference", "start", name)


@mcp.tool()
@audit_logged
def delete_inference(name: str) -> str:
    """Delete an inference endpoint.

    Args:
        name: Endpoint name.
    """
    return _run_cli("inference", "delete", name, "-y")


@mcp.tool()
@audit_logged
def canary_deploy(name: str, image: str, weight: int = 10) -> str:
    """Start a canary deployment (A/B test a new image version).

    Args:
        name: Endpoint name.
        image: New image to canary.
        weight: Percentage of traffic to send to canary (1-99).
    """
    return _run_cli("inference", "canary", name, "-i", image, "--weight", str(weight))


@mcp.tool()
@audit_logged
def promote_canary(name: str) -> str:
    """Promote canary to primary (100% traffic to new version).

    Args:
        name: Endpoint name.
    """
    return _run_cli("inference", "promote", name, "-y")


@mcp.tool()
@audit_logged
def rollback_canary(name: str) -> str:
    """Rollback canary (remove canary, 100% traffic to primary).

    Args:
        name: Endpoint name.
    """
    return _run_cli("inference", "rollback", name, "-y")


@mcp.tool()
@audit_logged
def invoke_inference(
    name: str,
    prompt: str,
    max_tokens: int = 100,
    api_path: str | None = None,
    stream: bool = False,
    region: str | None = None,
) -> str:
    """Send a prompt to an inference endpoint and return the generated text.

    Automatically discovers the endpoint's ingress path, detects the serving
    framework (vLLM, TGI, Triton), and routes the request through the API
    Gateway with SigV4 authentication.

    Use this for single-turn text completions. For multi-turn conversations
    with chat models, use chat_inference instead.

    Args:
        name: Endpoint name (e.g. my-llm).
        prompt: Text prompt to send to the model.
        max_tokens: Maximum tokens to generate (default: 100).
        api_path: Override the API sub-path (default: auto-detect from framework).
        stream: Enable streaming for lower time-to-first-token (default: false).
        region: Target region for the request (default: nearest via Global Accelerator).
    """
    args = ["inference", "invoke", name, "-p", prompt, "--max-tokens", str(max_tokens)]
    if api_path:
        args += ["--path", api_path]
    if stream:
        args.append("--stream")
    if region:
        args += ["-r", region]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def chat_inference(
    name: str,
    messages: list[dict[str, str]],
    max_tokens: int = 256,
    temperature: float | None = None,
    stream: bool = False,
    region: str | None = None,
) -> str:
    """Send a multi-turn chat conversation to an inference endpoint.

    Sends an OpenAI-compatible /v1/chat/completions request. Works with
    vLLM, TGI (with --api-protocol openai), and any OpenAI-compatible server.

    Each message in the list should have 'role' (system/user/assistant) and
    'content' keys.

    Args:
        name: Endpoint name (e.g. my-llm).
        messages: List of chat messages, e.g. [{"role": "user", "content": "Hello"}].
        max_tokens: Maximum tokens to generate (default: 256).
        temperature: Sampling temperature (optional, server default if omitted).
        stream: Enable streaming for lower time-to-first-token (default: false).
        region: Target region for the request.
    """
    body: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, "stream": stream}
    if temperature is not None:
        body["temperature"] = temperature
    data_str = json.dumps(body)
    args = [
        "inference",
        "invoke",
        name,
        "-d",
        data_str,
        "--path",
        "/v1/chat/completions",
    ]
    if stream:
        args.append("--stream")
    if region:
        args += ["-r", region]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def inference_health(name: str, region: str | None = None) -> str:
    """Check if an inference endpoint is healthy and ready to serve requests.

    Hits the endpoint's health check path and returns status and latency.
    Useful to verify readiness before sending inference requests.

    Args:
        name: Endpoint name.
        region: Target region to check (default: nearest via Global Accelerator).
    """
    args = ["inference", "health", name]
    if region:
        args += ["-r", region]
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def list_endpoint_models(name: str, region: str | None = None) -> str:
    """List models loaded on an inference endpoint.

    Queries the endpoint's /v1/models path (OpenAI-compatible) to discover
    which models are loaded, their context length, and other metadata.
    Works with vLLM and other OpenAI-compatible servers.

    Args:
        name: Endpoint name.
        region: Target region to query.
    """
    args = ["inference", "models", name]
    if region:
        args += ["-r", region]
    return _run_cli(*args)


# =============================================================================
# COST TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def cost_summary(days: int = 30) -> str:
    """Get total GCO spend broken down by AWS service.

    Args:
        days: Number of days to look back.
    """
    return _run_cli("costs", "summary", "--days", str(days))


@mcp.tool()
@audit_logged
def cost_by_region(days: int = 30) -> str:
    """Get cost breakdown by AWS region.

    Args:
        days: Number of days to look back.
    """
    return _run_cli("costs", "regions", "--days", str(days))


@mcp.tool()
@audit_logged
def cost_trend(days: int = 14) -> str:
    """Get daily cost trend.

    Args:
        days: Number of days to show.
    """
    return _run_cli("costs", "trend", "--days", str(days))


@mcp.tool()
@audit_logged
def cost_forecast(days_ahead: int = 30) -> str:
    """Forecast GCO costs for the next N days.

    Args:
        days_ahead: Days to forecast ahead.
    """
    return _run_cli("costs", "forecast", "--days", str(days_ahead))


# =============================================================================
# STACK / INFRASTRUCTURE TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def list_stacks() -> str:
    """List all GCO CDK stacks."""
    return _run_cli("stacks", "list")


@mcp.tool()
@audit_logged
def stack_status(stack_name: str, region: str) -> str:
    """Get detailed status of a CloudFormation stack.

    Args:
        stack_name: Stack name (e.g. gco-us-east-1).
        region: AWS region.
    """
    return _run_cli("stacks", "status", stack_name, "-r", region)


@mcp.tool()
@audit_logged
def setup_cluster_access(cluster: str | None = None, region: str | None = None) -> str:
    """Configure kubectl access to a GCO EKS cluster.

    Updates kubeconfig, creates an EKS access entry for your IAM principal,
    and associates the cluster admin policy. Handles assumed roles automatically.

    Args:
        cluster: Cluster name (default: gco-{region}).
        region: AWS region (default: first deployment region from cdk.json).
    """
    args = ["stacks", "access"]
    if cluster:
        args.extend(["-c", cluster])
    if region:
        args.extend(["-r", region])
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def fsx_status() -> str:
    """Check FSx for Lustre configuration status."""
    return _run_cli("stacks", "fsx", "status")


# =============================================================================
# FILE STORAGE TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def list_storage_contents(region: str, path: str = "/") -> str:
    """List contents of shared EFS storage.

    Args:
        region: AWS region.
        path: Directory path to list (default: root).
    """
    args = ["files", "ls", "-r", region]
    if path != "/":
        args.append(path)
    return _run_cli(*args)


@mcp.tool()
@audit_logged
def list_file_systems(region: str | None = None) -> str:
    """List EFS and FSx file systems.

    Args:
        region: Specific region, or omit for all.
    """
    args = ["files", "list"]
    if region:
        args += ["-r", region]
    return _run_cli(*args)


# =============================================================================
# MODEL WEIGHT TOOLS
# =============================================================================


@mcp.tool()
@audit_logged
def list_models() -> str:
    """List all uploaded model weights in the S3 bucket."""
    return _run_cli("models", "list")


@mcp.tool()
@audit_logged
def get_model_uri(model_name: str) -> str:
    """Get the S3 URI for a model (for use with --model-source).

    Args:
        model_name: Name of the model.
    """
    return _run_cli("models", "uri", model_name)


# =============================================================================
# DOCUMENTATION RESOURCES
# =============================================================================

DOCS_DIR = PROJECT_ROOT / "docs"
EXAMPLES_DIR = PROJECT_ROOT / "examples"


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
    sections.append("- `docs://gco/examples/README` — Examples overview and usage guide\n")
    # Categorize examples for easier discovery
    job_examples = []
    inference_examples = []
    dag_examples = []
    scheduler_examples = []
    storage_examples = []
    other_examples = []
    for f in sorted(EXAMPLES_DIR.glob("*.yaml")):
        name = f.stem
        entry = f"- `docs://gco/examples/{name}` — {name}"
        if "inference" in name:
            inference_examples.append(entry)
        elif "dag" in name or "pipeline" in name:
            dag_examples.append(entry)
        elif any(k in name for k in ("volcano", "yunikorn", "kueue", "keda", "slurm")):
            scheduler_examples.append(entry)
        elif any(k in name for k in ("efs", "fsx", "valkey")):
            storage_examples.append(entry)
        elif any(k in name for k in ("job", "gpu", "training", "simple", "model-download")):
            job_examples.append(entry)
        else:
            other_examples.append(entry)

    if job_examples:
        sections.append("### Jobs & Training")
        sections.extend(job_examples)
    if inference_examples:
        sections.append("\n### Inference")
        sections.extend(inference_examples)
    if dag_examples:
        sections.append("\n### DAG / Pipelines")
        sections.extend(dag_examples)
    if scheduler_examples:
        sections.append("\n### Schedulers")
        sections.extend(scheduler_examples)
    if storage_examples:
        sections.append("\n### Storage")
        sections.extend(storage_examples)
    if other_examples:
        sections.append("\n### Other")
        sections.extend(other_examples)
    sections.append("\n## Other Resource Groups")
    sections.append("- `k8s://gco/manifests/index` — Kubernetes manifests deployed to EKS")
    sections.append("- `iam://gco/policies/index` — IAM policy templates")
    sections.append("- `infra://gco/index` — Dockerfiles, Helm charts, CI/CD config")
    sections.append("- `source://gco/index` — Source code browser")
    sections.append("- `demos://gco/index` — Demo walkthroughs and presentation materials")
    sections.append("- `clients://gco/index` — API client examples (Python, curl, AWS CLI)")
    sections.append("- `scripts://gco/index` — Utility scripts")
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


@mcp.resource("docs://gco/examples/{example_name}")
def example_resource(example_name: str) -> str:
    """Read an example manifest by name (e.g. simple-job, inference-vllm)."""
    path = EXAMPLES_DIR / f"{example_name}.yaml"
    if not path.is_file():
        available = [f.stem for f in EXAMPLES_DIR.glob("*.yaml")]
        return f"Example '{example_name}' not found. Available: {', '.join(available)}"
    return path.read_text()


# =============================================================================
# SOURCE CODE RESOURCES
# =============================================================================

_SOURCE_DIRS = {
    "gco": PROJECT_ROOT / "gco",
    "cli": PROJECT_ROOT / "cli",
    "lambda": PROJECT_ROOT / "lambda",
    "mcp": PROJECT_ROOT / "mcp",
    "scripts": PROJECT_ROOT / "scripts",
    "demo": PROJECT_ROOT / "demo",
    "dockerfiles": PROJECT_ROOT / "dockerfiles",
}
_SKIP_DIRS = {
    "__pycache__",
    ".git",
    "cdk.out",
    "node_modules",
    "kubectl-applier-simple-build",
    "helm-installer-build",
}
_SOURCE_EXTENSIONS = {".py", ".yaml", ".yml", ".json", ".txt", ".toml", ".cfg", ".sh", ".md"}


def _list_source_files(base: Path) -> list[Path]:
    """Walk a directory and return all source files, skipping noise."""
    files = []
    for p in sorted(base.rglob("*")):
        if any(skip in p.parts for skip in _SKIP_DIRS):
            continue
        if p.is_file() and p.suffix in _SOURCE_EXTENSIONS:
            files.append(p)
    return files


@mcp.resource("source://gco/index")
def source_index() -> str:
    """List all source code files available for reading, grouped by package."""
    sections = ["# GCO Source Code Index\n"]
    sections.append("## Project Config")
    config_files = [
        "pyproject.toml",
        "cdk.json",
        "app.py",
        "Dockerfile.dev",
        ".gitlab-ci.yml",
        ".pre-commit-config.yaml",
        ".flake8",
        ".yamllint.yml",
        ".checkov.yaml",
        ".kics.yaml",
        ".gitleaks.toml",
        ".semgrepignore",
        ".dockerignore",
        ".gitignore",
    ]
    for name in config_files:
        if (PROJECT_ROOT / name).is_file():
            sections.append(f"- `source://gco/config/{name}`")
    for pkg, base in _SOURCE_DIRS.items():
        if not base.is_dir():
            continue
        files = _list_source_files(base)
        if not files:
            continue
        sections.append(f"\n## {pkg}/ ({len(files)} files)")
        for f in files:
            rel = f.relative_to(PROJECT_ROOT)
            sections.append(f"- `source://gco/file/{rel}`")
    return "\n".join(sections)


@mcp.resource("source://gco/config/{filename}")
def config_file_resource(filename: str) -> str:
    """Read a top-level project config file (pyproject.toml, cdk.json, etc.)."""
    allowed = {
        "pyproject.toml",
        "cdk.json",
        "app.py",
        "Dockerfile.dev",
        ".gitlab-ci.yml",
        ".pre-commit-config.yaml",
        ".flake8",
        ".yamllint.yml",
        ".checkov.yaml",
        ".kics.yaml",
        ".gitleaks.toml",
        ".semgrepignore",
        ".dockerignore",
        ".gitignore",
    }
    if filename not in allowed:
        return f"Not available. Allowed: {', '.join(sorted(allowed))}"
    path = PROJECT_ROOT / filename
    if not path.is_file():
        return f"File '{filename}' not found."
    return path.read_text()


@mcp.resource("source://gco/file/{filepath*}")
def source_file_resource(filepath: str) -> str:
    """Read any source file by its path relative to the project root.

    Args:
        filepath: Relative path like 'gco/services/health_monitor.py' or 'cli/jobs.py'.
    """
    path = (PROJECT_ROOT / filepath).resolve()
    if not str(path).startswith(str(PROJECT_ROOT.resolve())):
        return "Access denied: path is outside the project."
    if any(skip in path.parts for skip in _SKIP_DIRS):
        return "Access denied: path is in a skipped directory."
    if not path.is_file():
        return f"File '{filepath}' not found."
    if path.suffix not in _SOURCE_EXTENSIONS:
        return f"File type '{path.suffix}' not served. Allowed: {', '.join(_SOURCE_EXTENSIONS)}"
    return path.read_text()


# =============================================================================
# KUBERNETES MANIFEST RESOURCES
# =============================================================================

MANIFESTS_DIR = PROJECT_ROOT / "lambda" / "kubectl-applier-simple" / "manifests"


@mcp.resource("k8s://gco/manifests/index")
def k8s_manifests_index() -> str:
    """List all Kubernetes manifests deployed to the EKS cluster.

    These are the actual YAML manifests applied during stack deployment via the
    kubectl-applier Lambda. They define namespaces, RBAC, deployments, services,
    ingress, NodePools, storage classes, and network policies.
    """
    lines = ["# Kubernetes Cluster Manifests\n"]
    lines.append("Applied in order during `gco stacks deploy`:\n")
    for f in sorted(MANIFESTS_DIR.glob("*.yaml")):
        lines.append(f"- `k8s://gco/manifests/{f.name}` — {f.stem}")
    readme = MANIFESTS_DIR / "README.md"
    if readme.is_file():
        lines.append("\n- `k8s://gco/manifests/README.md` — manifest documentation")
    return "\n".join(lines)


@mcp.resource("k8s://gco/manifests/{filename}")
def k8s_manifest_resource(filename: str) -> str:
    """Read a Kubernetes manifest that gets applied to the EKS cluster.

    Args:
        filename: Manifest filename (e.g. 30-health-monitor.yaml).
    """
    path = MANIFESTS_DIR / filename
    if not path.is_file():
        available = sorted(f.name for f in MANIFESTS_DIR.glob("*") if f.is_file())
        return f"Manifest '{filename}' not found. Available:\n" + "\n".join(available)
    return path.read_text()


# =============================================================================
# IAM POLICY RESOURCES
# =============================================================================

IAM_POLICIES_DIR = PROJECT_ROOT / "docs" / "iam-policies"


@mcp.resource("iam://gco/policies/index")
def iam_policies_index() -> str:
    """List available IAM policy templates for GCO access control.

    These are reference policies for granting users/roles access to the
    GCO API Gateway. Includes full-access, read-only, and
    namespace-restricted variants.
    """
    lines = ["# IAM Policy Templates\n"]
    for f in sorted(IAM_POLICIES_DIR.glob("*.json")):
        lines.append(f"- `iam://gco/policies/{f.name}` — {f.stem}")
    readme = IAM_POLICIES_DIR / "README.md"
    if readme.is_file():
        lines.append("\n- `iam://gco/policies/README.md` — policy documentation")
    return "\n".join(lines)


@mcp.resource("iam://gco/policies/{filename}")
def iam_policy_resource(filename: str) -> str:
    """Read an IAM policy template.

    Args:
        filename: Policy filename (e.g. full-access-policy.json).
    """
    path = IAM_POLICIES_DIR / filename
    if not path.is_file():
        available = sorted(f.name for f in IAM_POLICIES_DIR.glob("*") if f.is_file())
        return f"Policy '{filename}' not found. Available:\n" + "\n".join(available)
    return path.read_text()


# =============================================================================
# INFRASTRUCTURE CONFIG RESOURCES
# =============================================================================

DOCKERFILES_DIR = PROJECT_ROOT / "dockerfiles"
HELM_CHARTS_FILE = PROJECT_ROOT / "lambda" / "helm-installer" / "charts.yaml"


@mcp.resource("infra://gco/index")
def infra_index() -> str:
    """List infrastructure configuration files — Dockerfiles, Helm charts, CI/CD.

    These define how GCO services are built and deployed.
    """
    lines = ["# Infrastructure Configuration\n"]
    lines.append("## Dockerfiles")
    readme = DOCKERFILES_DIR / "README.md"
    if readme.is_file():
        lines.append("- `infra://gco/dockerfiles/README.md` — Dockerfiles overview")
    for f in sorted(DOCKERFILES_DIR.iterdir()):
        if f.is_file() and not f.name.startswith(".") and f.name != "README.md":
            lines.append(f"- `infra://gco/dockerfiles/{f.name}`")
    lines.append("\n## Helm Charts")
    if HELM_CHARTS_FILE.is_file():
        lines.append("- `infra://gco/helm/charts.yaml` — Helm chart versions and config")
    lines.append("\n## CI/CD")
    lines.append("- `source://gco/config/.gitlab-ci.yml` — GitLab CI pipeline")
    lines.append("- `source://gco/config/.pre-commit-config.yaml` — Pre-commit hooks")
    lines.append("\n## Security & Linting")
    lines.append("- `source://gco/config/.checkov.yaml` — Checkov security scanner config")
    lines.append("- `source://gco/config/.kics.yaml` — KICS security scanner config")
    lines.append("- `source://gco/config/.gitleaks.toml` — Gitleaks secret scanner config")
    lines.append("- `source://gco/config/.semgrepignore` — Semgrep ignore patterns")
    lines.append("- `source://gco/config/.flake8` — Flake8 linter config")
    lines.append("- `source://gco/config/.yamllint.yml` — YAML linter config")
    lines.append("\n## CDK Configuration")
    lines.append("- `source://gco/config/cdk.json` — CDK deployment configuration")
    lines.append("- `source://gco/config/app.py` — CDK app entry point")
    lines.append(
        "- `source://gco/config/pyproject.toml` — Python project metadata and dependencies"
    )
    lines.append("\n## Related Resources")
    lines.append("- `scripts://gco/index` — Utility scripts for operations")
    lines.append("- `demos://gco/index` — Demo walkthroughs and scripts")
    return "\n".join(lines)


@mcp.resource("infra://gco/dockerfiles/{filename}")
def dockerfile_resource(filename: str) -> str:
    """Read a Dockerfile, requirements file, or README for a GCO service.

    Args:
        filename: Dockerfile name (e.g. health-monitor-dockerfile, README.md).
    """
    path = DOCKERFILES_DIR / filename
    if not path.is_file():
        available = sorted(f.name for f in DOCKERFILES_DIR.iterdir() if f.is_file())
        return f"File '{filename}' not found. Available:\n" + "\n".join(available)
    return path.read_text()


@mcp.resource("infra://gco/helm/charts.yaml")
def helm_charts_resource() -> str:
    """Read the Helm charts configuration (chart names, versions, values)."""
    if not HELM_CHARTS_FILE.is_file():
        return "charts.yaml not found."
    return HELM_CHARTS_FILE.read_text()


# =============================================================================
# DEMO & WALKTHROUGH RESOURCES
# =============================================================================

DEMO_DIR = PROJECT_ROOT / "demo"
_DEMO_EXTENSIONS = {".md", ".sh", ".py"}


@mcp.resource("demos://gco/index")
def demos_index() -> str:
    """List demo walkthroughs, live demo scripts, and presentation materials.

    Includes step-by-step demo scripts for infrastructure, jobs, inference,
    and automated terminal recordings.
    """
    lines = ["# Demo & Walkthrough Resources\n"]
    lines.append("## Walkthroughs")
    for name in ("DEMO_WALKTHROUGH", "INFERENCE_WALKTHROUGH", "LIVE_DEMO"):
        path = DEMO_DIR / f"{name}.md"
        if path.is_file():
            lines.append(f"- `demos://gco/{name}` — {name.replace('_', ' ').title()}")
    lines.append("\n- `demos://gco/README` — Demo starter kit overview")
    lines.append("\n## Live Demo Scripts")
    for name in (
        "live_demo.sh",
        "lib_demo.sh",
        "record_demo.sh",
        "record_deploy.sh",
        "record_destroy.sh",
    ):
        path = DEMO_DIR / name
        if path.is_file():
            lines.append(f"- `demos://gco/{name}` — {name}")
    lines.append("\n## Utilities")
    path = DEMO_DIR / "md_to_pdf.py"
    if path.is_file():
        lines.append("- `demos://gco/md_to_pdf.py` — Markdown to PDF converter")
    return "\n".join(lines)


@mcp.resource("demos://gco/{filename}")
def demo_resource(filename: str) -> str:
    """Read a demo walkthrough, script, or utility file.

    Args:
        filename: Demo filename (e.g. DEMO_WALKTHROUGH, live_demo.sh).
    """
    # Try with .md extension first for walkthroughs
    path = DEMO_DIR / filename
    if not path.is_file():
        path = DEMO_DIR / f"{filename}.md"
    if not path.is_file():
        available = sorted(
            f.name for f in DEMO_DIR.iterdir() if f.is_file() and f.suffix in _DEMO_EXTENSIONS
        )
        return f"Demo file '{filename}' not found. Available:\n" + "\n".join(available)
    if path.suffix not in _DEMO_EXTENSIONS:
        return f"File type '{path.suffix}' not served. Allowed: {', '.join(_DEMO_EXTENSIONS)}"
    return path.read_text()


# =============================================================================
# API CLIENT EXAMPLE RESOURCES
# =============================================================================

CLIENT_EXAMPLES_DIR = PROJECT_ROOT / "docs" / "client-examples"
_CLIENT_EXTENSIONS = {".py", ".sh", ".md"}


@mcp.resource("clients://gco/index")
def clients_index() -> str:
    """List API client examples for interacting with the GCO API Gateway.

    Includes Python (boto3), curl with SigV4 proxy, and AWS CLI examples.
    All examples demonstrate SigV4 authentication.
    """
    lines = ["# API Client Examples\n"]
    lines.append("- `clients://gco/README` — Overview, setup, and API reference\n")
    for f in sorted(CLIENT_EXAMPLES_DIR.iterdir()):
        if f.is_file() and f.suffix in _CLIENT_EXTENSIONS and f.name != "README.md":
            desc = f.stem.replace("_", " ").title()
            lines.append(f"- `clients://gco/{f.name}` — {desc}")
    return "\n".join(lines)


@mcp.resource("clients://gco/{filename}")
def client_example_resource(filename: str) -> str:
    """Read an API client example file.

    Args:
        filename: Client example filename (e.g. python_boto3_example.py, README).
    """
    path = CLIENT_EXAMPLES_DIR / filename
    if not path.is_file():
        # Try with .md extension for README
        path = CLIENT_EXAMPLES_DIR / f"{filename}.md"
    if not path.is_file():
        available = sorted(f.name for f in CLIENT_EXAMPLES_DIR.iterdir() if f.is_file())
        return f"Client example '{filename}' not found. Available:\n" + "\n".join(available)
    if path.suffix not in _CLIENT_EXTENSIONS:
        return f"File type '{path.suffix}' not served."
    return path.read_text()


# =============================================================================
# UTILITY SCRIPT RESOURCES
# =============================================================================

SCRIPTS_DIR = PROJECT_ROOT / "scripts"
_SCRIPT_EXTENSIONS = {".py", ".sh"}


@mcp.resource("scripts://gco/index")
def scripts_index() -> str:
    """List utility scripts for cluster access, versioning, testing, and operations.

    These scripts handle common operational tasks like configuring kubectl,
    bumping versions, testing CDK synthesis, and validating webhooks.
    """
    lines = ["# Utility Scripts\n"]
    readme = SCRIPTS_DIR / "README.md"
    if readme.is_file():
        lines.append("- `scripts://gco/README` — Scripts overview and usage\n")
    for f in sorted(SCRIPTS_DIR.iterdir()):
        if f.is_file() and f.suffix in _SCRIPT_EXTENSIONS:
            desc = f.stem.replace("_", " ").replace("-", " ").title()
            lines.append(f"- `scripts://gco/{f.name}` — {desc}")
    return "\n".join(lines)


@mcp.resource("scripts://gco/{filename}")
def script_resource(filename: str) -> str:
    """Read a utility script.

    Args:
        filename: Script filename (e.g. setup-cluster-access.sh, bump_version.py, README).
    """
    path = SCRIPTS_DIR / filename
    if not path.is_file():
        # Try with .md extension for README
        path = SCRIPTS_DIR / f"{filename}.md"
    if not path.is_file():
        available = sorted(
            f.name
            for f in SCRIPTS_DIR.iterdir()
            if f.is_file() and f.suffix in (_SCRIPT_EXTENSIONS | {".md"})
        )
        return f"Script '{filename}' not found. Available:\n" + "\n".join(available)
    if path.suffix not in (_SCRIPT_EXTENSIONS | {".md"}):
        return f"File type '{path.suffix}' not served."
    return path.read_text()


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    mcp.run()
