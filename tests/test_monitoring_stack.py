"""
Tests for gco/stacks/monitoring_stack.GCOMonitoringStack.

Synthesizes the monitoring stack against MockConfigLoader plus mock
Global, API Gateway, and regional stack objects with the attributes
the monitoring stack reads (accelerator name/id, DynamoDB table
names, Lambda function names, queue names, cluster names). Asserts
dashboard widgets, CloudWatch alarms (metric and composite), and SNS
topic shape on the resulting CloudFormation template — no AWS or
Docker dependency.
"""

from unittest.mock import MagicMock

import aws_cdk as cdk
import pytest
from aws_cdk import assertions


class MockConfigLoader:
    """Mock ConfigLoader for testing monitoring stack."""

    def __init__(self, app=None):
        pass

    def get_project_name(self):
        return "gco-test"

    def get_regions(self):
        return ["us-east-1", "us-west-2"]

    def get_global_region(self):
        return "us-east-2"

    def get_api_gateway_region(self):
        return "us-east-2"

    def get_monitoring_region(self):
        return "us-east-2"


def create_mock_global_stack():
    """Create a mock global stack with accelerator."""
    mock_global_stack = MagicMock()
    mock_global_stack.accelerator_name = "gco-test-accelerator"
    mock_global_stack.accelerator_id = "test-accelerator-id-12345"
    # Add DynamoDB table mocks
    mock_global_stack.templates_table.table_name = "gco-test-templates"
    mock_global_stack.webhooks_table.table_name = "gco-test-webhooks"
    mock_global_stack.jobs_table.table_name = "gco-test-jobs"
    return mock_global_stack


def create_mock_api_gateway_stack():
    """Create a mock API gateway stack with Lambda functions and API."""
    mock_api_gw_stack = MagicMock()
    mock_api_gw_stack.api.rest_api_name = "gco-global-api"
    mock_api_gw_stack.proxy_lambda.function_name = "gco-test-proxy"
    mock_api_gw_stack.rotation_lambda.function_name = "gco-test-rotation"
    mock_api_gw_stack.secret.secret_name = (
        "gco/api-gateway-auth-token"  # nosec B105 - test fixture mock value, not a real secret
    )
    return mock_api_gw_stack


def create_mock_regional_stack(region: str):
    """Create a mock regional stack with cluster, queues, and Lambda functions."""
    mock_regional_stack = MagicMock()
    mock_regional_stack.deployment_region = region
    mock_regional_stack.cluster.cluster_name = f"gco-test-{region}"
    mock_regional_stack.job_queue.queue_name = f"gco-test-jobs-{region}"
    mock_regional_stack.job_dlq.queue_name = f"gco-test-jobs-dlq-{region}"
    mock_regional_stack.kubectl_lambda_function_name = f"gco-test-kubectl-{region}"
    mock_regional_stack.helm_installer_lambda_function_name = f"gco-test-helm-{region}"
    return mock_regional_stack


class TestMonitoringStackImports:
    """Tests for monitoring stack imports."""

    def test_monitoring_stack_can_be_imported(self):
        """Test that GCOMonitoringStack can be imported."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        assert GCOMonitoringStack is not None


class TestMonitoringStackSynthesis:
    """Tests for monitoring stack synthesis."""

    @pytest.fixture
    def monitoring_stack(self):
        """Create a monitoring stack for testing."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader()

        # Create mock stacks
        mock_global_stack = create_mock_global_stack()
        mock_api_gw_stack = create_mock_api_gateway_stack()
        mock_regional_stacks = [
            create_mock_regional_stack("us-east-1"),
            create_mock_regional_stack("us-west-2"),
        ]

        stack = GCOMonitoringStack(
            app,
            "TestMonitoringStack",
            config=config,
            global_stack=mock_global_stack,
            regional_stacks=mock_regional_stacks,
            api_gateway_stack=mock_api_gw_stack,
            env=cdk.Environment(account="123456789012", region="us-east-2"),
        )
        return stack

    def test_monitoring_stack_creates_sns_topic(self, monitoring_stack):
        """Test that monitoring stack creates SNS topic."""
        template = assertions.Template.from_stack(monitoring_stack)
        template.has_resource_properties(
            "AWS::SNS::Topic",
            {
                "DisplayName": "GCO (Global Capacity Orchestrator on AWS) Monitoring Alerts",
            },
        )

    def test_monitoring_stack_creates_dashboard(self, monitoring_stack):
        """Test that monitoring stack creates CloudWatch dashboard."""
        template = assertions.Template.from_stack(monitoring_stack)
        template.resource_count_is("AWS::CloudWatch::Dashboard", 1)

    def test_monitoring_stack_creates_log_groups(self, monitoring_stack):
        """Test that monitoring stack creates log groups for each region."""
        template = assertions.Template.from_stack(monitoring_stack)
        # Should create log groups for health monitor and manifest processor per region
        # 2 regions * 2 services = 4 log groups
        template.resource_count_is("AWS::Logs::LogGroup", 4)

    def test_monitoring_stack_creates_alarms(self, monitoring_stack):
        """Test that monitoring stack creates CloudWatch alarms."""
        template = assertions.Template.from_stack(monitoring_stack)
        # Should have multiple alarms
        alarms = template.find_resources("AWS::CloudWatch::Alarm")
        assert len(alarms) > 0

    def test_monitoring_stack_creates_composite_alarms(self, monitoring_stack):
        """Test that monitoring stack creates composite alarms."""
        template = assertions.Template.from_stack(monitoring_stack)
        composite_alarms = template.find_resources("AWS::CloudWatch::CompositeAlarm")
        assert len(composite_alarms) > 0

    def test_monitoring_stack_exports_dashboard_url(self, monitoring_stack):
        """Test that monitoring stack exports dashboard URL."""
        template = assertions.Template.from_stack(monitoring_stack)
        template.has_output("DashboardUrl", {})

    def test_monitoring_stack_exports_alert_topic_arn(self, monitoring_stack):
        """Test that monitoring stack exports alert topic ARN."""
        template = assertions.Template.from_stack(monitoring_stack)
        template.has_output("AlertTopicArn", {})


class TestMonitoringStackAlarms:
    """Tests for specific alarm configurations."""

    @pytest.fixture
    def monitoring_stack(self):
        """Create a monitoring stack for testing."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader()

        # Create mock stacks
        mock_global_stack = create_mock_global_stack()
        mock_api_gw_stack = create_mock_api_gateway_stack()
        mock_regional_stacks = [
            create_mock_regional_stack("us-east-1"),
            create_mock_regional_stack("us-west-2"),
        ]

        stack = GCOMonitoringStack(
            app,
            "TestMonitoringStack",
            config=config,
            global_stack=mock_global_stack,
            regional_stacks=mock_regional_stacks,
            api_gateway_stack=mock_api_gw_stack,
            env=cdk.Environment(account="123456789012", region="us-east-2"),
        )
        return stack

    def test_api_gateway_5xx_alarm_exists(self, monitoring_stack):
        """Test that API Gateway 5XX alarm is created."""
        template = assertions.Template.from_stack(monitoring_stack)
        # CDK generates alarm names, so we just check for the metric properties
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "MetricName": "5XXError",
                "Namespace": "AWS/ApiGateway",
            },
        )

    def test_lambda_errors_alarm_exists(self, monitoring_stack):
        """Test that Lambda errors alarm is created."""
        template = assertions.Template.from_stack(monitoring_stack)
        # CDK generates alarm names, so we just check for the metric properties
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "MetricName": "Errors",
                "Namespace": "AWS/Lambda",
            },
        )

    def test_sqs_old_message_alarm_exists(self, monitoring_stack):
        """Test that SQS old message alarm is created for each region."""
        template = assertions.Template.from_stack(monitoring_stack)
        # CDK generates alarm names, so we just check for the metric properties
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "MetricName": "ApproximateAgeOfOldestMessage",
                "Namespace": "AWS/SQS",
            },
        )

    def test_sqs_dlq_alarm_exists(self, monitoring_stack):
        """Test that SQS DLQ alarm is created for each region."""
        template = assertions.Template.from_stack(monitoring_stack)
        # CDK generates alarm names, so we just check for the metric properties
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "MetricName": "ApproximateNumberOfMessagesVisible",
                "Namespace": "AWS/SQS",
            },
        )

    def test_eks_high_cpu_alarm_exists(self, monitoring_stack):
        """Test that EKS high CPU alarm is created for each region."""
        template = assertions.Template.from_stack(monitoring_stack)
        # CDK generates alarm names, so we just check for the metric properties
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "MetricName": "node_cpu_utilization",
                "Namespace": "ContainerInsights",
            },
        )

    def test_alb_unhealthy_hosts_alarm_skipped(self, monitoring_stack):
        """Test that ALB unhealthy hosts alarm is NOT created (ALB names unknown at synth time)."""
        template = assertions.Template.from_stack(monitoring_stack)
        # ALB alarms are intentionally skipped because ALB names are dynamically
        # generated by the AWS Load Balancer Controller and not known at CDK synth time.
        # Verify no UnHealthyHostCount alarms exist by checking alarm count doesn't include ALB alarms
        alarms = template.find_resources("AWS::CloudWatch::Alarm")
        alb_alarms = [
            name
            for name, props in alarms.items()
            if props.get("Properties", {}).get("MetricName") == "UnHealthyHostCount"
        ]
        assert len(alb_alarms) == 0, "ALB alarms should not be created"


class TestMonitoringStackDashboardWidgets:
    """Tests for dashboard widget configurations."""

    @pytest.fixture
    def monitoring_stack(self):
        """Create a monitoring stack for testing."""
        from gco.stacks.monitoring_stack import GCOMonitoringStack

        app = cdk.App()
        config = MockConfigLoader()

        # Create mock stacks
        mock_global_stack = create_mock_global_stack()
        mock_api_gw_stack = create_mock_api_gateway_stack()
        mock_regional_stacks = [
            create_mock_regional_stack("us-east-1"),
            create_mock_regional_stack("us-west-2"),
        ]

        stack = GCOMonitoringStack(
            app,
            "TestMonitoringStack",
            config=config,
            global_stack=mock_global_stack,
            regional_stacks=mock_regional_stacks,
            api_gateway_stack=mock_api_gw_stack,
            env=cdk.Environment(account="123456789012", region="us-east-2"),
        )
        return stack

    def test_dashboard_has_body(self, monitoring_stack):
        """Test that dashboard has a body with widgets."""
        template = assertions.Template.from_stack(monitoring_stack)
        dashboards = template.find_resources("AWS::CloudWatch::Dashboard")
        assert len(dashboards) == 1

        # Get the dashboard body
        dashboard_key = list(dashboards.keys())[0]
        dashboard_body = dashboards[dashboard_key]["Properties"]["DashboardBody"]
        assert dashboard_body is not None
