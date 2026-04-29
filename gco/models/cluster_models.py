"""
Cluster configuration data models for GCO (Global Capacity Orchestrator on AWS).

This module defines dataclasses for EKS cluster configuration including:
- ResourceThresholds: CPU/memory/GPU utilization thresholds for health monitoring
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
class ClusterConfig:
    """Complete configuration for an EKS cluster"""

    region: str
    cluster_name: str
    kubernetes_version: str
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
