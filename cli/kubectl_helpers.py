"""
Shared kubectl helper utilities for GCO CLI.

Provides common kubectl operations used across multiple CLI modules
to reduce code duplication and ensure consistent error handling.
"""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# EKS cluster names: 1-100 chars, alphanumeric and hyphens only.
# AWS region names: e.g. us-east-1, ap-southeast-2, eu-central-1.
_CLUSTER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{0,99}$")
_REGION_RE = re.compile(r"^[a-z]{2,3}-[a-z]+-\d+$")


def _validate_cluster_name(cluster_name: str) -> None:
    """Raise ValueError if cluster_name contains characters outside the EKS naming rules."""
    if not _CLUSTER_NAME_RE.match(cluster_name):
        raise ValueError(
            f"Invalid cluster name {cluster_name!r}: must be 1-100 alphanumeric/hyphen characters"
        )


def _validate_region(region: str) -> None:
    """Raise ValueError if region does not match the standard AWS region pattern."""
    if not _REGION_RE.match(region):
        raise ValueError(f"Invalid AWS region {region!r}: expected format like 'us-east-1'")


def update_kubeconfig(cluster_name: str, region: str) -> None:
    """Update kubeconfig for an EKS cluster.

    Args:
        cluster_name: Name of the EKS cluster
        region: AWS region where the cluster is located

    Raises:
        ValueError: If cluster_name or region contain unexpected characters
        RuntimeError: If the kubeconfig update fails
        FileNotFoundError: If the AWS CLI is not installed
    """
    _validate_cluster_name(cluster_name)
    _validate_region(region)

    cmd = [
        "aws",
        "eks",
        "update-kubeconfig",
        "--name",
        cluster_name,
        "--region",
        region,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True
        )  # nosemgrep: dangerous-subprocess-use-audit - inputs validated above; list form, no shell=True
        if result.returncode != 0:
            raise RuntimeError(f"Failed to update kubeconfig: {result.stderr}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to update kubeconfig: {e.stderr}") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "AWS CLI not found. Please install the AWS CLI and ensure it's in your PATH."
        ) from e
