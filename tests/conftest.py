"""
Pytest configuration and shared fixtures for GCO tests.

This module provides common fixtures used across multiple test modules,
including mock Kubernetes clients, sample manifests, and configuration objects.
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gco.models import (
    ClusterConfig,
    HealthStatus,
    NodeGroupConfig,
    ResourceThresholds,
    ResourceUtilization,
)

# ============================================================================
# Session-scoped: ensure Lambda build directories exist for CDK tests
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def ensure_lambda_build_dirs():
    """Ensure Lambda build directories exist before any CDK synthesis tests.

    CDK's Code.from_asset() fingerprints these directories during synthesis.
    If they're missing or stale, CDK tests fail with ENOENT errors.
    This fixture runs once per test session and rebuilds if needed.
    """
    kubectl_build = PROJECT_ROOT / "lambda" / "kubectl-applier-simple-build"
    helm_build = PROJECT_ROOT / "lambda" / "helm-installer-build"

    # Rebuild kubectl-applier-simple-build if handler.py is missing
    if not (kubectl_build / "handler.py").exists():
        kubectl_build.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            PROJECT_ROOT / "lambda" / "kubectl-applier-simple" / "handler.py",
            kubectl_build / "handler.py",
        )
        shutil.copytree(
            PROJECT_ROOT / "lambda" / "kubectl-applier-simple" / "manifests",
            kubectl_build / "manifests",
            dirs_exist_ok=True,
        )
        subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit - test fixture: static list [sys.executable,"-m","pip","install",...]; no user input
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "kubernetes",
                "pyyaml",
                "urllib3",
                "-t",
                str(kubectl_build),
                "-q",
            ],
            check=True,
        )

    # Rebuild helm-installer-build if handler.py is missing
    if not (helm_build / "handler.py").exists():
        if helm_build.exists():
            shutil.rmtree(helm_build)
        shutil.copytree(
            PROJECT_ROOT / "lambda" / "helm-installer",
            helm_build,
        )
        # Remove __pycache__ from the copy
        for pycache in helm_build.rglob("__pycache__"):
            shutil.rmtree(pycache)


# ============================================================================
# Model Fixtures
# ============================================================================


@pytest.fixture
def sample_thresholds():
    """Create sample resource thresholds."""
    return ResourceThresholds(cpu_threshold=80, memory_threshold=85, gpu_threshold=90)


@pytest.fixture
def sample_utilization():
    """Create sample resource utilization."""
    return ResourceUtilization(cpu=50.0, memory=60.0, gpu=30.0)


@pytest.fixture
def sample_node_group():
    """Create sample node group configuration."""
    return NodeGroupConfig(
        name="gpu-nodes",
        instance_types=["g4dn.xlarge", "g5.xlarge"],
        scaling_config={"min_size": 0, "max_size": 10, "desired_size": 2},
        labels={"workload-type": "gpu"},
        taints=[{"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}],
    )


@pytest.fixture
def sample_cluster_config(sample_thresholds, sample_node_group):
    """Create sample cluster configuration."""
    return ClusterConfig(
        region="us-east-1",
        cluster_name="gco-us-east-1",
        kubernetes_version="1.35",
        node_groups=[sample_node_group],
        addons=["metrics-server"],
        resource_thresholds=sample_thresholds,
    )


@pytest.fixture
def sample_health_status(sample_thresholds, sample_utilization):
    """Create sample health status."""
    return HealthStatus(
        cluster_id="gco-us-east-1",
        region="us-east-1",
        timestamp=datetime.now(),
        status="healthy",
        resource_utilization=sample_utilization,
        thresholds=sample_thresholds,
        active_jobs=5,
    )


# ============================================================================
# Kubernetes Manifest Fixtures
# ============================================================================


@pytest.fixture
def sample_deployment_manifest():
    """Create sample Kubernetes Deployment manifest."""
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "test-app", "namespace": "default"},
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": "test"}},
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/nginx:latest",
                            "ports": [{"containerPort": 80}],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                        }
                    ]
                },
            },
        },
    }


@pytest.fixture
def sample_job_manifest():
    """Create sample Kubernetes Job manifest."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "test-job", "namespace": "gco-jobs"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "worker",
                            "image": "public.ecr.aws/test/worker:v1",
                            "resources": {
                                "requests": {"cpu": "1", "memory": "2Gi"},
                                "limits": {"cpu": "2", "memory": "4Gi"},
                            },
                        }
                    ],
                    "restartPolicy": "Never",
                }
            }
        },
    }


@pytest.fixture
def sample_gpu_job_manifest():
    """Create sample GPU Job manifest."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "gpu-training-job", "namespace": "gco-jobs"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "trainer",
                            "image": "docker.io/pytorch/pytorch:latest",
                            "resources": {
                                "requests": {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
                                "limits": {"cpu": "8", "memory": "32Gi", "nvidia.com/gpu": "1"},
                            },
                        }
                    ],
                    "restartPolicy": "Never",
                    "tolerations": [
                        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
                    ],
                }
            }
        },
    }


@pytest.fixture
def sample_configmap_manifest():
    """Create sample ConfigMap manifest."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "test-config", "namespace": "default"},
        "data": {"config.yaml": "key: value\nother: setting"},
    }


# ============================================================================
# Mock Fixtures
# ============================================================================


@pytest.fixture
def mock_k8s_config():
    """Mock Kubernetes configuration loading."""
    with (
        patch("kubernetes.config.load_incluster_config") as mock_incluster,
        patch("kubernetes.config.load_kube_config") as mock_kubeconfig,
    ):
        mock_incluster.side_effect = Exception("Not in cluster")
        mock_kubeconfig.return_value = None
        yield {"incluster": mock_incluster, "kubeconfig": mock_kubeconfig}


@pytest.fixture
def mock_k8s_clients():
    """Mock Kubernetes API clients."""
    with (
        patch("kubernetes.client.CoreV1Api") as mock_core,
        patch("kubernetes.client.AppsV1Api") as mock_apps,
        patch("kubernetes.client.BatchV1Api") as mock_batch,
        patch("kubernetes.client.CustomObjectsApi") as mock_custom,
    ):
        yield {
            "core_v1": mock_core.return_value,
            "apps_v1": mock_apps.return_value,
            "batch_v1": mock_batch.return_value,
            "custom_objects": mock_custom.return_value,
        }


@pytest.fixture
def mock_secrets_manager():
    """Mock AWS Secrets Manager client."""
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": '{"token": "test-secret-token"}'
        }
        mock_boto.return_value = mock_client
        yield mock_client


# ============================================================================
# Configuration Fixtures
# ============================================================================


@pytest.fixture
def valid_cdk_context():
    """Create valid CDK context for ConfigLoader tests."""
    return {
        "project_name": "gco",
        "deployment_regions": {
            "global": "us-east-2",
            "api_gateway": "us-east-2",
            "monitoring": "us-east-2",
            "regional": ["us-east-1", "us-west-2"],
        },
        "kubernetes_version": "1.35",
        "resource_thresholds": {"cpu_threshold": 80, "memory_threshold": 85, "gpu_threshold": 90},
        "node_groups": {
            "gpu_instances": ["g4dn.xlarge", "g5.xlarge"],
            "min_size": 0,
            "max_size": 10,
            "desired_size": 2,
        },
        "global_accelerator": {
            "name": "gco-accelerator",
            "health_check_grace_period": 30,
            "health_check_interval": 30,
            "health_check_timeout": 5,
            "health_check_path": "/api/v1/health",
        },
        "alb_config": {
            "health_check_interval": 30,
            "health_check_timeout": 5,
            "healthy_threshold": 2,
            "unhealthy_threshold": 2,
        },
        "manifest_processor": {
            "image": "gco/manifest-processor:latest",
            "replicas": 3,
            "resource_limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "job_validation_policy": {
            "allowed_namespaces": ["default", "gco-jobs"],
            "resource_quotas": {
                "max_cpu_per_manifest": "10",
                "max_memory_per_manifest": "32Gi",
                "max_gpu_per_manifest": 4,
            },
        },
        "api_gateway": {
            "throttle_rate_limit": 1000,
            "throttle_burst_limit": 2000,
            "log_level": "INFO",
            "metrics_enabled": True,
            "tracing_enabled": True,
        },
        "tags": {"Environment": "test", "Project": "gco"},
    }
