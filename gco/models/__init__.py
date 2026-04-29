"""
Data models for GCO (Global Capacity Orchestrator on AWS).

This module provides Pydantic-style dataclasses for:
- Cluster configuration (EKS settings, thresholds)
- Health monitoring (resource utilization, health status)
- Manifest processing (Kubernetes manifests, submission requests/responses)

All models include validation in __post_init__ to ensure data integrity.
"""

from .cluster_models import ClusterConfig, ResourceThresholds
from .health_models import HealthStatus, RequestedResources, ResourceUtilization
from .inference_models import (
    EndpointState,
    InferenceEndpoint,
    InferenceEndpointSpec,
    RegionStatus,
    RegionSyncState,
)
from .manifest_models import (
    KubernetesManifest,
    ManifestSubmissionRequest,
    ManifestSubmissionResponse,
    ResourceStatus,
)

__all__ = [
    # Cluster configuration models
    "ClusterConfig",
    "ResourceThresholds",
    # Health monitoring models
    "HealthStatus",
    "RequestedResources",
    "ResourceUtilization",
    # Inference endpoint models
    "EndpointState",
    "InferenceEndpoint",
    "InferenceEndpointSpec",
    "RegionStatus",
    "RegionSyncState",
    # Manifest processing models
    "KubernetesManifest",
    "ManifestSubmissionRequest",
    "ManifestSubmissionResponse",
    "ResourceStatus",
]
