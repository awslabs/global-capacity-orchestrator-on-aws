"""CDK-nag suppression utilities for GCO stacks.

This module provides centralized suppression management for cdk-nag rules
that are intentionally not applicable or have documented justifications.

Supported Compliance Frameworks:
- AWS Solutions: Best practices for AWS architectures
- HIPAA Security: Healthcare compliance requirements
- NIST 800-53 Rev 5: Federal security controls
- PCI DSS 3.2.1: Payment card industry standards
- Serverless: Best practices for serverless architectures

Suppression Categories:
1. AWS Managed Policies - Required for EKS/Lambda integrations
2. Inline Policies - CDK-generated for custom resources
3. Wildcard Permissions - Required for dynamic resource access
4. Infrastructure Patterns - Intentional architectural decisions
"""

from aws_cdk import Stack
from cdk_nag import NagPackSuppression, NagSuppressions


def add_eks_suppressions(stack: Stack) -> None:
    """Add suppressions for EKS-related cdk-nag findings.

    EKS requires specific AWS managed policies that cannot be replaced
    with customer-managed policies without breaking functionality.
    """
    # EKS requires these AWS managed policies - they are AWS-recommended
    eks_managed_policies = [
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSClusterPolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSComputePolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSBlockStoragePolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSLoadBalancingPolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSNetworkingPolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKS_CNI_Policy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy",
        # CloudWatch Observability addon policies for Container Insights
        "Policy::arn:<AWS::Partition>:iam::aws:policy/CloudWatchAgentServerPolicy",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/AWSXrayWriteOnlyAccess",
    ]

    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason=(
                    "EKS requires AWS managed policies for cluster, node, and add-on functionality. "
                    "These are AWS-recommended policies that provide necessary permissions for EKS Auto Mode. "
                    "See: https://docs.aws.amazon.com/eks/latest/userguide/security-iam-awsmanpol.html"
                ),
                applies_to=eks_managed_policies,
            ),
        ],
    )


def add_lambda_suppressions(stack: Stack) -> None:
    """Add suppressions for Lambda-related cdk-nag findings.

    Lambda functions used for CDK custom resources and infrastructure
    automation have specific requirements that trigger cdk-nag warnings.
    """
    lambda_managed_policies = [
        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
    ]

    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason=(
                    "Lambda basic execution and VPC access roles are AWS-recommended managed policies. "
                    "They provide minimal permissions for CloudWatch Logs and VPC ENI management. "
                    "See: https://docs.aws.amazon.com/lambda/latest/dg/lambda-intro-execution-role.html"
                ),
                applies_to=lambda_managed_policies,
            ),
            NagPackSuppression(
                id="AwsSolutions-L1",
                reason=(
                    "CDK Provider framework Lambda functions use a specific runtime version "
                    "managed by CDK. These are internal functions not exposed to users."
                ),
            ),
            # HIPAA Lambda suppressions
            NagPackSuppression(
                id="HIPAA.Security-LambdaConcurrency",
                reason=(
                    "Infrastructure Lambda functions (custom resources) are invoked only during "
                    "stack deployment and do not require concurrency limits. They are not user-facing."
                ),
            ),
            NagPackSuppression(
                id="HIPAA.Security-LambdaDLQ",
                reason=(
                    "CDK custom resource Lambda functions have built-in retry logic and report "
                    "failures directly to CloudFormation. DLQ is not applicable for this pattern."
                ),
            ),
            NagPackSuppression(
                id="HIPAA.Security-LambdaInsideVPC",
                reason=(
                    "CDK Provider framework Lambda functions need internet access to communicate "
                    "with CloudFormation. VPC placement would require NAT Gateway configuration. "
                    "User-facing Lambda functions (kubectl applier) ARE placed in VPC."
                ),
            ),
            # NIST 800-53 Lambda suppressions
            NagPackSuppression(
                id="NIST.800.53.R5-LambdaConcurrency",
                reason=(
                    "Infrastructure Lambda functions (custom resources) are invoked only during "
                    "stack deployment and do not require concurrency limits."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-LambdaDLQ",
                reason=(
                    "CDK custom resource Lambda functions have built-in retry logic and report "
                    "failures directly to CloudFormation. DLQ is not applicable."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-LambdaInsideVPC",
                reason=(
                    "CDK Provider framework Lambda functions need internet access to communicate "
                    "with CloudFormation. User-facing Lambda functions ARE placed in VPC."
                ),
            ),
            # PCI DSS Lambda suppressions
            NagPackSuppression(
                id="PCI.DSS.321-LambdaInsideVPC",
                reason=(
                    "CDK Provider framework Lambda functions need internet access to communicate "
                    "with CloudFormation. User-facing Lambda functions ARE placed in VPC."
                ),
            ),
            # Serverless Lambda suppressions
            NagPackSuppression(
                id="Serverless-LambdaLatestVersion",
                reason=(
                    "CDK Provider framework Lambda functions use a specific runtime version "
                    "managed by CDK. These are internal functions not exposed to users."
                ),
            ),
            NagPackSuppression(
                id="Serverless-LambdaDefaultMemorySize",
                reason=(
                    "CDK Provider framework Lambda functions have appropriate memory for their "
                    "workload. Custom Lambda functions have explicit memory configuration."
                ),
            ),
            NagPackSuppression(
                id="Serverless-LambdaDLQ",
                reason=(
                    "CDK custom resource Lambda functions have built-in retry logic and report "
                    "failures directly to CloudFormation. DLQ is not applicable."
                ),
            ),
        ],
    )


def add_iam_suppressions(
    stack: Stack, regions: list[str] | None = None, global_region: str | None = None
) -> None:
    """Add suppressions for IAM-related cdk-nag findings.

    CDK generates inline policies for custom resources and some patterns
    require wildcard permissions for dynamic resource access.

    Args:
        stack: The CDK stack to apply suppressions to
        regions: List of regional deployment regions (for EKS addon patterns)
        global_region: Global region for SSM parameters and DynamoDB tables
    """
    # Build dynamic applies_to list based on configured regions
    applies_to = [
        "Resource::<KubectlApplierFunction6147DA0C.Arn>:*",
        "Resource::<GaRegistrationFunction4A12C41B.Arn>:*",
        "Resource::<HelmInstallerFunction3FEB04EF.Arn>:*",
        "Resource::<VpcFlowLogGroup86559C69.Arn>:*",
        # Secrets Manager cross-region access with wildcard for suffix
        f"Resource::arn:aws:secretsmanager:{global_region or 'us-east-2'}:<AWS::AccountId>:secret:gco/api-gateway-auth-token*",
    ]

    # Add EKS addon patterns for each configured region
    if regions:
        for region in regions:
            applies_to.append(
                f"Resource::arn:aws:eks:{region}:<AWS::AccountId>:addon/<GCOEksCluster841A896A>/*"
            )

    # Add SSM parameter patterns for global region and all regional regions.
    # Using ``dict.fromkeys`` (insertion-ordered) + sorting gives a stable
    # ordering so the cdk-nag metadata block doesn't churn between synths
    # when PYTHONHASHSEED changes — previous ``set()`` iteration order was
    # hash-based and produced non-deterministic template diffs.
    ssm_regions_set: set[str] = set()
    if global_region:
        ssm_regions_set.add(global_region)
    if regions:
        ssm_regions_set.update(regions)

    for region in sorted(ssm_regions_set):
        applies_to.append(f"Resource::arn:aws:ssm:{region}:<AWS::AccountId>:parameter/gco/*")

    # Add DynamoDB index wildcard patterns for global region
    # Tables are created in global stack, accessed from all regional stacks
    if global_region:
        applies_to.extend(
            [
                f"Resource::arn:aws:dynamodb:{global_region}:<AWS::AccountId>:table/gco-job-templates/index/*",
                f"Resource::arn:aws:dynamodb:{global_region}:<AWS::AccountId>:table/gco-webhooks/index/*",
                f"Resource::arn:aws:dynamodb:{global_region}:<AWS::AccountId>:table/gco-jobs/index/*",
                f"Resource::arn:aws:dynamodb:{global_region}:<AWS::AccountId>:table/gco-inference-endpoints/index/*",
            ]
        )

    # Add S3 wildcard patterns for model weights bucket
    # Bucket name is auto-generated by CDK, so we use a prefix pattern
    applies_to.extend(
        [
            "Resource::arn:aws:s3:::gco-*",
            "Resource::arn:aws:s3:::gco-*/*",
        ]
    )

    # KMS wildcard scoped to S3 via condition for model weights bucket decryption
    applies_to.append("Resource::arn:aws:kms:*:<AWS::AccountId>:key/*")

    NagSuppressions.add_stack_suppressions(
        stack,
        [
            # Inline policy suppressions for all frameworks
            NagPackSuppression(
                id="HIPAA.Security-IAMNoInlinePolicy",
                reason=(
                    "CDK generates inline policies for custom resources and Lambda functions. "
                    "These are scoped to specific resources and follow least-privilege principles."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-IAMNoInlinePolicy",
                reason=(
                    "CDK generates inline policies for custom resources and Lambda functions. "
                    "These are scoped to specific resources and follow least-privilege principles."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-IAMNoInlinePolicy",
                reason=(
                    "CDK generates inline policies for custom resources and Lambda functions. "
                    "These are scoped to specific resources and follow least-privilege principles."
                ),
            ),
            # Wildcard permission suppressions
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason=(
                    "Wildcard permissions are required for: (1) EKS cluster admin access to manage "
                    "dynamic Kubernetes resources, (2) Custom resource providers to invoke Lambda versions, "
                    "(3) SSM parameter access for cross-region coordination, (4) EKS addon management, "
                    "(5) VPC Flow Logs to write to CloudWatch, (6) Secrets Manager cross-region access "
                    "with wildcard suffix for auth token, (7) DynamoDB GSI access for job queue, templates, "
                    "webhooks, and inference endpoints tables, (8) S3 access for model weights bucket "
                    "(auto-generated name). All wildcards are scoped to specific patterns. "
                    "(9) KMS decrypt scoped to S3 via condition for model weights bucket."
                ),
                applies_to=applies_to,
            ),
        ],
    )


def add_vpc_suppressions(stack: Stack) -> None:
    """Add suppressions for VPC-related cdk-nag findings.

    Public subnets and IGW routes are required for ALB and NAT Gateway
    functionality in a multi-tier architecture.
    """
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            # HIPAA VPC suppressions
            NagPackSuppression(
                id="HIPAA.Security-VPCSubnetAutoAssignPublicIpDisabled",
                reason=(
                    "Public subnets are required for internet-facing ALB. EC2 instances "
                    "(EKS nodes) are deployed only in private subnets."
                ),
            ),
            NagPackSuppression(
                id="HIPAA.Security-VPCNoUnrestrictedRouteToIGW",
                reason=(
                    "Public subnets require IGW route for ALB to receive traffic from "
                    "Global Accelerator. All compute resources are in private subnets."
                ),
            ),
            # NIST 800-53 VPC suppressions
            NagPackSuppression(
                id="NIST.800.53.R5-VPCSubnetAutoAssignPublicIpDisabled",
                reason=(
                    "Public subnets are required for internet-facing ALB. EC2 instances "
                    "(EKS nodes) are deployed only in private subnets."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-VPCNoUnrestrictedRouteToIGW",
                reason=(
                    "Public subnets require IGW route for ALB to receive traffic from "
                    "Global Accelerator. All compute resources are in private subnets."
                ),
            ),
            # PCI DSS VPC suppressions
            NagPackSuppression(
                id="PCI.DSS.321-VPCSubnetAutoAssignPublicIpDisabled",
                reason=(
                    "Public subnets are required for internet-facing ALB. EC2 instances "
                    "(EKS nodes) are deployed only in private subnets."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-VPCNoUnrestrictedRouteToIGW",
                reason=(
                    "Public subnets require IGW route for ALB to receive traffic from "
                    "Global Accelerator. All compute resources are in private subnets."
                ),
            ),
        ],
    )


def add_api_gateway_suppressions(stack: Stack) -> None:
    """Add suppressions for API Gateway-related cdk-nag findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-COG4",
                reason=(
                    "API Gateway uses IAM authentication (SigV4) instead of Cognito. "
                    "This is intentional for machine-to-machine API access patterns."
                ),
            ),
            NagPackSuppression(
                id="AwsSolutions-APIG2",
                reason=(
                    "Request validation is performed by the backend Manifest Processor service "
                    "which has detailed schema validation. API Gateway acts as a pass-through proxy."
                ),
            ),
            # Cache suppressions - caching is intentionally disabled
            NagPackSuppression(
                id="HIPAA.Security-APIGWCacheEnabledAndEncrypted",
                reason=(
                    "Caching is disabled intentionally. Manifest submissions are unique "
                    "and should not be cached. Health checks need real-time data."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-APIGWCacheEnabledAndEncrypted",
                reason=(
                    "Caching is disabled intentionally. Manifest submissions are unique "
                    "and should not be cached. Health checks need real-time data."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-APIGWCacheEnabledAndEncrypted",
                reason=(
                    "Caching is disabled intentionally. Manifest submissions are unique "
                    "and should not be cached. Health checks need real-time data."
                ),
            ),
            # SSL certificate suppressions
            NagPackSuppression(
                id="HIPAA.Security-APIGWSSLEnabled",
                reason=(
                    "Backend SSL certificates are not required as traffic flows through "
                    "Global Accelerator (TLS terminated) to internal ALB (HTTPS)."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-APIGWSSLEnabled",
                reason=(
                    "Backend SSL certificates are not required as traffic flows through "
                    "Global Accelerator (TLS terminated) to internal ALB (HTTPS)."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-APIGWSSLEnabled",
                reason=(
                    "Backend SSL certificates are not required as traffic flows through "
                    "Global Accelerator (TLS terminated) to internal ALB (HTTPS)."
                ),
            ),
            # CloudWatch Log Group encryption suppressions
            NagPackSuppression(
                id="HIPAA.Security-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs are encrypted by default with AWS-managed keys. "
                    "Customer-managed KMS keys can be enabled via configuration if required."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs are encrypted by default with AWS-managed keys. "
                    "Customer-managed KMS keys can be enabled via configuration if required."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs are encrypted by default with AWS-managed keys. "
                    "Customer-managed KMS keys can be enabled via configuration if required."
                ),
            ),
            # API Gateway CloudWatch role
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason=(
                    "API Gateway CloudWatch role requires the AWS managed policy "
                    "AmazonAPIGatewayPushToCloudWatchLogs for logging functionality."
                ),
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs",
                ],
            ),
            # CdkNagValidationFailure for structured logging check
            NagPackSuppression(
                id="CdkNagValidationFailure",
                reason=(
                    "Validation failure due to CloudFormation intrinsic functions. "
                    "Access logging is properly configured on the API Gateway stage."
                ),
            ),
        ],
    )


def add_monitoring_suppressions(stack: Stack) -> None:
    """Add suppressions for monitoring-related cdk-nag findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-SNS3",
                reason="SNS topic has enforce_ssl=True enabled, which adds the required policy.",
            ),
            NagPackSuppression(
                id="HIPAA.Security-SNSEncryptedKMS",
                reason=(
                    "Alert notifications contain operational data (alarm names, thresholds) "
                    "not PHI. KMS encryption adds latency to time-sensitive alerts."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-SNSEncryptedKMS",
                reason=(
                    "Alert notifications contain operational data (alarm names, thresholds). "
                    "KMS encryption adds latency to time-sensitive alerts."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-SNSEncryptedKMS",
                reason=(
                    "Alert notifications contain operational data (alarm names, thresholds). "
                    "KMS encryption can be enabled if required for PCI compliance."
                ),
            ),
            # CloudWatch Log Group encryption
            NagPackSuppression(
                id="HIPAA.Security-CloudWatchLogGroupEncrypted",
                reason="CloudWatch Logs are encrypted by default with AWS-managed keys.",
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                reason="CloudWatch Logs are encrypted by default with AWS-managed keys.",
            ),
            NagPackSuppression(
                id="PCI.DSS.321-CloudWatchLogGroupEncrypted",
                reason="CloudWatch Logs are encrypted by default with AWS-managed keys.",
            ),
            # CloudWatch Alarm Action suppressions for composite alarm inputs
            # These alarms are intentionally used only as inputs to composite alarms
            # The composite alarms have actions attached, not the individual alarms
            NagPackSuppression(
                id="HIPAA.Security-CloudWatchAlarmAction",
                reason=(
                    "These alarms are inputs to composite alarms which have SNS actions. "
                    "Individual alarms don't need actions as they're aggregated for better signal-to-noise."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-CloudWatchAlarmAction",
                reason=(
                    "These alarms are inputs to composite alarms which have SNS actions. "
                    "Individual alarms don't need actions as they're aggregated for better signal-to-noise."
                ),
            ),
        ],
    )


def add_storage_suppressions(stack: Stack) -> None:
    """Add suppressions for storage-related cdk-nag findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            # EFS backup suppressions
            NagPackSuppression(
                id="HIPAA.Security-EFSInBackupPlan",
                reason=(
                    "EFS backup is optional and can be enabled via AWS Backup if required. "
                    "Default deployment prioritizes cost optimization."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-EFSInBackupPlan",
                reason=(
                    "EFS backup is optional and can be enabled via AWS Backup if required. "
                    "Default deployment prioritizes cost optimization."
                ),
            ),
            # CloudWatch Log Group encryption
            NagPackSuppression(
                id="HIPAA.Security-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs are encrypted by default with AWS-managed keys. "
                    "CDK Provider log groups are for infrastructure automation only."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs are encrypted by default with AWS-managed keys. "
                    "CDK Provider log groups are for infrastructure automation only."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs are encrypted by default with AWS-managed keys. "
                    "CDK Provider log groups are for infrastructure automation only."
                ),
            ),
        ],
    )


def add_sqs_suppressions(stack: Stack) -> None:
    """Add suppressions for SQS-related cdk-nag findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-SQS4",
                reason="SQS queues have enforce_ssl=True enabled, which adds the required policy.",
            ),
            NagPackSuppression(
                id="Serverless-SQSRedrivePolicy",
                reason=(
                    "The dead-letter queue itself does not need a redrive policy. "
                    "The main job queue has a redrive policy pointing to the DLQ."
                ),
            ),
        ],
    )


def add_secrets_suppressions(stack: Stack) -> None:
    """Add suppressions for Secrets Manager-related cdk-nag findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            # KMS key suppressions - using AWS-managed keys is acceptable
            NagPackSuppression(
                id="HIPAA.Security-SecretsManagerUsingKMSKey",
                reason=(
                    "Secrets Manager encrypts secrets by default with AWS-managed keys. "
                    "Customer-managed KMS can be enabled if required for compliance."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-SecretsManagerUsingKMSKey",
                reason="Secrets Manager encrypts secrets by default with AWS-managed keys.",
            ),
            NagPackSuppression(
                id="PCI.DSS.321-SecretsManagerUsingKMSKey",
                reason=(
                    "Secrets Manager encrypts secrets by default with AWS-managed keys. "
                    "Customer-managed KMS can be enabled if required for PCI compliance."
                ),
            ),
        ],
    )


def add_eks_cluster_suppressions(stack: Stack) -> None:
    """Add suppressions for EKS cluster-specific findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-EKS1",
                reason=(
                    "EKS public endpoint is enabled for kubectl access from CI/CD pipelines "
                    "and developer workstations. Access is controlled via IAM."
                ),
            ),
            # CdkNagValidationFailure suppressions for security group rules with intrinsic functions
            NagPackSuppression(
                id="CdkNagValidationFailure",
                reason=(
                    "Security group rules use VPC CIDR block via CloudFormation intrinsic function. "
                    "The rule restricts access to VPC CIDR only, which is secure."
                ),
            ),
        ],
    )


def add_backup_suppressions(stack: Stack) -> None:
    """Add suppressions for AWS Backup-related cdk-nag findings."""
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason=(
                    "AWS Backup requires the AWSBackupServiceRolePolicyForBackup managed policy "
                    "attached to the backup service role to perform backup operations on DynamoDB tables. "
                    "This is the AWS-recommended policy for AWS Backup default service roles. "
                    "See: https://docs.aws.amazon.com/aws-backup/latest/devguide/iam-service-roles.html"
                ),
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup",
                ],
            ),
        ],
    )


def add_aurora_pgvector_suppressions(stack: Stack) -> None:
    """Add suppressions for Aurora pgvector-related cdk-nag findings.

    Aurora Serverless v2 with pgvector triggers several compliance findings
    that are intentionally accepted for this deployment pattern.
    """
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            # Secrets Manager KMS key — Aurora secret uses AWS-managed encryption
            NagPackSuppression(
                id="HIPAA.Security-SecretsManagerUsingKMSKey",
                reason=(
                    "Aurora Serverless v2 credentials in Secrets Manager are encrypted with "
                    "AWS-managed keys by default. Customer-managed KMS can be enabled if required."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-SecretsManagerUsingKMSKey",
                reason=(
                    "Aurora Serverless v2 credentials in Secrets Manager are encrypted with "
                    "AWS-managed keys by default."
                ),
            ),
            # Secrets Manager rotation — Aurora manages rotation via RDS integration
            NagPackSuppression(
                id="HIPAA.Security-SecretsManagerRotationEnabled",
                reason=(
                    "Aurora manages credential rotation via the RDS integration with Secrets "
                    "Manager. Manual rotation configuration is not required."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-SecretsManagerRotationEnabled",
                reason=(
                    "Aurora manages credential rotation via the RDS integration with Secrets "
                    "Manager. Manual rotation configuration is not required."
                ),
            ),
            # RDS in backup plan — Aurora has built-in continuous backups
            NagPackSuppression(
                id="HIPAA.Security-RDSInBackupPlan",
                reason=(
                    "Aurora Serverless v2 has built-in continuous backups with point-in-time "
                    "recovery. AWS Backup integration is optional and can be enabled if required."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-RDSInBackupPlan",
                reason=(
                    "Aurora Serverless v2 has built-in continuous backups with point-in-time "
                    "recovery. AWS Backup integration is optional."
                ),
            ),
            # RDS logging enabled — covered by cloudwatch_logs_exports=["postgresql"]
            # but some frameworks check for additional log types
            NagPackSuppression(
                id="HIPAA.Security-RDSLoggingEnabled",
                reason=(
                    "PostgreSQL logs are exported to CloudWatch via cloudwatch_logs_exports. "
                    "Aurora Serverless v2 does not support all log types available on provisioned instances."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-RDSLoggingEnabled",
                reason=(
                    "PostgreSQL logs are exported to CloudWatch via cloudwatch_logs_exports. "
                    "Aurora Serverless v2 does not support all log types available on provisioned instances."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-RDSLoggingEnabled",
                reason=(
                    "PostgreSQL logs are exported to CloudWatch via cloudwatch_logs_exports. "
                    "Aurora Serverless v2 does not support all log types available on provisioned instances."
                ),
            ),
            # CloudWatch Log Group encryption for Aurora logs
            NagPackSuppression(
                id="HIPAA.Security-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs for Aurora PostgreSQL are encrypted by default with "
                    "AWS-managed keys. Customer-managed KMS can be enabled if required."
                ),
            ),
            NagPackSuppression(
                id="NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs for Aurora PostgreSQL are encrypted by default with "
                    "AWS-managed keys."
                ),
            ),
            NagPackSuppression(
                id="PCI.DSS.321-CloudWatchLogGroupEncrypted",
                reason=(
                    "CloudWatch Logs for Aurora PostgreSQL are encrypted by default with "
                    "AWS-managed keys."
                ),
            ),
            # Enhanced monitoring IAM role uses AWS managed policy
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason=(
                    "Aurora enhanced monitoring requires the AWS managed policy "
                    "AmazonRDSEnhancedMonitoringRole for publishing OS-level metrics to CloudWatch. "
                    "This is the AWS-recommended policy for RDS enhanced monitoring. "
                    "See: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Monitoring.OS.Enabling.html"
                ),
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole",
                ],
            ),
        ],
    )


def apply_all_suppressions(
    stack: Stack,
    stack_type: str = "regional",
    regions: list[str] | None = None,
    global_region: str | None = None,
) -> None:
    """Apply all relevant suppressions to a stack.

    Args:
        stack: The CDK stack to apply suppressions to
        stack_type: Type of stack - 'regional', 'global', 'api_gateway', or 'monitoring'
        regions: List of regional deployment regions (for dynamic IAM suppression patterns)
        global_region: Global region for SSM parameters (for dynamic IAM suppression patterns)
    """
    # Common suppressions for all stacks
    add_lambda_suppressions(stack)
    add_iam_suppressions(stack, regions=regions, global_region=global_region)

    if stack_type == "regional":
        add_eks_suppressions(stack)
        add_eks_cluster_suppressions(stack)
        add_vpc_suppressions(stack)
        add_storage_suppressions(stack)
        add_sqs_suppressions(stack)
        add_aurora_pgvector_suppressions(stack)

    elif stack_type == "global":
        add_backup_suppressions(stack)

    elif stack_type == "api_gateway":
        add_api_gateway_suppressions(stack)
        add_secrets_suppressions(stack)

    elif stack_type == "monitoring":
        add_monitoring_suppressions(stack)
