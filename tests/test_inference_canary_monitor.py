"""
Tests for canary deployment reconciliation in gco/services/inference_monitor.py.

Covers _reconcile_canary — creating a "-canary" Deployment + Service
when the canary config appears, updating the canary image when it
changes, scaling canary replicas, and re-weighting the Ingress — plus
_cleanup_canary which tears down the canary resources when the field
is removed. Uses a shared monitor fixture that builds an InferenceMonitor
via __new__ with every Kubernetes API attribute mocked out, so tests
never need a real cluster or K8s config on disk.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException


@pytest.fixture
def monitor():
    """Create an InferenceMonitor with mocked K8s clients."""
    with patch("gco.services.inference_monitor.config"):
        from gco.services.inference_monitor import InferenceMonitor

        m = InferenceMonitor.__new__(InferenceMonitor)
        m.apps_v1 = MagicMock()
        m.core_v1 = MagicMock()
        m.networking_v1 = MagicMock()
        m.store = MagicMock()
        m.region = "us-east-1"
        m.namespace = "gco-inference"
        m.cluster_name = "gco-us-east-1"
        m._k8s_timeout = 30
        return m


class TestReconcileCanary:
    """Tests for _reconcile_canary method."""

    def test_creates_canary_deployment_when_missing(self, monitor):
        monitor.apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404)

        canary = {"image": "new:v2", "weight": 20, "replicas": 1}
        spec = {"image": "old:v1", "port": 8000, "replicas": 2, "gpu_count": 1, "canary": canary}
        endpoint = {"ingress_path": "/inference/ep"}

        # Mock _get_deployment to return None for canary
        with (
            patch.object(monitor, "_get_deployment", return_value=None),
            patch.object(monitor, "_create_deployment") as mock_create,
            patch.object(monitor, "_create_service") as mock_svc,
            patch.object(monitor, "_update_canary_ingress"),
        ):
            monitor._reconcile_canary("ep", "ns", spec, canary, endpoint)

        mock_create.assert_called_once()
        mock_svc.assert_called_once()
        # Verify canary deployment name
        assert mock_create.call_args[0][0] == "ep-canary"

    def test_updates_canary_image_when_changed(self, monitor):
        mock_deployment = MagicMock()
        mock_deployment.spec.replicas = 1

        with (
            patch.object(monitor, "_get_deployment", return_value=mock_deployment),
            patch.object(monitor, "_get_deployment_image", return_value="old:v1"),
            patch.object(monitor, "_update_deployment_image") as mock_update,
            patch.object(monitor, "_scale_deployment"),
            patch.object(monitor, "_update_canary_ingress"),
        ):
            monitor._reconcile_canary(
                "ep",
                "ns",
                {"image": "old:v1", "canary": {"image": "new:v2"}},
                {"image": "new:v2", "weight": 10, "replicas": 1},
                {"ingress_path": "/inference/ep"},
            )

        mock_update.assert_called_once_with("ep-canary", "ns", "new:v2")

    def test_scales_canary_when_replicas_changed(self, monitor):
        mock_deployment = MagicMock()
        mock_deployment.spec.replicas = 1

        with (
            patch.object(monitor, "_get_deployment", return_value=mock_deployment),
            patch.object(monitor, "_get_deployment_image", return_value="new:v2"),
            patch.object(monitor, "_scale_deployment") as mock_scale,
            patch.object(monitor, "_update_canary_ingress"),
        ):
            monitor._reconcile_canary(
                "ep",
                "ns",
                {"image": "old:v1", "canary": {"image": "new:v2"}},
                {"image": "new:v2", "weight": 10, "replicas": 3},
                {"ingress_path": "/inference/ep"},
            )

        mock_scale.assert_called_once_with("ep-canary", "ns", 3)


class TestCleanupCanary:
    """Tests for _cleanup_canary method."""

    def test_deletes_canary_resources(self, monitor):
        monitor._cleanup_canary("ep", "ns")

        monitor.apps_v1.delete_namespaced_deployment.assert_called_once_with(
            "ep-canary", "ns", _request_timeout=30
        )
        monitor.core_v1.delete_namespaced_service.assert_called_once_with(
            "ep-canary", "ns", _request_timeout=30
        )

    def test_handles_404_gracefully(self, monitor):
        monitor.apps_v1.delete_namespaced_deployment.side_effect = ApiException(status=404)
        monitor.core_v1.delete_namespaced_service.side_effect = ApiException(status=404)

        # Should not raise
        monitor._cleanup_canary("ep", "ns")

    def test_logs_non_404_errors(self, monitor):
        monitor.apps_v1.delete_namespaced_deployment.side_effect = ApiException(status=500)

        # Should not raise, just log
        monitor._cleanup_canary("ep", "ns")


class TestUpdateCanaryIngress:
    """Tests for _update_canary_ingress method."""

    def test_patches_existing_ingress(self, monitor):
        spec = {"image": "vllm/vllm-openai:v0.8.0", "health_check_path": "/health"}
        endpoint = {"ingress_path": "/inference/ep"}

        monitor._update_canary_ingress("ep", "ns", spec, endpoint, 80, 20)

        monitor.networking_v1.patch_namespaced_ingress.assert_called_once()
        call_args = monitor.networking_v1.patch_namespaced_ingress.call_args
        assert call_args[0][0] == "inference-ep"

    def test_creates_ingress_on_404(self, monitor):
        monitor.networking_v1.patch_namespaced_ingress.side_effect = ApiException(status=404)

        spec = {"image": "vllm/vllm-openai:v0.8.0", "health_check_path": "/health"}
        endpoint = {"ingress_path": "/inference/ep"}

        monitor._update_canary_ingress("ep", "ns", spec, endpoint, 80, 20)

        monitor.networking_v1.create_namespaced_ingress.assert_called_once()

    def test_raises_on_non_404_error(self, monitor):
        monitor.networking_v1.patch_namespaced_ingress.side_effect = ApiException(status=500)

        spec = {"image": "img:v1", "health_check_path": "/health"}
        endpoint = {"ingress_path": "/inference/ep"}

        with pytest.raises(ApiException):
            monitor._update_canary_ingress("ep", "ns", spec, endpoint, 80, 20)

    def test_canary_label_set(self, monitor):
        spec = {"image": "img:v1", "health_check_path": "/health"}
        endpoint = {"ingress_path": "/inference/ep"}

        monitor._update_canary_ingress("ep", "ns", spec, endpoint, 90, 10)

        call_args = monitor.networking_v1.patch_namespaced_ingress.call_args
        ingress = call_args[0][2]
        assert ingress.metadata.labels["gco.io/canary"] == "true"


class TestCapacityTypeNodeSelector:
    """Tests for capacity_type node selector in _create_deployment."""

    def test_spot_capacity_type_sets_node_selector(self, monitor):
        spec = {
            "image": "img:v1",
            "port": 8000,
            "replicas": 1,
            "gpu_count": 1,
            "health_check_path": "/health",
            "capacity_type": "spot",
        }

        monitor._create_deployment("ep", "ns", spec)

        call_args = monitor.apps_v1.create_namespaced_deployment.call_args
        deployment = call_args[0][1]
        node_selector = deployment.spec.template.spec.node_selector
        assert node_selector["karpenter.sh/capacity-type"] == "spot"

    def test_on_demand_capacity_type_sets_node_selector(self, monitor):
        spec = {
            "image": "img:v1",
            "port": 8000,
            "replicas": 1,
            "gpu_count": 1,
            "health_check_path": "/health",
            "capacity_type": "on-demand",
        }

        monitor._create_deployment("ep", "ns", spec)

        call_args = monitor.apps_v1.create_namespaced_deployment.call_args
        deployment = call_args[0][1]
        node_selector = deployment.spec.template.spec.node_selector
        assert node_selector["karpenter.sh/capacity-type"] == "on-demand"

    def test_no_capacity_type_no_karpenter_selector(self, monitor):
        spec = {
            "image": "img:v1",
            "port": 8000,
            "replicas": 1,
            "gpu_count": 1,
            "health_check_path": "/health",
        }

        monitor._create_deployment("ep", "ns", spec)

        call_args = monitor.apps_v1.create_namespaced_deployment.call_args
        deployment = call_args[0][1]
        node_selector = deployment.spec.template.spec.node_selector
        assert "karpenter.sh/capacity-type" not in (node_selector or {})


class TestInferenceEndpointSpecCapacityType:
    """Tests for capacity_type in InferenceEndpointSpec."""

    def test_spec_with_capacity_type(self):
        from gco.models.inference_models import InferenceEndpointSpec

        spec = InferenceEndpointSpec(image="img:v1", capacity_type="spot")
        d = spec.to_dict()
        assert d["capacity_type"] == "spot"

    def test_spec_without_capacity_type(self):
        from gco.models.inference_models import InferenceEndpointSpec

        spec = InferenceEndpointSpec(image="img:v1")
        d = spec.to_dict()
        assert "capacity_type" not in d

    def test_spec_from_dict_with_capacity_type(self):
        from gco.models.inference_models import InferenceEndpointSpec

        spec = InferenceEndpointSpec.from_dict({"image": "img:v1", "capacity_type": "on-demand"})
        assert spec.capacity_type == "on-demand"

    def test_spec_from_dict_without_capacity_type(self):
        from gco.models.inference_models import InferenceEndpointSpec

        spec = InferenceEndpointSpec.from_dict({"image": "img:v1"})
        assert spec.capacity_type is None


class TestInferenceStoreExtended:
    """Extended tests for InferenceEndpointStore."""

    @patch("gco.services.inference_store.boto3")
    def test_delete_endpoint_success(self, mock_boto):
        from gco.services.inference_store import InferenceEndpointStore

        store = InferenceEndpointStore(table_name="test-table", region="us-east-1")
        result = store.delete_endpoint("ep1")
        assert result is True

    @patch("gco.services.inference_store.boto3")
    def test_delete_endpoint_not_found(self, mock_boto):
        from botocore.exceptions import ClientError

        from gco.services.inference_store import InferenceEndpointStore

        store = InferenceEndpointStore(table_name="test-table", region="us-east-1")
        store._table.delete_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "DeleteItem"
        )
        result = store.delete_endpoint("nonexistent")
        assert result is False

    @patch("gco.services.inference_store.boto3")
    def test_update_region_status(self, mock_boto):
        from gco.services.inference_store import InferenceEndpointStore

        store = InferenceEndpointStore(table_name="test-table", region="us-east-1")
        # Should not raise
        store.update_region_status(
            "ep1", "us-east-1", "running", replicas_ready=2, replicas_desired=2
        )
        store._table.update_item.assert_called_once()

    @patch("gco.services.inference_store.boto3")
    def test_update_region_status_with_error(self, mock_boto):
        from gco.services.inference_store import InferenceEndpointStore

        store = InferenceEndpointStore(table_name="test-table", region="us-east-1")
        store.update_region_status("ep1", "us-east-1", "error", error="OOM killed")
        call_args = store._table.update_item.call_args[1]
        assert "error" in str(call_args)

    @patch("gco.services.inference_store.boto3")
    def test_scale_endpoint_not_found(self, mock_boto):
        from botocore.exceptions import ClientError

        from gco.services.inference_store import InferenceEndpointStore

        store = InferenceEndpointStore(table_name="test-table", region="us-east-1")
        store._table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )
        result = store.scale_endpoint("nonexistent", 3)
        assert result is None
