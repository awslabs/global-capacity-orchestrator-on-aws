"""
Cluster configuration data models for GCO (Global Capacity Orchestrator on AWS).

This module defines dataclasses for EKS cluster configuration including:
- ResourceThresholds: CPU/memory/GPU utilization thresholds for health monitoring
- NodeGroupConfig: EKS node group configuration (instance types, scaling, labels)
- ClusterConfig: Complete cluster configuration combining all settings

All models include validation in __post_init__ to ensure data integrity.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResourceThresholds:
    """
    Resource utilization thresholds for cluster health monitoring.

    When utilization exceeds these thresholds, the cluster is considered unhealthy.
    Set any threshold to -1 to disable that check entirely.

    Attributes:
        cpu_threshold: CPU utilization threshold percentage (0-100, or -1 to disable)
        memory_threshold: Memory utilization threshold percentage (0-100, or -1 to disable)
        gpu_threshold: GPU utilization threshold percentage (0-100, or -1 to disable)
        pending_pods_threshold: Max pending pods before unhealthy (-1 to disable, default: 10)
        pending_requested_cpu_vcpus: Max vCPUs requested by pending pods (-1 to disable, default: 100)
        pending_requested_memory_gb: Max GB memory requested by pending pods (-1 to disable, default: 200)
        pending_requested_gpus: Max GPUs requested by pending pods (-1 to disable, default: 8)
    """

    cpu_threshold: int
    memory_threshold: int
    gpu_threshold: int
    pending_pods_threshold: int = 10
    pending_requested_cpu_vcpus: int = 100
    pending_requested_memory_gb: int = 200
    pending_requested_gpus: int = 8

    def is_disabled(self, field_name: str) -> bool:
        """Check if a threshold is disabled (set to -1)."""
        value: int = getattr(self, field_name)
        return value == -1

    def __post_init__(self) -> None:
        """Validate threshold values"""
        for field_name, value in [
            ("cpu_threshold", self.cpu_threshold),
            ("memory_threshold", self.memory_threshold),
            ("gpu_threshold", self.gpu_threshold),
        ]:
            if not isinstance(value, int) or (value != -1 and not 0 <= value <= 100):
                raise ValueError(
                    f"{field_name} must be an integer between 0 and 100 (or -1 to disable), got {value}"
                )

        for field_name, value in [
            ("pending_pods_threshold", self.pending_pods_threshold),
            ("pending_requested_cpu_vcpus", self.pending_requested_cpu_vcpus),
            ("pending_requested_memory_gb", self.pending_requested_memory_gb),
            ("pending_requested_gpus", self.pending_requested_gpus),
        ]:
            if not isinstance(value, int) or (value != -1 and value < 0):
                raise ValueError(
                    f"{field_name} must be a non-negative integer (or -1 to disable), got {value}"
                )


@dataclass
class NodeGroupConfig:
    """Configuration for EKS node groups"""

    name: str
    instance_types: list[str]
    scaling_config: dict[str, int]  # min_size, max_size, desired_size
    labels: dict[str, str]
    taints: list[dict[str, str]]

    def __post_init__(self) -> None:
        """Validate node group configuration"""
        if not self.name:
            raise ValueError("Node group name cannot be empty")

        if not self.instance_types:
            raise ValueError("At least one instance type must be specified")

        required_scaling_keys = {"min_size", "max_size", "desired_size"}
        if not required_scaling_keys.issubset(self.scaling_config.keys()):
            raise ValueError(f"Scaling config must contain keys: {required_scaling_keys}")

        # Validate scaling values
        min_size = self.scaling_config["min_size"]
        max_size = self.scaling_config["max_size"]
        desired_size = self.scaling_config["desired_size"]

        if min_size < 0 or max_size < 0 or desired_size < 0:
            raise ValueError("Scaling values must be non-negative")

        if min_size > max_size:
            raise ValueError("min_size cannot be greater than max_size")

        if desired_size < min_size or desired_size > max_size:
            raise ValueError("desired_size must be between min_size and max_size")


@dataclass
class ClusterConfig:
    """Complete configuration for an EKS cluster"""

    region: str
    cluster_name: str
    kubernetes_version: str
    node_groups: list[NodeGroupConfig]
    addons: list[str]
    resource_thresholds: ResourceThresholds

    def __post_init__(self) -> None:
        """Validate cluster configuration"""
        if not self.region:
            raise ValueError("Region cannot be empty")

        if not self.cluster_name:
            raise ValueError("Cluster name cannot be empty")

        if not self.kubernetes_version:
            raise ValueError("Kubernetes version cannot be empty")

        if not self.node_groups:
            raise ValueError("At least one node group must be specified")

        # Validate node group names are unique
        node_group_names = [ng.name for ng in self.node_groups]
        if len(node_group_names) != len(set(node_group_names)):
            raise ValueError("Node group names must be unique")
