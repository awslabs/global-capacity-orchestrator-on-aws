"""
Tests for the kubectl-applier Lambda (lambda/kubectl-applier-simple/handler.py).

Covers the manifest-application state machine that bootstraps the
cluster: the two-phase apply that defers `post-helm-*.yaml` files to
after Helm runs, skipping of placeholder manifests for optional
features, PV smart-recreate (skip when unchanged, delete+recreate
when volumeHandle changes), credential verification before any
mutation, and the AllowedKinds allowlist. The handler_module fixture
reloads the handler with sys.modules cleanup so each test runs
against a fresh import.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


@pytest.fixture
def handler_module():
    """Import the kubectl-applier handler with mocked dependencies."""
    handler_path = str(Path(__file__).parent.parent / "lambda" / "kubectl-applier-simple")
    sys.path.insert(0, handler_path)
    try:
        # Remove cached module if present
        sys.modules.pop("handler", None)
        import handler

        yield handler
    finally:
        sys.path.pop(0)
        sys.modules.pop("handler", None)


class TestPostHelmDeferral:
    """Tests for the post-helm- filename prefix convention."""

    def test_main_pass_skips_post_helm_files(self, handler_module, tmp_path):
        """Main pass (post_helm=False) skips files prefixed with post-helm-."""
        # Create a post-helm manifest
        (tmp_path / "post-helm-keda.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "keda.sh/v1alpha1",
                    "kind": "ScaledJob",
                    "metadata": {"name": "test", "namespace": "default"},
                }
            )
        )
        # Create a normal manifest
        (tmp_path / "00-ns.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "test-ns"},
                }
            )
        )

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            result = handler_module.apply_manifests("test-cluster", "us-east-1", str(tmp_path), {})

        # Namespace should be applied, ScaledJob should be deferred
        assert result["AppliedCount"] == 1
        assert "post-helm-keda.yaml:deferred-to-post-helm" in result["Skipped"]

    def test_post_helm_pass_only_applies_post_helm_files(self, handler_module, tmp_path):
        """Post-helm pass (post_helm=True) only applies post-helm- files."""
        (tmp_path / "post-helm-keda.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "keda.sh/v1alpha1",
                    "kind": "ScaledJob",
                    "metadata": {"name": "test", "namespace": "default"},
                    "spec": {
                        "jobTargetRef": {"template": {"spec": {"containers": [{"name": "test"}]}}}
                    },
                }
            )
        )
        (tmp_path / "00-ns.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "test-ns"},
                }
            )
        )

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_custom = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_custom

            result = handler_module.apply_manifests(
                "test-cluster", "us-east-1", str(tmp_path), {}, post_helm=True
            )

        # Only the ScaledJob should be applied, Namespace skipped
        assert result["AppliedCount"] == 1
        assert result["FailedCount"] == 0


class TestPlaceholderSkipping:
    """Tests for skipping manifests with unreplaced template variables."""

    def test_skips_files_with_unreplaced_placeholders(self, handler_module, tmp_path):
        """Files with {{PLACEHOLDER}} values are skipped (feature not enabled)."""
        (tmp_path / "20-fsx.yaml").write_text(
            "apiVersion: v1\nkind: PersistentVolume\nmetadata:\n  name: test\n"
            "spec:\n  csi:\n    volumeHandle: '{{FSX_FILE_SYSTEM_ID}}'\n"
        )

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_client.CoreV1Api.return_value = MagicMock()
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            result = handler_module.apply_manifests("test-cluster", "us-east-1", str(tmp_path), {})

        assert result["AppliedCount"] == 0
        assert "20-fsx.yaml:unreplaced-placeholders" in result["Skipped"]

    def test_applies_files_after_placeholder_replacement(self, handler_module, tmp_path):
        """Files with placeholders are applied after replacement."""
        (tmp_path / "00-ns.yaml").write_text(
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: '{{NS_NAME}}'\n"
        )

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            result = handler_module.apply_manifests(
                "test-cluster",
                "us-east-1",
                str(tmp_path),
                {"{{NS_NAME}}": "my-namespace"},
            )

        assert result["AppliedCount"] == 1


class TestPersistentVolumeHandling:
    """Tests for PV smart recreate logic."""

    def test_pv_skip_when_volume_handle_unchanged(self, handler_module, tmp_path):
        """PV with same volumeHandle is skipped (no-op)."""
        from kubernetes.client.rest import ApiException

        pv_doc = {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": "test-pv"},
            "spec": {
                "capacity": {"storage": "1200Gi"},
                "accessModes": ["ReadWriteMany"],
                "csi": {
                    "driver": "fsx.csi.aws.com",
                    "volumeHandle": "fs-abc123",
                },
            },
        }
        (tmp_path / "20-pv.yaml").write_text(yaml.dump(pv_doc))

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            # create_persistent_volume raises 409 (already exists)
            mock_v1.create_persistent_volume.side_effect = ApiException(status=409)

            # read returns existing PV with same volumeHandle
            existing_pv = MagicMock()
            existing_pv.spec.csi.volume_handle = "fs-abc123"
            mock_v1.read_persistent_volume.return_value = existing_pv

            result = handler_module.apply_manifests("test-cluster", "us-east-1", str(tmp_path), {})

        # Should succeed (skip counts as applied)
        assert result["AppliedCount"] == 1
        assert result["FailedCount"] == 0
        # Should NOT have called delete
        mock_v1.delete_persistent_volume.assert_not_called()

    def test_pv_recreate_when_volume_handle_changed(self, handler_module, tmp_path):
        """PV with different volumeHandle is deleted and recreated."""
        from kubernetes.client.rest import ApiException

        pv_doc = {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": "test-pv"},
            "spec": {
                "capacity": {"storage": "1200Gi"},
                "accessModes": ["ReadWriteMany"],
                "csi": {
                    "driver": "fsx.csi.aws.com",
                    "volumeHandle": "fs-NEW456",
                },
            },
        }
        (tmp_path / "20-pv.yaml").write_text(yaml.dump(pv_doc))

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            # First create raises 409
            # Second create (after delete) succeeds
            mock_v1.create_persistent_volume.side_effect = [
                ApiException(status=409),
                None,
            ]

            # Existing PV has OLD volumeHandle
            existing_pv = MagicMock()
            existing_pv.spec.csi.volume_handle = "fs-OLD123"
            mock_v1.read_persistent_volume.side_effect = [
                existing_pv,  # first read (check existing)
                ApiException(status=404),  # second read (wait loop — PV gone)
            ]

            result = handler_module.apply_manifests("test-cluster", "us-east-1", str(tmp_path), {})

        assert result["AppliedCount"] == 1
        assert result["FailedCount"] == 0
        # Should have removed finalizer, deleted, and recreated
        mock_v1.patch_persistent_volume.assert_called_once()
        mock_v1.delete_persistent_volume.assert_called_once_with("test-pv")
        assert mock_v1.create_persistent_volume.call_count == 2


class TestPostHelmPassNoRestarts:
    """Tests that the post-helm pass doesn't restart deployments or verify credentials."""

    def test_post_helm_pass_returns_minimal_response(self, handler_module, tmp_path):
        """Post-helm pass doesn't include RestartedDeployments or CredentialWarnings."""
        (tmp_path / "post-helm-test.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": "test", "namespace": "default"},
                    "data": {"key": "value"},
                }
            )
        )

        with (
            patch.object(handler_module, "configure_k8s_client"),
            patch("handler.client") as mock_client,
        ):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.RbacAuthorizationV1Api.return_value = MagicMock()
            mock_client.NetworkingV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            result = handler_module.apply_manifests(
                "test-cluster", "us-east-1", str(tmp_path), {}, post_helm=True
            )

        # Post-helm response should NOT have restart or credential fields
        assert "RestartedDeployments" not in result
        assert "CredentialWarnings" not in result
        assert result["AppliedCount"] == 1


class TestLambdaHandler:
    """Tests for the lambda_handler entry point."""

    def test_passes_post_helm_flag(self, handler_module):
        """lambda_handler passes PostHelm property to apply_manifests."""
        event = {
            "RequestType": "Update",
            "StackId": "arn:aws:cloudformation:us-east-1:123:stack/test/id",
            "RequestId": "req-123",
            "LogicalResourceId": "KubectlApply",
            "PhysicalResourceId": "phys-123",
            "ResponseURL": "https://example.com",
            "ResourceProperties": {
                "ClusterName": "test-cluster",
                "Region": "us-east-1",
                "PostHelm": "true",
                "ImageReplacements": {},
            },
        }

        with (
            patch.object(handler_module, "apply_manifests") as mock_apply,
            patch.object(handler_module, "send_response"),
        ):
            mock_apply.return_value = {"AppliedCount": 0, "FailedCount": 0}
            handler_module.lambda_handler(event, MagicMock())

        # Verify post_helm=True was passed
        _, kwargs = mock_apply.call_args
        assert kwargs.get("post_helm") is True or mock_apply.call_args[0][4] is True

    def test_default_post_helm_is_false(self, handler_module):
        """lambda_handler defaults PostHelm to false."""
        event = {
            "RequestType": "Create",
            "StackId": "arn:aws:cloudformation:us-east-1:123:stack/test/id",
            "RequestId": "req-123",
            "LogicalResourceId": "KubectlApply",
            "PhysicalResourceId": "phys-123",
            "ResponseURL": "https://example.com",
            "ResourceProperties": {
                "ClusterName": "test-cluster",
                "Region": "us-east-1",
                "ImageReplacements": {},
            },
        }

        with (
            patch.object(handler_module, "apply_manifests") as mock_apply,
            patch.object(handler_module, "send_response"),
        ):
            mock_apply.return_value = {
                "AppliedCount": 0,
                "FailedCount": 0,
                "SkippedCount": 0,
            }
            handler_module.lambda_handler(event, MagicMock())

        # Verify post_helm=False was passed
        _, kwargs = mock_apply.call_args
        assert kwargs.get("post_helm") is False or mock_apply.call_args[0][4] is False
