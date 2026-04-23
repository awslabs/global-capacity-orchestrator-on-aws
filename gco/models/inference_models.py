"""
Data models for inference endpoint management.

Defines the schema for inference endpoints stored in DynamoDB and
used by the inference_monitor reconciliation loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EndpointState(StrEnum):
    """Desired state for an inference endpoint."""

    DEPLOYING = "deploying"
    RUNNING = "running"
    STOPPED = "stopped"
    DELETED = "deleted"


class RegionSyncState(StrEnum):
    """Sync state of an endpoint in a specific region."""

    PENDING = "pending"
    CREATING = "creating"
    RUNNING = "running"
    UPDATING = "updating"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DELETING = "deleting"
    DELETED = "deleted"
    ERROR = "error"


@dataclass
class InferenceEndpointSpec:
    """Specification for an inference endpoint deployment."""

    image: str
    port: int = 8000
    replicas: int = 1
    gpu_count: int = 1
    gpu_type: str | None = None  # e.g. "g5.xlarge" — used for nodeSelector
    model_path: str | None = None  # EFS/FSx path for model weights
    model_source: str | None = None  # S3 URI (s3://bucket/path) or HuggingFace repo ID
    health_check_path: str = "/health"
    env: dict[str, str] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    command: list[str] | None = None
    args: list[str] | None = None
    tolerations: list[dict[str, Any]] | None = None
    node_selector: dict[str, str] | None = None
    autoscaling: dict[str, Any] | None = (
        None  # {enabled, min_replicas, max_replicas, metrics: [{type, target}]}
    )
    # Canary deployment fields
    canary: dict[str, Any] | None = None  # {image, weight, replicas}
    # Capacity type: "on-demand", "spot", or "mixed" (base on-demand, overflow spot)
    capacity_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "image": self.image,
            "port": self.port,
            "replicas": self.replicas,
            "gpu_count": self.gpu_count,
            "health_check_path": self.health_check_path,
        }
        if self.gpu_type:
            result["gpu_type"] = self.gpu_type
        if self.model_path:
            result["model_path"] = self.model_path
        if self.model_source:
            result["model_source"] = self.model_source
        if self.env:
            result["env"] = self.env
        if self.resources:
            result["resources"] = self.resources
        if self.command:
            result["command"] = self.command
        if self.args:
            result["args"] = self.args
        if self.tolerations:
            result["tolerations"] = self.tolerations
        if self.node_selector:
            result["node_selector"] = self.node_selector
        if self.autoscaling:
            result["autoscaling"] = self.autoscaling
        if self.canary:
            result["canary"] = self.canary
        if self.capacity_type:
            result["capacity_type"] = self.capacity_type
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceEndpointSpec:
        return cls(
            image=data["image"],
            port=data.get("port", 8000),
            replicas=data.get("replicas", 1),
            gpu_count=data.get("gpu_count", 1),
            gpu_type=data.get("gpu_type"),
            model_path=data.get("model_path"),
            model_source=data.get("model_source"),
            health_check_path=data.get("health_check_path", "/health"),
            env=data.get("env", {}),
            resources=data.get("resources", {}),
            command=data.get("command"),
            args=data.get("args"),
            tolerations=data.get("tolerations"),
            node_selector=data.get("node_selector"),
            autoscaling=data.get("autoscaling"),
            canary=data.get("canary"),
            capacity_type=data.get("capacity_type"),
        )


@dataclass
class RegionStatus:
    """Status of an endpoint in a specific region."""

    region: str
    state: str = RegionSyncState.PENDING.value
    replicas_ready: int = 0
    replicas_desired: int = 0
    last_sync: str | None = None
    error: str | None = None
    endpoint_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "region": self.region,
            "state": self.state,
            "replicas_ready": self.replicas_ready,
            "replicas_desired": self.replicas_desired,
        }
        if self.last_sync:
            result["last_sync"] = self.last_sync
        if self.error:
            result["error"] = self.error
        if self.endpoint_url:
            result["endpoint_url"] = self.endpoint_url
        return result


@dataclass
class InferenceEndpoint:
    """An inference endpoint managed by GCO."""

    endpoint_name: str
    desired_state: str = EndpointState.DEPLOYING.value
    target_regions: list[str] = field(default_factory=list)
    namespace: str = "gco-inference"
    spec: InferenceEndpointSpec | dict[str, Any] = field(
        default_factory=lambda: InferenceEndpointSpec(image="")
    )
    ingress_path: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    created_by: str | None = None
    region_status: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.spec, dict):
            self.spec = InferenceEndpointSpec.from_dict(self.spec)
        if not self.ingress_path:
            self.ingress_path = f"/inference/{self.endpoint_name}"

    def to_dict(self) -> dict[str, Any]:
        spec_dict = (
            self.spec.to_dict() if isinstance(self.spec, InferenceEndpointSpec) else self.spec
        )
        return {
            "endpoint_name": self.endpoint_name,
            "desired_state": self.desired_state,
            "target_regions": self.target_regions,
            "namespace": self.namespace,
            "spec": spec_dict,
            "ingress_path": self.ingress_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "region_status": self.region_status,
            "labels": self.labels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceEndpoint:
        return cls(
            endpoint_name=data["endpoint_name"],
            desired_state=data.get("desired_state", EndpointState.DEPLOYING.value),
            target_regions=data.get("target_regions", []),
            namespace=data.get("namespace", "gco-inference"),
            spec=data.get("spec", {}),
            ingress_path=data.get("ingress_path", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            created_by=data.get("created_by"),
            region_status=data.get("region_status", {}),
            labels=data.get("labels", {}),
        )
