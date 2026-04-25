#!/usr/bin/env python3
"""CDK Configuration Matrix Synthesis Tests.

Tests that `cdk synth` succeeds across a matrix of configuration
combinations. This catches issues like hardcoded regions, missing
conditional guards, and broken feature flag interactions — without
deploying anything.

Usage:
    python3 scripts/test_cdk_synthesis.py [--verbose]
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

VERBOSE = "--verbose" in sys.argv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CDK_JSON = PROJECT_ROOT / "cdk.json"

# Save original config to restore after each test
ORIGINAL_CONFIG = CDK_JSON.read_text()
BASE_CONFIG = json.loads(ORIGINAL_CONFIG)


def log(msg: str) -> None:
    print(msg, flush=True)


def synth_with_config(name: str, overrides: dict[str, Any]) -> bool:
    """Run cdk synth with a modified cdk.json config."""
    config = json.loads(json.dumps(BASE_CONFIG))
    ctx = config["context"]

    for key, value in overrides.items():
        if isinstance(value, dict) and key in ctx and isinstance(ctx[key], dict):
            ctx[key].update(value)
        else:
            ctx[key] = value

    try:
        # Write modified config
        CDK_JSON.write_text(json.dumps(config, indent=2))

        result = subprocess.run(
            ["cdk", "synth", "--quiet", "--no-staging", "--app", f"{sys.executable} app.py"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

        # CDK returns 0 on success even with notices
        if result.returncode == 0:
            log(f"  PASS: {name}")
            return True

        # Check if it's a real error or just notices
        stderr = result.stderr
        if "Error" in stderr or "error" in stderr.split("NOTICES")[0]:
            log(f"  FAIL: {name}")
            # Show the error before NOTICES
            error_part = stderr.split("NOTICES")[0].strip()
            if error_part:
                log(f"  {error_part[-300:]}")
            return False

        log(f"  PASS: {name} (with notices)")
        return True

    except subprocess.TimeoutExpired:
        log(f"  FAIL: {name} (timeout)")
        return False
    except Exception as e:
        log(f"  FAIL: {name} ({e})")
        return False
    finally:
        # Always restore original config
        CDK_JSON.write_text(ORIGINAL_CONFIG)


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
    # Asia-Pacific region
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
    # Three-region deployment
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
    # Valkey with large capacity
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
    # FSx with S3 import
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
    # High API throttle limits
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
    # Large node group config
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
    # Helm: Slurm and YuniKorn enabled
    (
        "helm-slurm-yunikorn-enabled",
        {
            "helm": {
                "slurm": {"enabled": True},
                "yunikorn": {"enabled": True},
            }
        },
    ),
    # Helm: minimal — only GPU operator and Kueue
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
    # Helm: everything disabled except GPU basics
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
    # Helm: all schedulers enabled
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


def main() -> int:
    log(f"Running CDK synthesis matrix: {len(CONFIGS)} configurations")
    log("=" * 60)

    passed = 0
    failed = 0
    failures = []

    for name, overrides in CONFIGS:
        success = synth_with_config(name, overrides)
        if success:
            passed += 1
        else:
            failed += 1
            failures.append(name)

    log("")
    log("=" * 60)
    log(f"Results: {passed} passed, {failed} failed out of {len(CONFIGS)}")

    if failures:
        log(f"Failed configs: {', '.join(failures)}")
        return 1

    log("All configurations synthesized successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
