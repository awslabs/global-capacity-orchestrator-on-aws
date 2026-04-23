"""
Helm Installer Lambda Handler

Installs and manages Helm charts on EKS clusters via CloudFormation Custom Resources.
Supports KEDA, NVIDIA DRA Driver, and other Helm-based installations.

Features:
- Automatic Helm repo management
- Idempotent install/upgrade operations
- Configurable chart values via CloudFormation properties
- EKS authentication via IAM

Environment Variables:
    CLUSTER_NAME: Name of the EKS cluster
    REGION: AWS region

CloudFormation Properties:
    ClusterName: EKS cluster name
    Region: AWS region
    Charts: Dict of chart configurations to override defaults
    EnabledCharts: List of chart names to enable (overrides charts.yaml)
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import boto3
import urllib3
import yaml

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SUCCESS = "SUCCESS"
FAILED = "FAILED"

# Load default chart configurations
CHARTS_CONFIG_PATH = Path(__file__).parent / "charts.yaml"


def load_charts_config() -> dict[str, Any]:
    """Load chart configurations from charts.yaml."""
    if CHARTS_CONFIG_PATH.exists():
        with open(CHARTS_CONFIG_PATH, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            return loaded if isinstance(loaded, dict) else {"charts": {}}
    return {"charts": {}}


def send_response(
    event: dict[str, Any],
    context: Any,
    status: str,
    data: dict[str, Any],
    physical_id: str,
    reason: str | None = None,
) -> None:
    """Send response to CloudFormation."""
    body = {
        "Status": status,
        "Reason": reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
        "PhysicalResourceId": physical_id,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data,
    }

    logger.info(f"Sending response: {json.dumps(data)}")

    # Timeout is for the CFN response callback (HTTP PUT to S3 presigned URL),
    # not for Helm chart installation. Helm installs use subprocess with --timeout 10m.
    http = urllib3.PoolManager()
    try:
        http.request(
            "PUT",
            event["ResponseURL"],
            body=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
    except Exception as e:
        logger.error(f"Failed to send response: {e}")


def get_eks_token(cluster_name: str, region: str) -> str:
    """Generate EKS authentication token."""
    from botocore.signers import RequestSigner

    session = boto3.Session()
    sts = session.client("sts", region_name=region)
    service_id = sts.meta.service_model.service_id

    signer = RequestSigner(
        service_id, region, "sts", "v4", session.get_credentials(), session.events
    )

    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }

    url = signer.generate_presigned_url(
        params, region_name=region, expires_in=60, operation_name=""
    )
    token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"k8s-aws-v1.{token}"


def configure_kubeconfig(cluster_name: str, region: str) -> str:
    """Configure kubeconfig for EKS cluster and return path."""
    eks = boto3.client("eks", region_name=region)
    cluster = eks.describe_cluster(name=cluster_name)["cluster"]

    # Create kubeconfig
    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": cluster_name,
                "cluster": {
                    "server": cluster["endpoint"],
                    "certificate-authority-data": cluster["certificateAuthority"]["data"],
                },
            }
        ],
        "contexts": [
            {
                "name": cluster_name,
                "context": {
                    "cluster": cluster_name,
                    "user": cluster_name,
                },
            }
        ],
        "current-context": cluster_name,
        "users": [
            {
                "name": cluster_name,
                "user": {
                    "token": get_eks_token(cluster_name, region),
                },
            }
        ],
    }

    # Write kubeconfig to temp file using secure method
    fd, kubeconfig_path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(kubeconfig, f)
    except Exception:
        os.close(fd)
        raise

    return kubeconfig_path


def run_helm(
    args: list[str], kubeconfig: str, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run helm command with kubeconfig."""
    cmd = ["helm"] + args

    helm_env = os.environ.copy()
    helm_env["KUBECONFIG"] = kubeconfig
    # Lambda has read-only filesystem except /tmp
    helm_env["HELM_CACHE_HOME"] = (
        "/tmp/.helm/cache"  # nosec B108 - Lambda runtime requires /tmp for writable storage
    )
    helm_env["HELM_CONFIG_HOME"] = (
        "/tmp/.helm/config"  # nosec B108 - Lambda runtime requires /tmp for writable storage
    )
    helm_env["HELM_DATA_HOME"] = (
        "/tmp/.helm/data"  # nosec B108 - Lambda runtime requires /tmp for writable storage
    )
    if env:
        helm_env.update(env)

    logger.info(f"Running: {' '.join(cmd)}")

    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit - cmd is ["helm"] + static args list; helm_env is a controlled copy of os.environ, no shell=True
        cmd,
        capture_output=True,
        text=True,
        env=helm_env,
        timeout=300,
    )

    if result.stdout:
        logger.info(f"stdout: {result.stdout}")
    if result.stderr:
        logger.warning(f"stderr: {result.stderr}")

    return result.returncode, result.stdout, result.stderr


def add_helm_repo(repo_name: str, repo_url: str, kubeconfig: str) -> bool:
    """Add Helm repository."""
    code, _, _ = run_helm(["repo", "add", repo_name, repo_url, "--force-update"], kubeconfig)
    if code != 0:
        return False

    code, _, _ = run_helm(["repo", "update", repo_name], kubeconfig)
    return code == 0


def install_chart(
    chart_name: str,
    config: dict[str, Any],
    kubeconfig: str,
    value_overrides: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Install or upgrade a Helm chart."""
    repo_name = config["repo_name"]
    repo_url = config["repo_url"]
    chart = config["chart"]
    version = config.get("version")
    namespace = config.get("namespace", "default")
    create_ns = config.get("create_namespace", True)
    values = config.get("values", {})
    use_oci = config.get("use_oci", False)

    # Merge value overrides
    if value_overrides:
        values = deep_merge(values, value_overrides)

    # For OCI registries, we don't need to add a repo
    if not use_oci:
        # Add repo
        if not add_helm_repo(repo_name, repo_url, kubeconfig):
            return False, f"Failed to add repo {repo_name}"
        chart_ref = f"{repo_name}/{chart}"
    else:
        # For OCI, use the full OCI URL
        chart_ref = f"{repo_url}/{chart}"

    # Build helm upgrade --install command
    args = [
        "upgrade",
        "--install",
        chart_name,
        chart_ref,
        "--namespace",
        namespace,
        "--wait",
        "--timeout",
        "10m",
    ]

    if version:
        args.extend(["--version", version])

    if create_ns:
        args.append("--create-namespace")

    # Write values to temp file using secure method
    if values:
        fd, values_file = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(values, f)
        except Exception:
            os.close(fd)
            raise
        args.extend(["--values", values_file])

    code, stdout, stderr = run_helm(args, kubeconfig)

    if code == 0:
        return True, f"Successfully installed {chart_name}"
    else:
        # If the release is stuck in pending-upgrade/pending-install, rollback first
        if "another operation" in stderr.lower() and "in progress" in stderr.lower():
            logger.warning(f"Release {chart_name} stuck in pending state, rolling back...")
            rollback_args = ["rollback", chart_name, "--namespace", namespace, "--wait"]
            rb_code, _, rb_err = run_helm(rollback_args, kubeconfig)
            if rb_code != 0:
                logger.warning(f"Rollback failed for {chart_name}: {rb_err}")
            # Retry the install after rollback
            code2, stdout2, stderr2 = run_helm(args, kubeconfig)
            if code2 == 0:
                return True, f"Successfully installed {chart_name} (after rollback)"
            return False, f"Failed to install {chart_name}: {stderr2}"
        return False, f"Failed to install {chart_name}: {stderr}"


def uninstall_chart(chart_name: str, namespace: str, kubeconfig: str) -> tuple[bool, str]:
    """Uninstall a Helm chart."""
    args = ["uninstall", chart_name, "--namespace", namespace, "--wait"]
    code, _, stderr = run_helm(args, kubeconfig)

    if code == 0:
        return True, f"Successfully uninstalled {chart_name}"
    else:
        # Ignore "not found" errors
        if "not found" in stderr.lower():
            return True, f"Chart {chart_name} not found (already uninstalled)"
        return False, f"Failed to uninstall {chart_name}: {stderr}"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _cleanup_stale_webhooks(kubeconfig: str) -> None:
    """Remove MutatingWebhookConfigurations whose service endpoints are unavailable.

    When a webhook's backing pod is down (evicted, pending, crashed), the webhook
    blocks all API mutations for the resources it intercepts. This function detects
    and temporarily removes such webhooks so other Helm charts can upgrade.
    The webhook will be recreated when its chart is successfully reinstalled.
    """
    try:
        # Use kubectl to check for stale webhooks (simpler than kubernetes Python client)
        code, stdout, _ = run_helm(
            ["--kubeconfig", kubeconfig],  # dummy — we just need the env
            kubeconfig,
        )

        # Get all mutating webhook configs
        import subprocess

        env = os.environ.copy()
        env["KUBECONFIG"] = kubeconfig

        result = subprocess.run(
            [
                "kubectl",
                "get",
                "mutatingwebhookconfigurations",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"Failed to list webhooks: {result.stderr}")
            return

        for webhook_name in result.stdout.strip().split("\n"):
            if not webhook_name:
                continue

            # Check if the webhook's service has ready endpoints
            svc_result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "mutatingwebhookconfiguration",
                    webhook_name,
                    "-o",
                    "jsonpath={.webhooks[0].clientConfig.service.namespace}/{.webhooks[0].clientConfig.service.name}",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            if svc_result.returncode != 0 or "/" not in svc_result.stdout:
                continue

            ns, svc = svc_result.stdout.strip().split("/", 1)

            # Check if the service has ready endpoints
            ep_result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "endpoints",
                    svc,
                    "-n",
                    ns,
                    "-o",
                    "jsonpath={.subsets[*].addresses[*].ip}",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )

            if not ep_result.stdout.strip():
                logger.warning(
                    f"Webhook {webhook_name} has no ready endpoints "
                    f"(service {ns}/{svc}), temporarily removing..."
                )
                subprocess.run(
                    ["kubectl", "delete", "mutatingwebhookconfiguration", webhook_name],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=15,
                )

    except Exception as e:
        logger.warning(f"Webhook cleanup failed (non-fatal): {e}")


def lambda_handler(event: dict[str, Any], context: Any) -> None:
    """Main Lambda handler."""
    logger.info(f"Received event: {json.dumps(event)}")

    request_type = event["RequestType"]
    physical_id = event.get("PhysicalResourceId", f"helm-{event['LogicalResourceId']}")

    try:
        props = event["ResourceProperties"]
        cluster_name = props["ClusterName"]
        region = props["Region"]

        # Load default config and merge with overrides
        default_config = load_charts_config()
        charts_config = default_config.get("charts", {})

        # Apply chart overrides from CloudFormation
        chart_overrides = props.get("Charts", {})
        for chart_name, overrides in chart_overrides.items():
            if chart_name in charts_config:
                charts_config[chart_name] = deep_merge(charts_config[chart_name], overrides)
            else:
                charts_config[chart_name] = overrides

        # Apply enabled charts list
        enabled_charts = props.get("EnabledCharts", [])
        if enabled_charts:
            for chart_name in charts_config:
                charts_config[chart_name]["enabled"] = chart_name in enabled_charts

        # Inject KEDA operator IAM role ARN for IRSA if provided
        keda_operator_role_arn = props.get("KedaOperatorRoleArn")
        if keda_operator_role_arn and "keda" in charts_config:
            logger.info(f"Injecting KEDA operator role ARN: {keda_operator_role_arn}")
            keda_values = charts_config["keda"].setdefault("values", {})
            service_account = keda_values.setdefault("serviceAccount", {})
            operator = service_account.setdefault("operator", {})
            annotations = operator.setdefault("annotations", {})
            annotations["eks.amazonaws.com/role-arn"] = keda_operator_role_arn

        # Configure kubeconfig
        kubeconfig = configure_kubeconfig(cluster_name, region)

        results = {}
        failed = []

        if request_type in ("Create", "Update"):
            # Install/upgrade enabled charts with retry for transient failures
            # (e.g., webhook not ready yet, API server temporarily unavailable)
            max_retries = 3
            retry_delay = 30  # seconds

            # First pass: uninstall disabled charts that were previously installed
            for chart_name, config in charts_config.items():
                if not config.get("enabled", False):
                    namespace = config.get("namespace", "default")
                    logger.info(f"Chart {chart_name} is disabled, checking if installed...")
                    success, message = uninstall_chart(chart_name, namespace, kubeconfig)
                    results[chart_name] = (
                        "uninstalled (disabled)"
                        if "Successfully" in message
                        else "skipped (disabled)"
                    )

            # Second pass: install/upgrade enabled charts
            for chart_name, config in charts_config.items():
                if not config.get("enabled", False):
                    continue

                value_overrides = chart_overrides.get(chart_name, {}).get("values", {})
                success, message = install_chart(chart_name, config, kubeconfig, value_overrides)
                results[chart_name] = message

                if not success:
                    failed.append(chart_name)

            # Retry failed charts — transient issues (webhook races, API timeouts)
            # often resolve after other charts finish installing
            for attempt in range(1, max_retries + 1):
                if not failed:
                    break

                # If failures look like webhook issues, temporarily remove stale
                # MutatingWebhookConfigurations whose endpoints are unavailable.
                # This breaks the deadlock where a down webhook blocks all upgrades.
                if any(
                    "webhook" in results.get(c, "").lower()
                    or "no endpoints" in results.get(c, "").lower()
                    for c in failed
                ):
                    logger.info("Detected webhook-related failures, cleaning stale webhooks...")
                    _cleanup_stale_webhooks(kubeconfig)

                logger.info(
                    f"Retrying {len(failed)} failed chart(s) "
                    f"(attempt {attempt}/{max_retries}, waiting {retry_delay}s)..."
                )
                import time

                time.sleep(retry_delay)

                retry_list = failed.copy()
                failed = []
                for chart_name in retry_list:
                    config = charts_config[chart_name]
                    value_overrides = chart_overrides.get(chart_name, {}).get("values", {})
                    success, message = install_chart(
                        chart_name, config, kubeconfig, value_overrides
                    )
                    results[chart_name] = message
                    if not success:
                        failed.append(chart_name)
                    else:
                        logger.info(f"Retry succeeded for {chart_name}")

            if failed:
                logger.warning(f"Charts still failing after {max_retries} retries: {failed}")

        elif request_type == "Delete":
            # Uninstall charts (in reverse order)
            for chart_name, config in reversed(list(charts_config.items())):
                if not config.get("enabled", False):
                    continue

                namespace = config.get("namespace", "default")
                success, message = uninstall_chart(chart_name, namespace, kubeconfig)
                results[chart_name] = message

                if not success:
                    failed.append(chart_name)

        # Clean up kubeconfig
        import contextlib

        with contextlib.suppress(Exception):
            os.remove(kubeconfig)

        # Prepare response
        response_data = {
            "Results": json.dumps(results),
            "InstalledCharts": ",".join(
                [k for k, v in results.items() if "Successfully" in str(v)]
            ),
            "FailedCharts": ",".join(failed),
        }

        if failed and request_type != "Delete":
            send_response(
                event,
                context,
                FAILED,
                response_data,
                physical_id,
                f"Failed charts: {', '.join(failed)}",
            )
        else:
            send_response(event, context, SUCCESS, response_data, physical_id)

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        if request_type == "Delete":
            # Always succeed on delete to prevent stack from getting stuck
            send_response(
                event, context, SUCCESS, {"Status": "Forced success on delete"}, physical_id
            )
        else:
            send_response(event, context, FAILED, {}, physical_id, str(e))
