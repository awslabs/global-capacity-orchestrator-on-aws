"""
Security-focused validation tests for ManifestProcessor.

Pins the rejection rules for dangerous pod-level toggles: hostNetwork,
hostPID, hostIPC, and related incomplete-security-context scenarios.
Also exercises container type validation and resource kind restrictions.
Uses a _make_job_manifest helper to minimize boilerplate when building
variations on the same base manifest, and overlaps some with
test_manifest_validation_preservation.py's baseline tests.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from kubernetes import config as k8s_config

# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_manifest_processor_extended.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_k8s_config():
    """Mock Kubernetes configuration loading."""
    with patch("gco.services.manifest_processor.config") as mock_config:
        mock_config.ConfigException = k8s_config.ConfigException
        mock_config.load_incluster_config.side_effect = k8s_config.ConfigException("Not in cluster")
        mock_config.load_kube_config.return_value = None
        yield mock_config


@pytest.fixture
def manifest_processor(mock_k8s_config):
    """Create ManifestProcessor with mocked Kubernetes clients."""
    from gco.services.manifest_processor import ManifestProcessor

    with patch("gco.services.manifest_processor.client"):
        processor = ManifestProcessor(
            cluster_id="test-cluster",
            region="us-east-1",
            config_dict={
                "max_cpu_per_manifest": "10",
                "max_memory_per_manifest": "32Gi",
                "max_gpu_per_manifest": 4,
                "allowed_namespaces": ["default", "gco-jobs"],
                "validation_enabled": True,
            },
        )
        return processor


def _make_job_manifest(pod_spec_overrides=None, container_overrides=None):
    """Helper to build a Job manifest with optional pod spec and container overrides."""
    container = {
        "name": "worker",
        "image": "python:3.14",
    }
    if container_overrides:
        container.update(container_overrides)

    pod_spec = {
        "containers": [container],
        "restartPolicy": "Never",
    }
    if pod_spec_overrides:
        pod_spec.update(pod_spec_overrides)

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "test-job", "namespace": "default"},
        "spec": {"template": {"spec": pod_spec}},
    }


# ===========================================================================
# Incomplete security context validation
# ===========================================================================


class TestHostNetwork:
    """hostNetwork: true should be rejected."""

    def test_hostnetwork_true_rejected(self, manifest_processor):
        """A Job with hostNetwork: true must be rejected."""
        manifest = _make_job_manifest(pod_spec_overrides={"hostNetwork": True})
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, "hostNetwork: true was accepted but should be rejected"
        assert error is not None
        assert "hostnetwork" in error.lower(), f"Error should mention hostNetwork, got: {error}"


class TestHostPID:
    """hostPID: true should be rejected."""

    def test_hostpid_true_rejected(self, manifest_processor):
        """A Job with hostPID: true must be rejected."""
        manifest = _make_job_manifest(pod_spec_overrides={"hostPID": True})
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, "hostPID: true was accepted but should be rejected"
        assert error is not None
        assert "hostpid" in error.lower(), f"Error should mention hostPID, got: {error}"


class TestHostIPC:
    """hostIPC: true should be rejected."""

    def test_hostipc_true_rejected(self, manifest_processor):
        """A Job with hostIPC: true must be rejected."""
        manifest = _make_job_manifest(pod_spec_overrides={"hostIPC": True})
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, "hostIPC: true was accepted but should be rejected"
        assert error is not None
        assert "hostipc" in error.lower(), f"Error should mention hostIPC, got: {error}"


class TestHostPathVolume:
    """hostPath volumes should be rejected."""

    def test_hostpath_volume_rejected(self, manifest_processor):
        """A Job with a hostPath volume must be rejected."""
        manifest = _make_job_manifest(
            pod_spec_overrides={
                "volumes": [
                    {"name": "host-root", "hostPath": {"path": "/"}},
                ]
            }
        )
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, "hostPath volume was accepted but should be rejected"
        assert error is not None
        assert "hostpath" in error.lower(), f"Error should mention hostPath, got: {error}"


class TestCapabilitiesAdd:
    """capabilities.add should be rejected."""

    def test_capabilities_add_rejected(self, manifest_processor):
        """A Job with capabilities.add: [SYS_ADMIN] must be rejected."""
        manifest = _make_job_manifest(
            container_overrides={
                "securityContext": {
                    "capabilities": {"add": ["SYS_ADMIN"]},
                }
            }
        )
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, "capabilities.add was accepted but should be rejected"
        assert error is not None
        assert (
            "capabilities" in error.lower() or "capabilit" in error.lower()
        ), f"Error should mention capabilities, got: {error}"


class TestRunAsUserZeroConfigurable:
    """runAsUser: 0 is allowed by default but can be blocked via manifest_security_policy.

    The block_run_as_root toggle defaults to False because many GPU/ML containers
    require root. Teams can enable it in cdk.json if their security posture requires it.
    """

    def test_run_as_user_zero_allowed_by_default(self, manifest_processor):
        """A Job with runAsUser: 0 is accepted when block_run_as_root is False (default)."""
        manifest = _make_job_manifest(
            container_overrides={
                "securityContext": {"runAsUser": 0},
            }
        )
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is True, f"runAsUser: 0 should be allowed by default, got: {error}"

    def test_run_as_user_zero_blocked_when_configured(self, mock_k8s_config):
        """A Job with runAsUser: 0 is rejected when block_run_as_root is True."""
        from gco.services.manifest_processor import ManifestProcessor

        with patch("gco.services.manifest_processor.client"):
            processor = ManifestProcessor(
                cluster_id="test-cluster",
                region="us-east-1",
                config_dict={
                    "max_cpu_per_manifest": "10",
                    "max_memory_per_manifest": "32Gi",
                    "max_gpu_per_manifest": 4,
                    "allowed_namespaces": ["default", "gco-jobs"],
                    "validation_enabled": True,
                    "manifest_security_policy": {"block_run_as_root": True},
                },
            )
        manifest = _make_job_manifest(
            container_overrides={
                "securityContext": {"runAsUser": 0},
            }
        )
        is_valid, error = processor.validate_manifest(manifest)
        assert is_valid is False, "runAsUser: 0 should be rejected when block_run_as_root is True"
        assert error is not None
        assert "root" in error.lower() or "runasuser" in error.lower()


# ===========================================================================
# Init/ephemeral container validation
# ===========================================================================


class TestInitContainerPrivileged:
    """Privileged initContainers should be rejected."""

    def test_privileged_init_container_rejected(self, manifest_processor):
        """A Job with a privileged initContainer must be rejected."""
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "test-job", "namespace": "default"},
            "spec": {
                "template": {
                    "spec": {
                        "initContainers": [
                            {
                                "name": "setup",
                                "image": "python:3.14",
                                "securityContext": {"privileged": True},
                            }
                        ],
                        "containers": [
                            {"name": "worker", "image": "python:3.14"},
                        ],
                        "restartPolicy": "Never",
                    }
                }
            },
        }
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, "Privileged initContainer was accepted but should be rejected"
        assert error is not None


class TestInitContainerUntrustedImage:
    """initContainers with untrusted images should be rejected."""

    def test_untrusted_init_container_image_rejected(self, manifest_processor):
        """A Job with an initContainer from an untrusted registry must be rejected."""
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "test-job", "namespace": "default"},
            "spec": {
                "template": {
                    "spec": {
                        "initContainers": [
                            {
                                "name": "setup",
                                "image": "evil-registry.com/miner:latest",
                            }
                        ],
                        "containers": [
                            {"name": "worker", "image": "python:3.14"},
                        ],
                        "restartPolicy": "Never",
                    }
                }
            },
        }
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert (
            is_valid is False
        ), "initContainer with untrusted image was accepted but should be rejected"
        assert error is not None


class TestEphemeralContainerPrivileged:
    """Privileged ephemeralContainers should be rejected."""

    def test_privileged_ephemeral_container_rejected(self, manifest_processor):
        """A Job with a privileged ephemeralContainer must be rejected."""
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "test-job", "namespace": "default"},
            "spec": {
                "template": {
                    "spec": {
                        "ephemeralContainers": [
                            {
                                "name": "debug",
                                "image": "python:3.14",
                                "securityContext": {"privileged": True},
                            }
                        ],
                        "containers": [
                            {"name": "worker", "image": "python:3.14"},
                        ],
                        "restartPolicy": "Never",
                    }
                }
            },
        }
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert (
            is_valid is False
        ), "Privileged ephemeralContainer was accepted but should be rejected"
        assert error is not None


class TestInitContainerResourceLimits:
    """initContainers exceeding resource limits should be rejected."""

    def test_init_container_exceeding_gpu_limit_rejected(self, manifest_processor):
        """A Job with an initContainer exceeding GPU limits must be rejected."""
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "test-job", "namespace": "default"},
            "spec": {
                "template": {
                    "spec": {
                        "initContainers": [
                            {
                                "name": "setup",
                                "image": "python:3.14",
                                "resources": {
                                    "limits": {"nvidia.com/gpu": "8"},  # Exceeds max of 4
                                },
                            }
                        ],
                        "containers": [
                            {"name": "worker", "image": "python:3.14"},
                        ],
                        "restartPolicy": "Never",
                    }
                }
            },
        }
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert (
            is_valid is False
        ), "initContainer exceeding GPU limit was accepted but should be rejected"
        assert error is not None


# ===========================================================================
# Resource kind allowlist
# ===========================================================================


class TestDisallowedKinds:
    """Manifests with disallowed kinds should be rejected."""

    @pytest.mark.parametrize(
        "kind",
        ["NetworkPolicy", "Secret", "PersistentVolume", "ClusterRole"],
        ids=["NetworkPolicy", "Secret", "PersistentVolume", "ClusterRole"],
    )
    def test_disallowed_kind_rejected(self, manifest_processor, kind):
        """Manifests with disallowed resource kinds must be rejected."""
        manifest = {
            "apiVersion": "v1",
            "kind": kind,
            "metadata": {"name": f"test-{kind.lower()}", "namespace": "default"},
            "spec": {},
        }
        is_valid, error = manifest_processor.validate_manifest(manifest)
        assert is_valid is False, f"kind '{kind}' was accepted but should be rejected"
        assert error is not None
        assert (
            "not allowed" in error.lower() or "allowed" in error.lower()
        ), f"Error should mention 'allowed', got: {error}"


# ===========================================================================
# Auth middleware startup logging
# ===========================================================================


class TestStartupLogging:
    """Auth middleware should log warnings at startup when misconfigured."""

    def test_dev_mode_startup_warning_logged(self):
        """When AUTH_SECRET_ARN unset and GCO_DEV_MODE=true, a startup warning must be logged."""
        import gco.services.auth_middleware as auth_module

        # Reset module state
        auth_module._cached_tokens = set()
        auth_module._cache_timestamp = 0
        auth_module._secrets_client = None

        with (
            patch.dict("os.environ", {"GCO_DEV_MODE": "true"}, clear=True),
            patch("gco.services.auth_middleware.logger") as mock_logger,
        ):
            from fastapi import FastAPI

            from gco.services.auth_middleware import AuthenticationMiddleware

            app = FastAPI()
            app.add_middleware(AuthenticationMiddleware)

            @app.get("/healthz")
            async def healthz():
                return {"status": "ok"}

            # Starlette defers middleware init — send a request to trigger it
            client = TestClient(app, raise_server_exceptions=False)
            client.get("/healthz")

            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            all_log_calls = warning_calls + error_calls

            assert len(all_log_calls) > 0, (
                "No startup warning/error was logged when AUTH_SECRET_ARN is unset "
                "and GCO_DEV_MODE=true. The middleware should log at startup."
            )

    def test_no_secret_no_dev_mode_startup_error_logged(self):
        """When AUTH_SECRET_ARN unset and GCO_DEV_MODE not set, a startup error must be logged."""
        import gco.services.auth_middleware as auth_module

        # Reset module state
        auth_module._cached_tokens = set()
        auth_module._cache_timestamp = 0
        auth_module._secrets_client = None

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("gco.services.auth_middleware.logger") as mock_logger,
        ):
            from fastapi import FastAPI

            from gco.services.auth_middleware import AuthenticationMiddleware

            app = FastAPI()
            app.add_middleware(AuthenticationMiddleware)

            @app.get("/healthz")
            async def healthz():
                return {"status": "ok"}

            # Starlette defers middleware init — send a request to trigger it
            client = TestClient(app, raise_server_exceptions=False)
            client.get("/healthz")

            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            all_log_calls = error_calls + warning_calls

            assert len(all_log_calls) > 0, (
                "No startup error was logged when AUTH_SECRET_ARN is unset "
                "and GCO_DEV_MODE is not enabled. The middleware should log at startup."
            )
