"""
Global stack for GCO (Global Capacity Orchestrator on AWS) - AWS Global Accelerator configuration.

This stack creates the global-level resources that span all regions:
- AWS Global Accelerator with TCP listeners on ports 80 and 443
- Endpoint groups for each configured region
- SSM parameters for cross-region endpoint group ARN sharing
- DynamoDB tables for templates and webhooks (global, replicated)

The Global Accelerator provides:
- Single global endpoint for all regions
- Automatic health-based routing to nearest healthy region
- DDoS protection via AWS Shield Standard
- Reduced latency through AWS global network

Architecture:
    Global Accelerator → Listener (80, 443) → Endpoint Groups (per region)
                                                    ↓
                                            Regional ALBs (registered separately)
"""

from typing import Any

from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_backup as backup
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_globalaccelerator as ga
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from gco.config.config_loader import ConfigLoader


class GCOGlobalStack(Stack):
    """
    Global resources stack including AWS Global Accelerator.

    This stack must be deployed before regional stacks. Regional stacks
    will register their ALBs with the endpoint groups created here.

    Attributes:
        accelerator: The Global Accelerator resource
        listener: TCP listener for HTTP/HTTPS traffic
        endpoint_groups: Dict mapping region names to endpoint groups
        templates_table: DynamoDB table for job templates
        webhooks_table: DynamoDB table for webhooks
    """

    def __init__(
        self, scope: Construct, construct_id: str, config: ConfigLoader, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = config
        self.regional_endpoints: dict[str, str] = {}
        self.endpoint_groups: dict[str, ga.EndpointGroup] = {}

        ga_config = self.config.get_global_accelerator_config()

        # Store the accelerator name for reference by other stacks
        self.accelerator_name = ga_config["name"]

        # Create DynamoDB tables for templates and webhooks
        self._create_dynamodb_tables()

        # Create S3 bucket for model weights
        self._create_model_bucket()

        # Create AWS Backup plan for DynamoDB tables
        self._create_backup_plan()

        # Create Global Accelerator with TCP protocol for HTTP/HTTPS traffic
        self.accelerator = ga.Accelerator(
            self, "GCOAccelerator", accelerator_name=self.accelerator_name, enabled=True
        )

        # Store the accelerator ID for CloudWatch metrics
        # CloudWatch uses the accelerator ID (UUID), not the name or ARN
        # ARN format: arn:aws:globalaccelerator::<account>:accelerator/<accelerator-id>
        # Use Fn.select and Fn.split to extract the ID at deploy time
        self.accelerator_id = Fn.select(1, Fn.split("/", self.accelerator.accelerator_arn))

        # Create listener for both HTTP (80) and HTTPS (443) traffic
        self.listener = self.accelerator.add_listener(
            "GCOListener",
            port_ranges=[
                ga.PortRange(from_port=80, to_port=80),
                ga.PortRange(from_port=443, to_port=443),
            ],
            protocol=ga.ConnectionProtocol.TCP,
            client_affinity=ga.ClientAffinity.NONE,
        )

        # Create endpoint groups for each configured region
        for region in self.config.get_regions():
            self._create_endpoint_group(region)

        # Export Global Accelerator outputs for other stacks
        self._create_outputs()

        # Apply cdk-nag suppressions
        self._apply_nag_suppressions()

    def _create_outputs(self) -> None:
        """Create CloudFormation outputs for cross-stack references."""
        project_name = self.config.get_project_name()

        CfnOutput(
            self,
            "GlobalAcceleratorDnsName",
            value=self.accelerator.dns_name,
            description="Global Accelerator DNS name for global endpoint",
            export_name=f"{project_name}-global-accelerator-dns",
        )

        CfnOutput(
            self,
            "GlobalAcceleratorArn",
            value=self.accelerator.accelerator_arn,
            description="Global Accelerator ARN",
            export_name=f"{project_name}-global-accelerator-arn",
        )

        CfnOutput(
            self,
            "GlobalAcceleratorListenerArn",
            value=self.listener.listener_arn,
            description="Global Accelerator Listener ARN",
            export_name=f"{project_name}-global-accelerator-listener-arn",
        )

    def _apply_nag_suppressions(self) -> None:
        """Apply cdk-nag suppressions for this stack."""
        from gco.stacks.nag_suppressions import apply_all_suppressions

        apply_all_suppressions(self, stack_type="global")

    def _create_endpoint_group(self, region: str) -> None:
        """
        Create an endpoint group for a specific region.

        Configures HTTP health checks using the path from cdk.json so
        Global Accelerator can verify the ALB's backend services are
        actually healthy (not just that the port is open).

        Also stores the endpoint group ARN in SSM Parameter Store for
        cross-region access by regional stacks.

        Args:
            region: AWS region name (e.g., 'us-east-1')
        """
        project_name = self.config.get_project_name()
        region_id = region.replace("-", "").title()
        ga_config = self.config.get_global_accelerator_config()

        # Use HTTP health checks so GA validates the backend services are
        # actually responding, not just that the ALB port is open.
        # The health_check_path from cdk.json (default: /api/v1/health)
        # hits the health-monitor service behind the ALB.
        endpoint_group = self.listener.add_endpoint_group(
            f"EndpointGroup{region_id}",
            region=region,
            health_check_port=80,
            health_check_protocol=ga.HealthCheckProtocol.HTTP,
            health_check_path=ga_config.get("health_check_path", "/api/v1/health"),
            health_check_interval=Duration.seconds(ga_config.get("health_check_interval", 30)),
            health_check_threshold=3,
        )

        self.endpoint_groups[region] = endpoint_group

        # Export endpoint group ARN for regional stacks
        CfnOutput(
            self,
            f"EndpointGroup{region_id}Arn",
            value=endpoint_group.endpoint_group_arn,
            description=f"Endpoint group ARN for {region}",
            export_name=f"{project_name}-endpoint-group-{region}-arn",
        )

        # Store endpoint group ARN in SSM Parameter Store for cross-region access
        # Regional stacks read this to register their ALBs with Global Accelerator
        ssm.StringParameter(
            self,
            f"EndpointGroup{region_id}ArnParam",
            parameter_name=f"/{project_name}/endpoint-group-{region}-arn",
            string_value=endpoint_group.endpoint_group_arn,
            description=f"Global Accelerator endpoint group ARN for {region}",
        )

    def add_regional_endpoint(self, region: str, alb_arn: str) -> None:
        """Add a regional ALB endpoint to the Global Accelerator.

        Note: Due to cross-region reference limitations in CDK, the actual endpoint
        registration is handled by a custom resource in the regional stack.
        This method stores the ARN for reference but doesn't directly register it.

        The regional stack should use the endpoint group ARN exported by this stack
        to register its ALB via an AwsCustomResource.
        """
        self.regional_endpoints[region] = alb_arn
        # Actual registration happens in regional stack via custom resource

    def get_accelerator_dns_name(self) -> str:
        """Get the Global Accelerator DNS name"""
        return str(self.accelerator.dns_name)

    def get_accelerator_arn(self) -> str:
        """Get the Global Accelerator ARN"""
        return str(self.accelerator.accelerator_arn)

    def get_listener_arn(self) -> str:
        """Get the Global Accelerator Listener ARN"""
        return str(self.listener.listener_arn)

    def get_endpoint_group_arn(self, region: str) -> str:
        """Get the endpoint group ARN for a specific region"""
        if region in self.endpoint_groups:
            return str(self.endpoint_groups[region].endpoint_group_arn)
        raise ValueError(f"No endpoint group found for region: {region}")

    def _create_dynamodb_tables(self) -> None:
        """Create DynamoDB tables for templates, webhooks, and jobs."""
        project_name = self.config.get_project_name()

        # Job Templates table - stores reusable job templates
        self.templates_table = dynamodb.Table(
            self,
            "JobTemplatesTable",
            table_name=f"{project_name}-job-templates",
            partition_key=dynamodb.Attribute(
                name="template_name",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )

        # Webhooks table - stores webhook registrations
        self.webhooks_table = dynamodb.Table(
            self,
            "WebhooksTable",
            table_name=f"{project_name}-webhooks",
            partition_key=dynamodb.Attribute(
                name="webhook_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )

        # Add GSI for querying webhooks by namespace
        self.webhooks_table.add_global_secondary_index(
            index_name="namespace-index",
            partition_key=dynamodb.Attribute(
                name="namespace",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Jobs table - centralized job tracking and queue
        # This enables global job submission with regional pickup
        self.jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name=f"{project_name}-jobs",
            partition_key=dynamodb.Attribute(
                name="job_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            time_to_live_attribute="ttl",  # Auto-cleanup old completed jobs
        )

        # GSI for querying jobs by region and status (for regional polling)
        self.jobs_table.add_global_secondary_index(
            index_name="region-status-index",
            partition_key=dynamodb.Attribute(
                name="target_region",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI for querying jobs by namespace
        self.jobs_table.add_global_secondary_index(
            index_name="namespace-index",
            partition_key=dynamodb.Attribute(
                name="namespace",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="submitted_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI for querying jobs by status globally
        self.jobs_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="submitted_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Export table names and ARNs for regional stacks
        CfnOutput(
            self,
            "TemplatesTableName",
            value=self.templates_table.table_name,
            description="DynamoDB table name for job templates",
            export_name=f"{project_name}-templates-table-name",
        )

        CfnOutput(
            self,
            "TemplatesTableArn",
            value=self.templates_table.table_arn,
            description="DynamoDB table ARN for job templates",
            export_name=f"{project_name}-templates-table-arn",
        )

        CfnOutput(
            self,
            "WebhooksTableName",
            value=self.webhooks_table.table_name,
            description="DynamoDB table name for webhooks",
            export_name=f"{project_name}-webhooks-table-name",
        )

        CfnOutput(
            self,
            "WebhooksTableArn",
            value=self.webhooks_table.table_arn,
            description="DynamoDB table ARN for webhooks",
            export_name=f"{project_name}-webhooks-table-arn",
        )

        CfnOutput(
            self,
            "JobsTableName",
            value=self.jobs_table.table_name,
            description="DynamoDB table name for centralized job tracking",
            export_name=f"{project_name}-jobs-table-name",
        )

        CfnOutput(
            self,
            "JobsTableArn",
            value=self.jobs_table.table_arn,
            description="DynamoDB table ARN for centralized job tracking",
            export_name=f"{project_name}-jobs-table-arn",
        )

        # Inference Endpoints table - stores desired state for inference deployments
        # The inference_monitor in each regional cluster polls this table
        self.inference_endpoints_table = dynamodb.Table(
            self,
            "InferenceEndpointsTable",
            table_name=f"{project_name}-inference-endpoints",
            partition_key=dynamodb.Attribute(
                name="endpoint_name",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )

        CfnOutput(
            self,
            "InferenceEndpointsTableName",
            value=self.inference_endpoints_table.table_name,
            description="DynamoDB table name for inference endpoint state",
            export_name=f"{project_name}-inference-endpoints-table-name",
        )

        CfnOutput(
            self,
            "InferenceEndpointsTableArn",
            value=self.inference_endpoints_table.table_arn,
            description="DynamoDB table ARN for inference endpoint state",
            export_name=f"{project_name}-inference-endpoints-table-arn",
        )

        # Store table names in SSM for cross-region access
        ssm.StringParameter(
            self,
            "TemplatesTableNameParam",
            parameter_name=f"/{project_name}/templates-table-name",
            string_value=self.templates_table.table_name,
            description="DynamoDB table name for job templates",
        )

        ssm.StringParameter(
            self,
            "WebhooksTableNameParam",
            parameter_name=f"/{project_name}/webhooks-table-name",
            string_value=self.webhooks_table.table_name,
            description="DynamoDB table name for webhooks",
        )

        ssm.StringParameter(
            self,
            "JobsTableNameParam",
            parameter_name=f"/{project_name}/jobs-table-name",
            string_value=self.jobs_table.table_name,
            description="DynamoDB table name for centralized job tracking",
        )

        ssm.StringParameter(
            self,
            "InferenceEndpointsTableNameParam",
            parameter_name=f"/{project_name}/inference-endpoints-table-name",
            string_value=self.inference_endpoints_table.table_name,
            description="DynamoDB table name for inference endpoint state",
        )

    def _create_model_bucket(self) -> None:
        """Create S3 bucket for model weights.

        This bucket serves as the central model registry. Users upload model
        weights here once, and the inference_monitor's init containers sync
        them to each region's local EFS at pod startup.

        The bucket name is auto-generated by CDK to avoid naming collisions.
        It's exported via CfnOutput and SSM for CLI discovery.
        """
        project_name = self.config.get_project_name()
        from aws_cdk import aws_kms as kms

        # KMS key for model bucket encryption
        self.model_bucket_key = kms.Key(
            self,
            "ModelBucketKey",
            description="KMS key for GCO model weights bucket",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Access logs bucket (required for compliance)
        # Retention is configurable via cdk.json context field `s3_access_logs.retention_days`
        # (default: 90 days). Logs older than the configured retention are expired.
        s3_access_logs_ctx = self.node.try_get_context("s3_access_logs") or {}
        access_logs_retention_days = int(s3_access_logs_ctx.get("retention_days", 90))

        self.model_bucket_access_logs = s3.Bucket(
            self,
            "ModelWeightsAccessLogsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireAccessLogs",
                    enabled=True,
                    expiration=Duration.days(access_logs_retention_days),
                )
            ],
        )

        # Model weights bucket
        self.model_bucket = s3.Bucket(
            self,
            "ModelWeightsBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.model_bucket_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.model_bucket_access_logs,
            server_access_logs_prefix="model-bucket-logs/",
        )

        # CDK-nag suppressions — only replication (not needed for model weights)
        from cdk_nag import NagSuppressions

        replication_reason = (
            "Model weights are user-uploaded artifacts that can be re-uploaded. "
            "Cross-region replication is not required; the inference_monitor "
            "syncs models from S3 to each region's EFS at pod startup."
        )

        NagSuppressions.add_resource_suppressions(
            self.model_bucket,
            [
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": replication_reason,
                },
                {
                    "id": "NIST.800.53.R5-S3BucketReplicationEnabled",
                    "reason": replication_reason,
                },
                {
                    "id": "PCI.DSS.321-S3BucketReplicationEnabled",
                    "reason": replication_reason,
                },
            ],
        )

        logs_reason = "This is the server access logs destination bucket."
        NagSuppressions.add_resource_suppressions(
            self.model_bucket_access_logs,
            [
                {"id": "AwsSolutions-S1", "reason": logs_reason},
                {"id": "HIPAA.Security-S3BucketLoggingEnabled", "reason": logs_reason},
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "Access logs do not require replication.",
                },
                {
                    "id": "HIPAA.Security-S3DefaultEncryptionKMS",
                    "reason": "SSE-S3 is sufficient for access logs.",
                },
                {"id": "NIST.800.53.R5-S3BucketLoggingEnabled", "reason": logs_reason},
                {
                    "id": "NIST.800.53.R5-S3BucketReplicationEnabled",
                    "reason": "Access logs do not require replication.",
                },
                {
                    "id": "NIST.800.53.R5-S3DefaultEncryptionKMS",
                    "reason": "SSE-S3 is sufficient for access logs.",
                },
                {"id": "PCI.DSS.321-S3BucketLoggingEnabled", "reason": logs_reason},
                {
                    "id": "PCI.DSS.321-S3BucketReplicationEnabled",
                    "reason": "Access logs do not require replication.",
                },
                {
                    "id": "PCI.DSS.321-S3DefaultEncryptionKMS",
                    "reason": "SSE-S3 is sufficient for access logs.",
                },
            ],
        )

        CfnOutput(
            self,
            "ModelBucketName",
            value=self.model_bucket.bucket_name,
            description="S3 bucket for model weights",
            export_name=f"{project_name}-model-bucket-name",
        )

        CfnOutput(
            self,
            "ModelBucketArn",
            value=self.model_bucket.bucket_arn,
            description="S3 bucket ARN for model weights",
            export_name=f"{project_name}-model-bucket-arn",
        )

        ssm.StringParameter(
            self,
            "ModelBucketNameParam",
            parameter_name=f"/{project_name}/model-bucket-name",
            string_value=self.model_bucket.bucket_name,
            description="S3 bucket name for model weights",
        )

    def _create_backup_plan(self) -> None:
        """Create AWS Backup plan for DynamoDB tables.

        Creates a backup plan with:
        - Daily backups retained for 35 days
        - Weekly backups retained for 90 days
        - All DynamoDB tables added to the backup selection
        """
        # Create backup vault for storing backups
        self.backup_vault = backup.BackupVault(
            self,
            "DynamoDBBackupVault",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Create backup plan with daily and weekly rules
        self.backup_plan = backup.BackupPlan(
            self,
            "DynamoDBBackupPlan",
            backup_plan_rules=[
                # Daily backup - retained for 35 days
                backup.BackupPlanRule(
                    rule_name="DailyBackup",
                    backup_vault=self.backup_vault,
                    schedule_expression=events.Schedule.cron(
                        hour="3",
                        minute="0",
                    ),
                    delete_after=Duration.days(35),
                    enable_continuous_backup=True,  # Enable PITR for DynamoDB
                ),
                # Weekly backup - retained for 90 days
                backup.BackupPlanRule(
                    rule_name="WeeklyBackup",
                    backup_vault=self.backup_vault,
                    schedule_expression=events.Schedule.cron(
                        hour="4",
                        minute="0",
                        week_day="SUN",
                    ),
                    delete_after=Duration.days(90),
                ),
            ],
        )

        # Add all DynamoDB tables to the backup selection
        self.backup_plan.add_selection(
            "DynamoDBTablesSelection",
            resources=[
                backup.BackupResource.from_dynamo_db_table(self.templates_table),
                backup.BackupResource.from_dynamo_db_table(self.webhooks_table),
                backup.BackupResource.from_dynamo_db_table(self.jobs_table),
                backup.BackupResource.from_dynamo_db_table(self.inference_endpoints_table),
            ],
        )

        # Export backup plan ARN
        project_name = self.config.get_project_name()
        CfnOutput(
            self,
            "BackupPlanArn",
            value=self.backup_plan.backup_plan_arn,
            description="AWS Backup plan ARN for DynamoDB tables",
            export_name=f"{project_name}-backup-plan-arn",
        )

        CfnOutput(
            self,
            "BackupVaultArn",
            value=self.backup_vault.backup_vault_arn,
            description="AWS Backup vault ARN for DynamoDB backups",
            export_name=f"{project_name}-backup-vault-arn",
        )
