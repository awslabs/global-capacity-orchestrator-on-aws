"""Shared ``cdk.json`` configuration matrix.

Single source of truth for ``tests/test_nag_compliance.py`` and
``scripts/test_cdk_synthesis.py``. Both need to iterate over the
same set of cdk.json overlays to catch the same configuration-specific
regressions — divergence between the two lists is how we ended up
with an ``AwsSolutions-IAM5`` error on a ``gco-us-east-1`` deploy
that neither tool had ever exercised. Keep this list as the canonical
definition; both sides just import ``CONFIGS`` from here.

Each entry is a ``(name, overrides)`` tuple where ``overrides`` is a
shallow dict merged into the baseline ``cdk.json`` context before the
CDK app is constructed. Dict values are merged per-key (not
replaced), so a partial override like
``{"eks_cluster": {"endpoint_access": "PUBLIC_AND_PRIVATE"}}`` leaves
other keys in the ``eks_cluster`` block alone.

Notes on the list
-----------------

* ``default-regions`` is always first; it mirrors whatever cdk.json
  ships with the repo. The synthesis matrix script used to rely on
  that ordering to establish a baseline before applying overrides.

* The ``helm-*`` entries exercise the helm chart enable/disable
  matrix. These matter for the compliance test because the Helm
  installer Lambda's IAM role changes shape based on which charts
  it has to install.

* ``large-node-groups`` writes into the legacy ``node_groups`` context
  key. That key is no longer consumed by the stacks (EKS Auto Mode
  handles node groups dynamically) but the config loader still
  validates it, so the override still has to parse. See also the
  pending cleanup for removing the dead config surface.
"""

from __future__ import annotations

from typing import Any

CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("default-regions", {}),
    (
        "us-west-regions",
        {
            "deployment_regions": {
                "global": "us-west-2",
                "api_gateway": "us-west-2",
                "monitoring": "us-west-2",
                "regional": ["us-west-1"],
            }
        },
    ),
    (
        "eu-regions",
        {
            "deployment_regions": {
                "global": "eu-west-1",
                "api_gateway": "eu-west-1",
                "monitoring": "eu-west-1",
                "regional": ["eu-central-1"],
            }
        },
    ),
    (
        "multi-region",
        {
            "deployment_regions": {
                "global": "us-east-2",
                "api_gateway": "us-east-2",
                "monitoring": "us-east-2",
                "regional": ["us-east-1", "us-west-2"],
            }
        },
    ),
    (
        "valkey-enabled",
        {
            "valkey": {
                "enabled": True,
                "max_data_storage_gb": 5,
                "max_ecpu_per_second": 5000,
                "snapshot_retention_limit": 1,
            }
        },
    ),
    (
        "valkey-disabled",
        {
            "valkey": {
                "enabled": False,
                "max_data_storage_gb": 5,
                "max_ecpu_per_second": 5000,
                "snapshot_retention_limit": 1,
            }
        },
    ),
    (
        "fsx-enabled",
        {
            "fsx_lustre": {
                "enabled": True,
                "storage_capacity_gib": 1200,
                "deployment_type": "SCRATCH_2",
                "file_system_type_version": "2.15",
                "per_unit_storage_throughput": 200,
                "data_compression_type": "LZ4",
                "import_path": None,
                "export_path": None,
                "auto_import_policy": "NEW_CHANGED_DELETED",
            }
        },
    ),
    ("fsx-disabled", {"fsx_lustre": {"enabled": False}}),
    ("endpoint-private", {"eks_cluster": {"endpoint_access": "PRIVATE"}}),
    ("endpoint-public-private", {"eks_cluster": {"endpoint_access": "PUBLIC_AND_PRIVATE"}}),
]

CONFIGS.extend(
    [
        (
            "thresholds-all-disabled",
            {
                "resource_thresholds": {
                    "cpu_threshold": -1,
                    "memory_threshold": -1,
                    "gpu_threshold": -1,
                    "pending_pods_threshold": -1,
                    "pending_requested_cpu_vcpus": -1,
                    "pending_requested_memory_gb": -1,
                    "pending_requested_gpus": -1,
                }
            },
        ),
        (
            "thresholds-aggressive",
            {
                "resource_thresholds": {
                    "cpu_threshold": 90,
                    "memory_threshold": 90,
                    "gpu_threshold": 95,
                    "pending_pods_threshold": 50,
                    "pending_requested_cpu_vcpus": 500,
                    "pending_requested_memory_gb": 1000,
                    "pending_requested_gpus": 100,
                }
            },
        ),
        (
            "all-features-enabled",
            {
                "valkey": {
                    "enabled": True,
                    "max_data_storage_gb": 10,
                    "max_ecpu_per_second": 10000,
                    "snapshot_retention_limit": 3,
                },
                "fsx_lustre": {
                    "enabled": True,
                    "storage_capacity_gib": 2400,
                    "deployment_type": "SCRATCH_2",
                    "file_system_type_version": "2.15",
                    "per_unit_storage_throughput": 200,
                    "data_compression_type": "LZ4",
                    "import_path": None,
                    "export_path": None,
                    "auto_import_policy": "NEW_CHANGED_DELETED",
                },
                "eks_cluster": {"endpoint_access": "PUBLIC_AND_PRIVATE"},
            },
        ),
        (
            "minimal-config",
            {
                "valkey": {
                    "enabled": False,
                    "max_data_storage_gb": 5,
                    "max_ecpu_per_second": 5000,
                    "snapshot_retention_limit": 1,
                },
                "fsx_lustre": {"enabled": False},
                "eks_cluster": {"endpoint_access": "PRIVATE"},
            },
        ),
        (
            "ap-regions",
            {
                "deployment_regions": {
                    "global": "ap-southeast-1",
                    "api_gateway": "ap-southeast-1",
                    "monitoring": "ap-southeast-1",
                    "regional": ["ap-northeast-1"],
                }
            },
        ),
        (
            "three-regions",
            {
                "deployment_regions": {
                    "global": "us-east-2",
                    "api_gateway": "us-east-2",
                    "monitoring": "us-east-2",
                    "regional": ["us-east-1", "eu-west-1", "ap-northeast-1"],
                }
            },
        ),
        (
            "valkey-large",
            {
                "valkey": {
                    "enabled": True,
                    "max_data_storage_gb": 100,
                    "max_ecpu_per_second": 50000,
                    "snapshot_retention_limit": 7,
                }
            },
        ),
        (
            "fsx-with-s3-import",
            {
                "fsx_lustre": {
                    "enabled": True,
                    "storage_capacity_gib": 1200,
                    "deployment_type": "PERSISTENT_2",
                    "file_system_type_version": "2.15",
                    "per_unit_storage_throughput": 500,
                    "data_compression_type": "LZ4",
                    "import_path": "s3://my-bucket/data",
                    "export_path": "s3://my-bucket/output",
                    "auto_import_policy": "NEW_CHANGED_DELETED",
                }
            },
        ),
        (
            "high-api-limits",
            {
                "api_gateway": {
                    "throttle_rate_limit": 10000,
                    "throttle_burst_limit": 20000,
                    "log_level": "ERROR",
                    "metrics_enabled": True,
                    "tracing_enabled": False,
                }
            },
        ),
        (
            "large-node-groups",
            {
                "node_groups": {
                    "gpu_instances": ["p4d.24xlarge", "g5.48xlarge"],
                    "min_size": 0,
                    "max_size": 500,
                    "desired_size": 10,
                }
            },
        ),
        (
            "helm-slurm-yunikorn-enabled",
            {
                "helm": {
                    "slurm": {"enabled": True},
                    "yunikorn": {"enabled": True},
                }
            },
        ),
        (
            "helm-minimal",
            {
                "helm": {
                    "keda": {"enabled": False},
                    "volcano": {"enabled": False},
                    "kuberay": {"enabled": False},
                    "nvidia_network_operator": {"enabled": False},
                    "aws_efa_device_plugin": {"enabled": False},
                }
            },
        ),
        (
            "helm-gpu-only",
            {
                "helm": {
                    "keda": {"enabled": False},
                    "volcano": {"enabled": False},
                    "kuberay": {"enabled": False},
                    "kueue": {"enabled": False},
                    "cert_manager": {"enabled": False},
                    "nvidia_network_operator": {"enabled": False},
                    "aws_efa_device_plugin": {"enabled": False},
                    "aws_neuron_device_plugin": {"enabled": False},
                }
            },
        ),
        (
            "helm-all-schedulers",
            {
                "helm": {
                    "slurm": {"enabled": True},
                    "yunikorn": {"enabled": True},
                    "volcano": {"enabled": True},
                    "kueue": {"enabled": True},
                    "kuberay": {"enabled": True},
                }
            },
        ),
    ]
)


# ---------------------------------------------------------------------------
# NAG_CONFIGS — subset of CONFIGS used by tests/test_nag_compliance.py
# ---------------------------------------------------------------------------
# The full 24-entry CONFIGS list is used by scripts/test_cdk_synthesis.py
# (subprocess-based synth validation). For the in-process cdk-nag compliance
# test, we only need the configs that produce *distinct IAM policy surfaces*.
# Most configs (valkey-disabled, thresholds-aggressive, helm-minimal, etc.)
# change Helm charts, resource quotas, or threshold values that don't touch
# IAM at all — running cdk-nag on them is pure overhead.
#
# The 5 configs below cover every IAM code path:
#   1. default-regions     — baseline single-region, all standard roles
#   2. multi-region        — cross-region SSM/DynamoDB roles, 2 regional stacks
#   3. fsx-enabled         — FSx CSI IRSA role + PassRole on shared CR role
#   4. all-features-enabled — FSx + Valkey + public endpoint combined
#   5. three-regions       — 3 regional stacks, max cross-region surface
#
# On a 2-vCPU CI runner with 2 xdist workers this runs in ~5 minutes
# instead of ~30 minutes for the full matrix.

_NAG_CONFIG_NAMES = {
    "default-regions",
    "multi-region",
    "fsx-enabled",
    "all-features-enabled",
    "three-regions",
}

NAG_CONFIGS: list[tuple[str, dict[str, Any]]] = [
    (name, overrides) for name, overrides in CONFIGS if name in _NAG_CONFIG_NAMES
]

# Sanity check — if someone renames a config in CONFIGS but forgets to
# update _NAG_CONFIG_NAMES, this catches it at import time rather than
# silently running fewer configs.
assert len(NAG_CONFIGS) == len(_NAG_CONFIG_NAMES), (
    f"NAG_CONFIGS has {len(NAG_CONFIGS)} entries but expected "
    f"{len(_NAG_CONFIG_NAMES)}. Check that _NAG_CONFIG_NAMES matches "
    f"the names in CONFIGS: missing = "
    f"{_NAG_CONFIG_NAMES - {n for n, _ in NAG_CONFIGS}}"
)
