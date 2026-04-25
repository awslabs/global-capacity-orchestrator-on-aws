"""
Inference endpoint management for GCO CLI.

Provides functionality to deploy, manage, and monitor inference endpoints
across multi-region EKS clusters via the DynamoDB-backed reconciliation
pattern (inference_monitor).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .aws_client import get_aws_client
from .config import GCOConfig, get_config

if TYPE_CHECKING:
    from gco.services.inference_store import InferenceEndpointStore

logger = logging.getLogger(__name__)


class InferenceManager:
    """Manages inference endpoints via the DynamoDB store."""

    def __init__(self, config: GCOConfig | None = None):
        self.config = config or get_config()
        self._aws_client = get_aws_client(config)

    def _get_store(self, region: str | None = None) -> InferenceEndpointStore:
        """Get an InferenceEndpointStore for the global region."""
        from gco.services.inference_store import InferenceEndpointStore

        # Use the global region for DynamoDB (same as job store)
        store_region = region or self.config.global_region
        return InferenceEndpointStore(region=store_region)

    def deploy(
        self,
        endpoint_name: str,
        image: str,
        target_regions: list[str] | None = None,
        replicas: int = 1,
        gpu_count: int = 1,
        gpu_type: str | None = None,
        port: int = 8000,
        model_path: str | None = None,
        model_source: str | None = None,
        health_check_path: str = "/health",
        env: dict[str, str] | None = None,
        namespace: str = "gco-inference",
        labels: dict[str, str] | None = None,
        autoscaling: dict[str, Any] | None = None,
        capacity_type: str | None = None,
        extra_args: list[str] | None = None,
        accelerator: str = "nvidia",
        node_selector: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Deploy an inference endpoint to one or more regions.

        The endpoint spec is written to DynamoDB. The inference_monitor
        in each target region picks it up and creates the K8s resources.

        Args:
            endpoint_name: Unique name for the endpoint
            image: Container image (e.g. vllm/vllm-openai:v0.8.0)
            target_regions: Regions to deploy to (default: all deployed regions)
            replicas: Number of replicas per region
            gpu_count: GPUs per replica
            gpu_type: GPU instance type hint for node selector
            port: Container port
            model_path: EFS path for model weights
            health_check_path: Health check endpoint path
            env: Environment variables
            namespace: Kubernetes namespace
            labels: Labels for the endpoint

        Returns:
            Created endpoint record
        """
        if not target_regions:
            stacks = self._aws_client.discover_regional_stacks()
            target_regions = list(stacks.keys())
            if not target_regions:
                raise ValueError("No deployed regions found. Deploy infrastructure first.")

        spec = {
            "image": image,
            "port": port,
            "replicas": replicas,
            "gpu_count": gpu_count,
            "health_check_path": health_check_path,
        }
        if gpu_type:
            spec["gpu_type"] = gpu_type
        if model_path:
            spec["model_path"] = model_path
        if model_source:
            spec["model_source"] = model_source
        if env:
            spec["env"] = env
        if autoscaling:
            spec["autoscaling"] = autoscaling
        if capacity_type:
            spec["capacity_type"] = capacity_type
        if extra_args:
            spec["args"] = extra_args
        if accelerator != "nvidia":
            spec["accelerator"] = accelerator
        if node_selector:
            spec["node_selector"] = node_selector

        store = self._get_store()
        result: dict[str, Any] = store.create_endpoint(
            endpoint_name=endpoint_name,
            spec=spec,
            target_regions=target_regions,
            namespace=namespace,
            labels=labels,
        )
        return result

    def list_endpoints(
        self,
        desired_state: str | None = None,
        region: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all inference endpoints."""
        store = self._get_store()
        result: list[dict[str, Any]] = store.list_endpoints(
            desired_state=desired_state,
            target_region=region,
        )
        return result

    def get_endpoint(self, endpoint_name: str) -> dict[str, Any] | None:
        """Get details of a specific endpoint."""
        store = self._get_store()
        result: dict[str, Any] | None = store.get_endpoint(endpoint_name)
        return result

    def scale(self, endpoint_name: str, replicas: int) -> dict[str, Any] | None:
        """Scale an endpoint to a new replica count."""
        store = self._get_store()
        result: dict[str, Any] | None = store.scale_endpoint(endpoint_name, replicas)
        return result

    def stop(self, endpoint_name: str) -> dict[str, Any] | None:
        """Stop an endpoint (scale to zero, keep resources)."""
        store = self._get_store()
        result: dict[str, Any] | None = store.update_desired_state(endpoint_name, "stopped")
        return result

    def start(self, endpoint_name: str) -> dict[str, Any] | None:
        """Start a stopped endpoint."""
        store = self._get_store()
        result: dict[str, Any] | None = store.update_desired_state(endpoint_name, "running")
        return result

    def delete(self, endpoint_name: str) -> dict[str, Any] | None:
        """Mark an endpoint for deletion (inference_monitor cleans up)."""
        store = self._get_store()
        result: dict[str, Any] | None = store.update_desired_state(endpoint_name, "deleted")
        return result

    def update_image(self, endpoint_name: str, image: str) -> dict[str, Any] | None:
        """Update the container image for an endpoint."""
        store = self._get_store()
        endpoint = store.get_endpoint(endpoint_name)
        if not endpoint:
            return None
        spec = endpoint.get("spec", {})
        spec["image"] = image
        result: dict[str, Any] | None = store.update_spec(endpoint_name, spec)
        return result

    def add_region(self, endpoint_name: str, region: str) -> dict[str, Any] | None:
        """Add a region to an existing endpoint."""
        from datetime import UTC, datetime

        store = self._get_store()
        endpoint = store.get_endpoint(endpoint_name)
        if not endpoint:
            return None
        regions = endpoint.get("target_regions", [])
        if region not in regions:
            regions.append(region)
        # Update via raw DynamoDB update
        try:
            response = store._table.update_item(
                Key={"endpoint_name": endpoint_name},
                UpdateExpression="SET target_regions = :r, updated_at = :u",
                ExpressionAttributeValues={
                    ":r": regions,
                    ":u": datetime.now(UTC).isoformat(),
                },
                ReturnValues="ALL_NEW",
            )
            result: dict[str, Any] | None = response.get("Attributes")
            return result
        except Exception as e:
            logger.error("Failed to add region: %s", e)
            return None

    def remove_region(self, endpoint_name: str, region: str) -> dict[str, Any] | None:
        """Remove a region from an existing endpoint."""
        store = self._get_store()
        endpoint = store.get_endpoint(endpoint_name)
        if not endpoint:
            return None
        regions = endpoint.get("target_regions", [])
        if region in regions:
            regions.remove(region)
        try:
            from datetime import UTC, datetime

            response = store._table.update_item(
                Key={"endpoint_name": endpoint_name},
                UpdateExpression="SET target_regions = :r, updated_at = :u",
                ExpressionAttributeValues={
                    ":r": regions,
                    ":u": datetime.now(UTC).isoformat(),
                },
                ReturnValues="ALL_NEW",
            )
            result: dict[str, Any] | None = response.get("Attributes")
            return result
        except Exception as e:
            logger.error("Failed to remove region: %s", e)
            return None

    def canary_deploy(
        self,
        endpoint_name: str,
        image: str,
        weight: int = 10,
        replicas: int = 1,
    ) -> dict[str, Any] | None:
        """Start a canary deployment for an existing endpoint.

        Creates a canary variant with the new image receiving `weight`%
        of traffic. The primary deployment continues serving the rest.

        Args:
            endpoint_name: Existing endpoint to canary
            image: New container image for the canary
            weight: Percentage of traffic to route to canary (1-99)
            replicas: Number of canary replicas

        Returns:
            Updated endpoint record, or None if endpoint not found
        """
        if not 1 <= weight <= 99:
            raise ValueError("Canary weight must be between 1 and 99")

        store = self._get_store()
        endpoint = store.get_endpoint(endpoint_name)
        if not endpoint:
            return None

        if endpoint.get("desired_state") not in ("running", "deploying"):
            raise ValueError(
                f"Cannot canary an endpoint in '{endpoint.get('desired_state')}' state. "
                "Endpoint must be running."
            )

        spec = endpoint.get("spec", {})
        spec["canary"] = {
            "image": image,
            "weight": weight,
            "replicas": replicas,
        }

        result: dict[str, Any] | None = store.update_spec(endpoint_name, spec)
        return result

    def promote_canary(self, endpoint_name: str) -> dict[str, Any] | None:
        """Promote the canary to primary, removing the canary deployment.

        The primary image is replaced with the canary image, and the
        canary config is removed. All traffic goes to the new image.

        Returns:
            Updated endpoint record, or None if endpoint not found
        """
        store = self._get_store()
        endpoint = store.get_endpoint(endpoint_name)
        if not endpoint:
            return None

        spec = endpoint.get("spec", {})
        canary = spec.get("canary")
        if not canary:
            raise ValueError(f"Endpoint '{endpoint_name}' has no active canary deployment")

        if "image" not in canary:
            raise ValueError(
                f"Canary deployment for '{endpoint_name}' is missing the 'image' field"
            )

        # Swap primary image to canary image
        spec["image"] = canary["image"]
        # Remove canary config
        del spec["canary"]

        result: dict[str, Any] | None = store.update_spec(endpoint_name, spec)
        return result

    def rollback_canary(self, endpoint_name: str) -> dict[str, Any] | None:
        """Remove the canary deployment, keeping the primary unchanged.

        All traffic returns to the primary deployment.

        Returns:
            Updated endpoint record, or None if endpoint not found
        """
        store = self._get_store()
        endpoint = store.get_endpoint(endpoint_name)
        if not endpoint:
            return None

        spec = endpoint.get("spec", {})
        if "canary" not in spec:
            raise ValueError(f"Endpoint '{endpoint_name}' has no active canary deployment")

        del spec["canary"]
        result: dict[str, Any] | None = store.update_spec(endpoint_name, spec)
        return result


def get_inference_manager(config: GCOConfig | None = None) -> InferenceManager:
    """Factory function for InferenceManager."""
    return InferenceManager(config)
