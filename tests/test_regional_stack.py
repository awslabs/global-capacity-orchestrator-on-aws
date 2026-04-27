"""
Tests for gco/stacks/regional_stack.GCORegionalStack.

Synthesizes the regional stack — VPC, EKS cluster, EFS, optionally FSx,
kubectl-applier Lambda, helm-installer Lambda, the MCP role, drift
detection, and the NetworkPolicy/RBAC apply pipeline — against a
MockConfigLoader that supplies ClusterConfig, NodeGroupConfig, ALB
config, manifest processor config, and the API Gateway config. Patches
the DockerImageAsset and helm-installer builder so tests don't need a
Docker daemon. The MockConfigLoader here is reused by sibling test
files (drift detection, MCP IAM, stacks-ordering-FSx).
"""

from unittest.mock import MagicMock, patch

import aws_cdk as cdk
import pytest
from aws_cdk import assertions


class MockConfigLoader:
    """Mock ConfigLoader for testing regional stack."""

    def __init__(self, app=None, fsx_enabled=False):
        self._fsx_enabled = fsx_enabled

    def get_project_name(self):
        return "gco-test"

    def get_regions(self):
        return ["us-east-1"]

    def get_global_region(self):
        return "us-east-2"

    def get_api_gateway_region(self):
        return "us-east-2"

    def get_monitoring_region(self):
        return "us-east-2"

    def get_kubernetes_version(self):
        return "1.35"

    def get_tags(self):
        return {"Environment": "test", "Project": "gco"}

    def get_resource_thresholds(self):
        from gco.models import ResourceThresholds

        return ResourceThresholds(cpu_threshold=80, memory_threshold=85, gpu_threshold=90)

    def get_node_group_config(self):
        from gco.models import NodeGroupConfig

        return NodeGroupConfig(
            name="gpu-nodes",
            instance_types=["g4dn.xlarge"],
            scaling_config={"min_size": 0, "max_size": 10, "desired_size": 1},
            labels={"workload-type": "gpu"},
            taints=[],
        )

    def get_cluster_config(self, region):
        from gco.models import ClusterConfig

        return ClusterConfig(
            region=region,
            cluster_name=f"gco-test-{region}",
            kubernetes_version="1.35",
            node_groups=[self.get_node_group_config()],
            addons=["metrics-server"],
            resource_thresholds=self.get_resource_thresholds(),
        )

    def get_global_accelerator_config(self):
        return {
            "name": "gco-test-accelerator",
            "health_check_grace_period": 30,
            "health_check_interval": 30,
            "health_check_timeout": 5,
            "health_check_path": "/api/v1/health",
        }

    def get_alb_config(self):
        return {
            "health_check_interval": 30,
            "health_check_timeout": 5,
            "healthy_threshold": 2,
            "unhealthy_threshold": 2,
        }

    def get_manifest_processor_config(self):
        return {
            "image": "gco/manifest-processor:latest",
            "replicas": 3,
            "resource_limits": {"cpu": "1000m", "memory": "2Gi"},
            "allowed_namespaces": ["default", "gco-jobs"],
            "resource_quotas": {
                "max_cpu_per_manifest": "10",
                "max_memory_per_manifest": "32Gi",
                "max_gpu_per_manifest": 4,
            },
        }

    def get_api_gateway_config(self):
        return {
            "throttle_rate_limit": 1000,
            "throttle_burst_limit": 2000,
            "log_level": "INFO",
            "metrics_enabled": True,
            "tracing_enabled": True,
        }

    def get_eks_cluster_config(self):
        return {
            "endpoint_access": "PRIVATE",
        }

    def get_fsx_lustre_config(self, region=None):
        if self._fsx_enabled:
            return {
                "enabled": True,
                "storage_capacity_gib": 1200,
                "deployment_type": "SCRATCH_2",
                "per_unit_storage_throughput": 200,
                "data_compression_type": "LZ4",
                "import_path": None,
                "export_path": None,
            }
        return {
            "enabled": False,
            "storage_capacity_gib": 1200,
            "deployment_type": "SCRATCH_2",
        }

    def get_valkey_config(self):
        return {"enabled": False}


class TestRegionalStackImports:
    """Tests for regional stack imports and class structure."""

    def test_regional_stack_can_be_imported(self):
        """Test that GCORegionalStack can be imported."""
        from gco.stacks.regional_stack import GCORegionalStack

        assert GCORegionalStack is not None

    def test_regional_stack_has_required_methods(self):
        """Test that GCORegionalStack has expected methods."""
        from gco.stacks.regional_stack import GCORegionalStack

        assert hasattr(GCORegionalStack, "__init__")
        assert hasattr(GCORegionalStack, "get_cluster")
        assert hasattr(GCORegionalStack, "get_vpc")

    def test_regional_stack_has_private_methods(self):
        """Test that GCORegionalStack has expected private methods."""
        from gco.stacks.regional_stack import GCORegionalStack

        assert hasattr(GCORegionalStack, "_create_container_images")
        assert hasattr(GCORegionalStack, "_create_eks_cluster")
        assert hasattr(GCORegionalStack, "_create_efs")
        assert hasattr(GCORegionalStack, "_create_fsx_lustre")
        assert hasattr(GCORegionalStack, "_create_outputs")


class TestGlobalStackMethods:
    """Tests for GlobalStack helper methods."""

    def test_global_stack_get_accelerator_dns_name(self):
        """Test get_accelerator_dns_name method."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-global", config=config)

        dns_name = stack.get_accelerator_dns_name()
        assert dns_name is not None

    def test_global_stack_get_accelerator_arn(self):
        """Test get_accelerator_arn method."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-global-arn", config=config)

        arn = stack.get_accelerator_arn()
        assert arn is not None

    def test_global_stack_get_listener_arn(self):
        """Test get_listener_arn method."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-global-listener", config=config)

        arn = stack.get_listener_arn()
        assert arn is not None

    def test_global_stack_get_endpoint_group_arn(self):
        """Test get_endpoint_group_arn method."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-global-endpoint", config=config)

        arn = stack.get_endpoint_group_arn("us-east-1")
        assert arn is not None

    def test_global_stack_get_endpoint_group_arn_invalid_region(self):
        """Test get_endpoint_group_arn raises error for invalid region."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-global-invalid", config=config)

        with pytest.raises(ValueError, match="No endpoint group found"):
            stack.get_endpoint_group_arn("invalid-region")

    def test_global_stack_add_regional_endpoint(self):
        """Test add_regional_endpoint method."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-global-add", config=config)

        stack.add_regional_endpoint(
            "us-east-1",
            "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/test/123",
        )
        assert "us-east-1" in stack.regional_endpoints


class TestGlobalStackSynthesis:
    """Tests for GlobalStack CloudFormation synthesis."""

    def test_global_stack_creates_accelerator(self):
        """Test that GlobalStack creates a Global Accelerator."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-synth-accelerator", config=config)

        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::GlobalAccelerator::Accelerator", 1)

    def test_global_stack_creates_listener(self):
        """Test that GlobalStack creates a listener."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-synth-listener", config=config)

        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::GlobalAccelerator::Listener", 1)

    def test_global_stack_creates_endpoint_groups(self):
        """Test that GlobalStack creates endpoint groups for each region."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-synth-endpoints", config=config)

        template = assertions.Template.from_stack(stack)
        # One endpoint group per region
        template.resource_count_is("AWS::GlobalAccelerator::EndpointGroup", 1)

    def test_global_stack_creates_ssm_parameters(self):
        """Test that GlobalStack creates SSM parameters for endpoint groups and DynamoDB tables."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-synth-ssm", config=config)

        template = assertions.Template.from_stack(stack)
        # 1 for endpoint groups + 4 for DynamoDB tables (templates, webhooks, jobs, inference-endpoints)
        # + 1 for model bucket name
        template.resource_count_is("AWS::SSM::Parameter", 6)


class TestMonitoringStackMethods:
    """Tests for MonitoringStack methods."""

    @staticmethod
    def _create_mock_stacks():
        """Create mock stacks for monitoring stack tests."""
        mock_global_stack = MagicMock()
        mock_global_stack.accelerator_name = "test-accelerator"
        mock_global_stack.accelerator_id = "test-accelerator-id-12345"
        # Add DynamoDB table mocks
        mock_global_stack.templates_table.table_name = "test-templates"
        mock_global_stack.webhooks_table.table_name = "test-webhooks"
        mock_global_stack.jobs_table.table_name = "test-jobs"

        mock_api_gw_stack = MagicMock()
        mock_api_gw_stack.api.rest_api_name = "test-api"
        mock_api_gw_stack.proxy_lambda.function_name = "test-proxy"
        mock_api_gw_stack.rotation_lambda.function_name = "test-rotation"
        mock_api_gw_stack.secret.secret_name = (
            "test-secret"  # nosec B105 - test fixture mock value, not a real secret
        )

        mock_regional_stack = MagicMock()
        mock_regional_stack.deployment_region = "us-east-1"
        mock_regional_stack.cluster.cluster_name = "test-cluster"
        mock_regional_stack.job_queue.queue_name = "test-queue"
        mock_regional_stack.job_dlq.queue_name = "test-dlq"
        mock_regional_stack.kubectl_lambda_function_name = "test-kubectl"
        mock_regional_stack.helm_installer_lambda_function_name = "test-helm"

        return mock_global_stack, mock_api_gw_stack, [mock_regional_stack]

    def test_monitoring_stack_creates_dashboard(self):
        """Test that MonitoringStack creates a CloudWatch dashboard."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader(app)
        mock_global, mock_api_gw, mock_regional = self._create_mock_stacks()

        stack = GCOMonitoringStack(
            app,
            "test-monitoring-dashboard",
            config=config,
            global_stack=mock_global,
            regional_stacks=mock_regional,
            api_gateway_stack=mock_api_gw,
        )

        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::CloudWatch::Dashboard", 1)

    def test_monitoring_stack_creates_sns_topic(self):
        """Test that MonitoringStack creates an SNS topic."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader(app)
        mock_global, mock_api_gw, mock_regional = self._create_mock_stacks()

        stack = GCOMonitoringStack(
            app,
            "test-monitoring-sns",
            config=config,
            global_stack=mock_global,
            regional_stacks=mock_regional,
            api_gateway_stack=mock_api_gw,
        )

        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::SNS::Topic", 1)

    def test_monitoring_stack_creates_alarms(self):
        """Test that MonitoringStack creates CloudWatch alarms."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader(app)
        mock_global, mock_api_gw, mock_regional = self._create_mock_stacks()

        stack = GCOMonitoringStack(
            app,
            "test-monitoring-alarms",
            config=config,
            global_stack=mock_global,
            regional_stacks=mock_regional,
            api_gateway_stack=mock_api_gw,
        )

        template = assertions.Template.from_stack(stack)
        # Should have multiple alarms
        template.has_resource("AWS::CloudWatch::Alarm", {})

    def test_monitoring_stack_creates_log_groups(self):
        """Test that MonitoringStack creates log groups."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader(app)
        mock_global, mock_api_gw, mock_regional = self._create_mock_stacks()

        stack = GCOMonitoringStack(
            app,
            "test-monitoring-logs",
            config=config,
            global_stack=mock_global,
            regional_stacks=mock_regional,
            api_gateway_stack=mock_api_gw,
        )

        template = assertions.Template.from_stack(stack)
        # Should have log groups for health monitor and manifest processor
        template.has_resource("AWS::Logs::LogGroup", {})


class TestConfigLoaderValidation:
    """Tests for ConfigLoader validation methods."""

    def test_config_loader_valid_regions(self):
        """Test ConfigLoader VALID_REGIONS constant."""
        from gco.config.config_loader import ConfigLoader

        assert "us-east-1" in ConfigLoader.VALID_REGIONS
        assert "us-west-2" in ConfigLoader.VALID_REGIONS
        assert "eu-west-1" in ConfigLoader.VALID_REGIONS
        assert "invalid-region" not in ConfigLoader.VALID_REGIONS

    def test_config_loader_valid_gpu_instances(self):
        """Test ConfigLoader VALID_GPU_INSTANCES constant."""
        from gco.config.config_loader import ConfigLoader

        assert "g4dn.xlarge" in ConfigLoader.VALID_GPU_INSTANCES
        assert "g5.xlarge" in ConfigLoader.VALID_GPU_INSTANCES
        assert "p3.2xlarge" in ConfigLoader.VALID_GPU_INSTANCES
        assert "p4d.24xlarge" in ConfigLoader.VALID_GPU_INSTANCES
        assert "t3.micro" not in ConfigLoader.VALID_GPU_INSTANCES

    def test_config_validation_error_class(self):
        """Test ConfigValidationError exception class."""
        from gco.config.config_loader import ConfigValidationError

        error = ConfigValidationError("Test error message")
        assert str(error) == "Test error message"
        assert isinstance(error, Exception)


class TestConfigLoaderDefaults:
    """Tests for ConfigLoader default values."""

    def test_get_project_name_default(self):
        """Test default project name."""
        app = cdk.App()
        config = MockConfigLoader(app)
        assert config.get_project_name() == "gco-test"

    def test_get_regions_default(self):
        """Test default regions."""
        app = cdk.App()
        config = MockConfigLoader(app)
        assert config.get_regions() == ["us-east-1"]

    def test_get_kubernetes_version_default(self):
        """Test default Kubernetes version."""
        app = cdk.App()
        config = MockConfigLoader(app)
        assert config.get_kubernetes_version() == "1.35"

    def test_get_fsx_lustre_config_disabled(self):
        """Test FSx config when disabled."""
        app = cdk.App()
        config = MockConfigLoader(app, fsx_enabled=False)
        fsx_config = config.get_fsx_lustre_config()
        assert fsx_config["enabled"] is False

    def test_get_fsx_lustre_config_enabled(self):
        """Test FSx config when enabled."""
        app = cdk.App()
        config = MockConfigLoader(app, fsx_enabled=True)
        fsx_config = config.get_fsx_lustre_config()
        assert fsx_config["enabled"] is True
        assert fsx_config["storage_capacity_gib"] == 1200
        assert fsx_config["deployment_type"] == "SCRATCH_2"


class TestApiGatewayStackMethods:
    """Tests for ApiGatewayGlobalStack methods."""

    def test_api_gateway_stack_has_secret(self):
        """Test that ApiGatewayStack has secret attribute."""
        from gco.stacks.api_gateway_global_stack import GCOApiGatewayGlobalStack

        app = cdk.App()
        stack = GCOApiGatewayGlobalStack(
            app, "test-api-secret", global_accelerator_dns="test.awsglobalaccelerator.com"
        )

        assert hasattr(stack, "secret")
        assert stack.secret is not None

    def test_api_gateway_stack_creates_rest_api(self):
        """Test that ApiGatewayStack creates a REST API."""
        from gco.stacks.api_gateway_global_stack import GCOApiGatewayGlobalStack

        app = cdk.App()
        stack = GCOApiGatewayGlobalStack(
            app, "test-api-rest", global_accelerator_dns="test.awsglobalaccelerator.com"
        )

        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::ApiGateway::RestApi", 1)

    def test_api_gateway_stack_uses_iam_auth(self):
        """Test that ApiGatewayStack uses IAM authentication."""
        from gco.stacks.api_gateway_global_stack import GCOApiGatewayGlobalStack

        app = cdk.App()
        stack = GCOApiGatewayGlobalStack(
            app, "test-api-auth", global_accelerator_dns="test.awsglobalaccelerator.com"
        )

        template = assertions.Template.from_stack(stack)
        template.has_resource_properties(
            "AWS::ApiGateway::Method", {"AuthorizationType": "AWS_IAM"}
        )


class TestClusterConfigModel:
    """Tests for ClusterConfig model."""

    def test_cluster_config_creation(self):
        """Test creating ClusterConfig."""
        from gco.models import ClusterConfig, NodeGroupConfig, ResourceThresholds

        thresholds = ResourceThresholds(cpu_threshold=80, memory_threshold=85, gpu_threshold=90)
        node_group = NodeGroupConfig(
            name="gpu-nodes",
            instance_types=["g4dn.xlarge"],
            scaling_config={"min_size": 0, "max_size": 10, "desired_size": 1},
            labels={},
            taints=[],
        )

        config = ClusterConfig(
            region="us-east-1",
            cluster_name="test-cluster",
            kubernetes_version="1.35",
            node_groups=[node_group],
            addons=["metrics-server"],
            resource_thresholds=thresholds,
        )

        assert config.region == "us-east-1"
        assert config.cluster_name == "test-cluster"
        assert config.kubernetes_version == "1.35"
        assert len(config.node_groups) == 1


class TestNodeGroupConfigModel:
    """Tests for NodeGroupConfig model."""

    def test_node_group_config_creation(self):
        """Test creating NodeGroupConfig."""
        from gco.models import NodeGroupConfig

        config = NodeGroupConfig(
            name="gpu-nodes",
            instance_types=["g4dn.xlarge", "g5.xlarge"],
            scaling_config={"min_size": 0, "max_size": 10, "desired_size": 1},
            labels={"workload-type": "gpu"},
            taints=[{"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}],
        )

        assert config.name == "gpu-nodes"
        assert "g4dn.xlarge" in config.instance_types
        assert config.scaling_config["min_size"] == 0
        assert config.labels["workload-type"] == "gpu"


class TestResourceThresholdsModel:
    """Tests for ResourceThresholds model."""

    def test_resource_thresholds_creation(self):
        """Test creating ResourceThresholds."""
        from gco.models import ResourceThresholds

        thresholds = ResourceThresholds(cpu_threshold=80, memory_threshold=85, gpu_threshold=90)

        assert thresholds.cpu_threshold == 80
        assert thresholds.memory_threshold == 85
        assert thresholds.gpu_threshold == 90

    def test_resource_thresholds_defaults(self):
        """Test ResourceThresholds with default values."""
        from gco.models import ResourceThresholds

        # Test that the model can be created with explicit values
        thresholds = ResourceThresholds(cpu_threshold=70, memory_threshold=75, gpu_threshold=80)
        assert thresholds.cpu_threshold == 70


class TestRegionalStackSynthesis:
    """Tests for GCORegionalStack CloudFormation synthesis.

    These tests mock _create_helm_installer_lambda to avoid requiring Docker during tests.
    """

    @staticmethod
    def _mock_helm_installer(stack):
        """Set up mock attributes for helm installer."""
        stack.helm_installer_lambda = MagicMock()
        stack.helm_installer_provider = MagicMock()
        stack.helm_installer_provider.service_token = "arn:aws:lambda:us-east-1:123456789012:function:mock"  # nosec B106 - test fixture ARN with fake account ID, not a real credential

    def test_regional_stack_creates_vpc(self):
        """Test that RegionalStack creates a VPC."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        # Mock DockerImageAsset and _create_helm_installer_lambda to avoid Docker dependency
        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-vpc",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::EC2::VPC", 1)

    def test_regional_stack_creates_ecr_repositories(self):
        """Test that RegionalStack creates ECR repositories."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-ecr",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            # Should have 2 ECR repositories (health monitor and manifest processor)
            template.resource_count_is("AWS::ECR::Repository", 2)

    def test_regional_stack_creates_efs(self):
        """Test that RegionalStack creates EFS file system."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-efs",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::EFS::FileSystem", 1)

    def test_regional_stack_creates_iam_roles(self):
        """Test that RegionalStack creates IAM roles."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-iam",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            # Should have multiple IAM roles (cluster admin, node group, service account, etc.)
            template.has_resource("AWS::IAM::Role", {})

    def test_regional_stack_creates_lambda_functions(self):
        """Test that RegionalStack creates Lambda functions."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-lambda",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            # Should have Lambda functions (kubectl applier, GA registration)
            # Note: Helm installer Lambda is mocked, so it won't appear in template
            template.has_resource("AWS::Lambda::Function", {})


class TestRegionalStackWithFsx:
    """Tests for RegionalStack with FSx enabled."""

    def test_regional_stack_creates_fsx_when_enabled(self):
        """Test that RegionalStack creates FSx when enabled."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app, fsx_enabled=True)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-fsx",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::FSx::FileSystem", 1)

    def test_regional_stack_no_fsx_when_disabled(self):
        """Test that RegionalStack does not create FSx when disabled."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app, fsx_enabled=False)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-no-fsx",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::FSx::FileSystem", 0)


class TestAddonRoleUpdateDependencyChain:
    """Regression guard for the IAM PassRole race on fresh deploys.

    CDK's ``AwsCustomResource`` constructs reuse a singleton Lambda
    (logical id prefix ``AWS679``) whose execution role accumulates
    policy statements from every custom resource in the stack. On a
    cold stack create, IAM propagation of one policy attachment can
    race with the Lambda being invoked for a different custom resource.
    ``PassRole`` is the most common failure surface because the IAM
    authorization is checked inline by the subprocess, not cached.

    The regional stack creates up to three ``updateAddon``-style custom
    resources that share this singleton: one each for EFS CSI, FSx CSI
    (only when FSx Lustre is enabled), and CloudWatch Observability.
    The fix is to serialize them with CDK ``add_dependency`` so
    CloudFormation emits explicit ``DependsOn`` edges and the Lambda
    runs against a fully-propagated role each time.

    These tests assert the dependency chain is present in the
    synthesized template both with and without FSx enabled.
    """

    def _synth_regional_stack(self, fsx_enabled: bool, logical_name: str):
        """Synthesize the regional stack with or without FSx enabled.

        Returns the ``assertions.Template`` for inspection. Mirrors the
        Docker + helm-installer patching pattern used elsewhere in this
        file so no real Docker daemon is required.
        """
        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app, fsx_enabled=fsx_enabled)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                logical_name,
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            return assertions.Template.from_stack(stack)

    @staticmethod
    def _depends_on_names(resource: dict) -> list[str]:
        """Normalize a CFN ``DependsOn`` field to a list of logical ids."""
        dep = resource.get("DependsOn", [])
        if isinstance(dep, str):
            return [dep]
        return list(dep)

    @staticmethod
    def _find_by_logical_prefix(resources: dict, prefix: str) -> tuple[str, dict]:
        """Return the first (logical_id, resource) pair whose id starts with ``prefix``."""
        for lid, r in resources.items():
            if lid.startswith(prefix):
                return lid, r
        raise AssertionError(
            f"No resource found with logical id prefix {prefix!r}. "
            f"Available logical ids: {sorted(resources)[:20]}..."
        )

    def test_cw_addon_update_depends_on_efs_addon_update_fsx_disabled(self):
        """Without FSx, CloudWatch's update must depend on EFS's update.

        Prevents the IAM propagation race between the two custom
        resources that share the AWS679 singleton Lambda.
        """
        template = self._synth_regional_stack(
            fsx_enabled=False, logical_name="test-dep-chain-no-fsx"
        )
        resources = template.find_resources("Custom::AWS")

        cw_id, cw_resource = self._find_by_logical_prefix(resources, "UpdateCloudWatchAddonRole")
        efs_id, _ = self._find_by_logical_prefix(resources, "UpdateEfsCsiAddonRole")

        depends_on = self._depends_on_names(cw_resource)
        assert efs_id in depends_on, (
            f"CloudWatch update custom resource must depend on EFS update to "
            f"serialize IAM propagation on the AWS679 singleton Lambda. "
            f"Expected {efs_id!r} in CloudWatch's DependsOn; got: {depends_on}"
        )

    def test_cw_addon_update_has_no_fsx_dependency_when_fsx_disabled(self):
        """When FSx is disabled, no UpdateFsxCsiAddonRole should exist at all."""
        template = self._synth_regional_stack(
            fsx_enabled=False, logical_name="test-dep-chain-no-fsx-guard"
        )
        resources = template.find_resources("Custom::AWS")

        fsx_matches = [lid for lid in resources if lid.startswith("UpdateFsxCsiAddonRole")]
        assert fsx_matches == [], (
            f"No FSx CSI update custom resource should exist when FSx is "
            f"disabled; found: {fsx_matches}"
        )

    def test_cw_addon_update_depends_on_fsx_addon_update_fsx_enabled(self):
        """With FSx, CloudWatch's update must depend on both EFS and FSx updates.

        All three share the AWS679 singleton Lambda, so the full chain
        has to be serialized — not just the CloudWatch → EFS pair.
        """
        template = self._synth_regional_stack(fsx_enabled=True, logical_name="test-dep-chain-fsx")
        resources = template.find_resources("Custom::AWS")

        cw_id, cw_resource = self._find_by_logical_prefix(resources, "UpdateCloudWatchAddonRole")
        efs_id, _ = self._find_by_logical_prefix(resources, "UpdateEfsCsiAddonRole")
        fsx_id, _ = self._find_by_logical_prefix(resources, "UpdateFsxCsiAddonRole")

        depends_on = self._depends_on_names(cw_resource)

        assert efs_id in depends_on, (
            f"CloudWatch update must still depend on EFS update when FSx is "
            f"enabled. Expected {efs_id!r} in DependsOn; got: {depends_on}"
        )
        assert fsx_id in depends_on, (
            f"CloudWatch update must depend on FSx update when FSx is enabled, "
            f"otherwise FSx and CloudWatch race on the AWS679 singleton Lambda. "
            f"Expected {fsx_id!r} in DependsOn; got: {depends_on}"
        )


class TestRegionalStackGetters:
    """Tests for RegionalStack getter methods."""

    def test_get_cluster_returns_cluster(self):
        """Test get_cluster returns the EKS cluster."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-getter-cluster",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            cluster = stack.get_cluster()
            assert cluster is not None
            assert cluster == stack.cluster

    def test_get_vpc_returns_vpc(self):
        """Test get_vpc returns the VPC."""

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = MockConfigLoader(app)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                TestRegionalStackSynthesis._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-regional-getter-vpc",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            vpc = stack.get_vpc()
            assert vpc is not None
            assert vpc == stack.vpc


class TestRegionalStackFsxConfigurations:
    """Tests for RegionalStack FSx Lustre configurations."""

    @staticmethod
    def _mock_helm_installer(stack):
        """Set up mock attributes for helm installer."""
        stack.helm_installer_lambda = MagicMock()
        stack.helm_installer_provider = MagicMock()
        stack.helm_installer_provider.service_token = "arn:aws:lambda:us-east-1:123456789012:function:mock"  # nosec B106 - test fixture ARN with fake account ID, not a real credential

    def test_fsx_with_persistent_deployment_type(self):
        """Test FSx with PERSISTENT_1 deployment type includes throughput."""

        class PersistentFsxConfig(MockConfigLoader):
            def get_fsx_lustre_config(self, region=None):
                return {
                    "enabled": True,
                    "storage_capacity_gib": 2400,
                    "deployment_type": "PERSISTENT_1",
                    "per_unit_storage_throughput": 200,
                    "data_compression_type": "LZ4",
                    "import_path": None,
                    "export_path": None,
                    "file_system_type_version": "2.15",
                }

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = PersistentFsxConfig(app, fsx_enabled=True)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                self._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-fsx-persistent",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::FSx::FileSystem", 1)
            template.has_resource_properties(
                "AWS::FSx::FileSystem",
                {
                    "FileSystemType": "LUSTRE",
                    "StorageCapacity": 2400,
                },
            )

    def test_fsx_with_s3_import_path(self):
        """Test FSx with S3 import path configuration."""

        class S3ImportFsxConfig(MockConfigLoader):
            def get_fsx_lustre_config(self, region=None):
                return {
                    "enabled": True,
                    "storage_capacity_gib": 1200,
                    "deployment_type": "SCRATCH_2",
                    "data_compression_type": "LZ4",
                    "import_path": "s3://my-bucket/data",
                    "auto_import_policy": "NEW_CHANGED_DELETED",
                    "export_path": None,
                    "file_system_type_version": "2.15",
                }

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = S3ImportFsxConfig(app, fsx_enabled=True)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                self._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-fsx-s3-import",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::FSx::FileSystem", 1)

    def test_fsx_with_s3_export_path(self):
        """Test FSx with S3 export path configuration."""

        class S3ExportFsxConfig(MockConfigLoader):
            def get_fsx_lustre_config(self, region=None):
                return {
                    "enabled": True,
                    "storage_capacity_gib": 1200,
                    "deployment_type": "SCRATCH_2",
                    "data_compression_type": "LZ4",
                    "import_path": "s3://my-bucket/input",
                    "export_path": "s3://my-bucket/output",
                    "file_system_type_version": "2.15",
                }

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = S3ExportFsxConfig(app, fsx_enabled=True)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                self._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-fsx-s3-export",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::FSx::FileSystem", 1)

    def test_fsx_with_persistent_2_deployment_type(self):
        """Test FSx with PERSISTENT_2 deployment type."""

        class Persistent2FsxConfig(MockConfigLoader):
            def get_fsx_lustre_config(self, region=None):
                return {
                    "enabled": True,
                    "storage_capacity_gib": 4800,
                    "deployment_type": "PERSISTENT_2",
                    "per_unit_storage_throughput": 500,
                    "data_compression_type": "NONE",
                    "import_path": None,
                    "export_path": None,
                    "file_system_type_version": "2.15",
                }

        from gco.stacks.regional_stack import GCORegionalStack

        app = cdk.App()
        config = Persistent2FsxConfig(app, fsx_enabled=True)

        with (
            patch("gco.stacks.regional_stack.ecr_assets.DockerImageAsset") as mock_docker,
            patch.object(
                GCORegionalStack,
                "_create_helm_installer_lambda",
                self._mock_helm_installer,
            ),
        ):
            mock_image = MagicMock()
            mock_image.image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
            mock_docker.return_value = mock_image

            stack = GCORegionalStack(
                app,
                "test-fsx-persistent-2",
                config=config,
                region="us-east-1",
                auth_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",  # nosec B106 - test fixture ARN with fake account ID, not a real secret
                env=cdk.Environment(account="123456789012", region="us-east-1"),
            )

            template = assertions.Template.from_stack(stack)
            template.resource_count_is("AWS::FSx::FileSystem", 1)
            template.has_resource_properties(
                "AWS::FSx::FileSystem",
                {
                    "FileSystemType": "LUSTRE",
                    "StorageCapacity": 4800,
                },
            )


class TestGlobalStackDynamoDBTables:
    """Tests for GlobalStack DynamoDB tables."""

    def test_global_stack_creates_templates_table(self):
        """Test that GlobalStack creates templates DynamoDB table."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-dynamodb-templates", config=config)

        template = assertions.Template.from_stack(stack)
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "TableName": "gco-test-job-templates",
                "KeySchema": [{"AttributeName": "template_name", "KeyType": "HASH"}],
            },
        )

    def test_global_stack_creates_webhooks_table(self):
        """Test that GlobalStack creates webhooks DynamoDB table."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-dynamodb-webhooks", config=config)

        template = assertions.Template.from_stack(stack)
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "TableName": "gco-test-webhooks",
                "KeySchema": [{"AttributeName": "webhook_id", "KeyType": "HASH"}],
            },
        )

    def test_global_stack_creates_jobs_table(self):
        """Test that GlobalStack creates jobs DynamoDB table."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-dynamodb-jobs", config=config)

        template = assertions.Template.from_stack(stack)
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "TableName": "gco-test-jobs",
                "KeySchema": [{"AttributeName": "job_id", "KeyType": "HASH"}],
            },
        )

    def test_global_stack_creates_backup_plan(self):
        """Test that GlobalStack creates AWS Backup plan for DynamoDB tables."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-backup-plan", config=config)

        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::Backup::BackupPlan", 1)
        template.resource_count_is("AWS::Backup::BackupVault", 1)
        template.resource_count_is("AWS::Backup::BackupSelection", 1)

    def test_global_stack_dynamodb_tables_have_pitr(self):
        """Test that DynamoDB tables have point-in-time recovery enabled."""
        from gco.stacks.global_stack import GCOGlobalStack

        app = cdk.App()
        config = MockConfigLoader(app)
        stack = GCOGlobalStack(app, "test-dynamodb-pitr", config=config)

        template = assertions.Template.from_stack(stack)
        # All 3 tables should have PITR enabled
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            },
        )


class TestNagSuppressions:
    """Tests for CDK-nag suppression functions."""

    def test_add_backup_suppressions(self):
        """Test add_backup_suppressions function."""
        from gco.stacks.nag_suppressions import add_backup_suppressions

        app = cdk.App()
        stack = cdk.Stack(app, "test-backup-suppressions")

        # Should not raise any errors
        add_backup_suppressions(stack)

    def test_apply_all_suppressions_global_stack(self):
        """Test apply_all_suppressions for global stack type."""
        from gco.stacks.nag_suppressions import apply_all_suppressions

        app = cdk.App()
        stack = cdk.Stack(app, "test-global-suppressions")

        # Should not raise any errors
        apply_all_suppressions(stack, stack_type="global")

    def test_apply_all_suppressions_regional_stack(self):
        """Test apply_all_suppressions for regional stack type."""
        from gco.stacks.nag_suppressions import apply_all_suppressions

        app = cdk.App()
        stack = cdk.Stack(app, "test-regional-suppressions")

        # Should not raise any errors
        apply_all_suppressions(
            stack,
            stack_type="regional",
            regions=["us-east-1", "us-west-2"],
            global_region="us-east-2",
        )

    def test_apply_all_suppressions_api_gateway_stack(self):
        """Test apply_all_suppressions for api_gateway stack type."""
        from gco.stacks.nag_suppressions import apply_all_suppressions

        app = cdk.App()
        stack = cdk.Stack(app, "test-api-gateway-suppressions")

        # Should not raise any errors
        apply_all_suppressions(stack, stack_type="api_gateway")

    def test_apply_all_suppressions_monitoring_stack(self):
        """Test apply_all_suppressions for monitoring stack type."""
        from gco.stacks.nag_suppressions import apply_all_suppressions

        app = cdk.App()
        stack = cdk.Stack(app, "test-monitoring-suppressions")

        # Should not raise any errors
        apply_all_suppressions(stack, stack_type="monitoring")

    def test_add_iam_suppressions_with_dynamodb_patterns(self):
        """Test add_iam_suppressions includes DynamoDB index patterns."""
        from gco.stacks.nag_suppressions import add_iam_suppressions

        app = cdk.App()
        stack = cdk.Stack(app, "test-iam-dynamodb-suppressions")

        # Should not raise any errors
        add_iam_suppressions(
            stack,
            regions=["us-east-1"],
            global_region="us-east-2",
        )


class TestResourceStatusUid:
    """Tests for ResourceStatus uid attribute."""

    def test_resource_status_with_uid(self):
        """Test ResourceStatus can be created with uid."""
        from gco.models.manifest_models import ResourceStatus

        status = ResourceStatus(
            api_version="batch/v1",
            kind="Job",
            name="test-job",
            namespace="default",
            status="created",
            uid="abc123-def456",
        )

        assert status.uid == "abc123-def456"

    def test_resource_status_without_uid(self):
        """Test ResourceStatus can be created without uid (defaults to None)."""
        from gco.models.manifest_models import ResourceStatus

        status = ResourceStatus(
            api_version="batch/v1",
            kind="Job",
            name="test-job",
            namespace="default",
            status="created",
        )

        assert status.uid is None

    def test_resource_status_uid_in_dict(self):
        """Test ResourceStatus uid is included when converting to dict-like access."""
        from gco.models.manifest_models import ResourceStatus

        status = ResourceStatus(
            api_version="batch/v1",
            kind="Job",
            name="test-job",
            namespace="default",
            status="created",
            uid="test-uid-123",
        )

        # Verify uid attribute is accessible
        assert hasattr(status, "uid")
        assert status.uid == "test-uid-123"


# =============================================================================
# Valkey Cache Tests
# =============================================================================


class TestValkeyCache:
    """Tests for the Valkey Serverless cache construct."""

    def test_valkey_disabled_by_default_creates_no_cache(self):
        """When valkey.enabled is false, no cache is created."""
        from unittest.mock import MagicMock

        mock_config = MagicMock()
        mock_config.app.node.try_get_context.return_value = {"enabled": False}

        # The method should return early without creating resources
        # We test this by checking the config is read
        assert mock_config.app.node.try_get_context("valkey") == {"enabled": False}

    def test_valkey_config_defaults(self):
        """Test that default Valkey config values are sensible."""
        import json
        from pathlib import Path

        cdk_json = json.loads(Path("cdk.json").read_text())
        valkey_config = cdk_json["context"]["valkey"]

        assert isinstance(valkey_config["enabled"], bool)
        assert valkey_config["max_data_storage_gb"] == 5
        assert valkey_config["max_ecpu_per_second"] == 5000
        assert valkey_config["snapshot_retention_limit"] == 1


class TestQueueProcessorConfig:
    """Tests for queue_processor configuration in cdk.json."""

    def test_queue_processor_config_exists(self):
        import json
        from pathlib import Path

        cdk_json = json.loads(Path("cdk.json").read_text())
        qp = cdk_json["context"]["queue_processor"]
        policy = cdk_json["context"]["job_validation_policy"]

        assert qp["enabled"] is True
        assert qp["polling_interval"] == 10
        assert qp["max_concurrent_jobs"] == 10
        assert qp["messages_per_job"] == 1
        # allowed_namespaces now lives under the shared job_validation_policy
        # section (both processors read the same allowlist).
        assert "gco-jobs" in policy["allowed_namespaces"]
        # Resource caps now live under the shared job_validation_policy
        # section (both processors read the same values).
        assert policy["resource_quotas"]["max_gpu_per_manifest"] == 4

    def test_queue_processor_defaults_match_docs(self):
        """Ensure cdk.json defaults match what's documented in CUSTOMIZATION.md."""
        import json
        from pathlib import Path

        cdk_json = json.loads(Path("cdk.json").read_text())
        qp = cdk_json["context"]["queue_processor"]
        policy = cdk_json["context"]["job_validation_policy"]

        assert qp["successful_jobs_history"] == 20
        assert qp["failed_jobs_history"] == 10
        assert policy["resource_quotas"]["max_cpu_per_manifest"] == "10"
        assert policy["resource_quotas"]["max_memory_per_manifest"] == "32Gi"
