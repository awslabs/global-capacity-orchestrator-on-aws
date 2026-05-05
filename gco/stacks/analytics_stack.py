"""Analytics stack for GCO - optional ML/analytics environment.

Instantiated only when ``analytics_environment.enabled=true`` in ``cdk.json``.
When the toggle is ``false`` (the default), ``app.py`` skips creating it so
``cdk synth`` emits no SageMaker, EMR Serverless, or Cognito resources.

Resources (wired in this order):

1. ``_create_kms_key``                                    — ``Analytics_KMS_Key``
2. ``_create_vpc_and_endpoints``                          — private-isolated VPC + endpoints
3. ``_create_access_logs_bucket``                         — S3 access-logs bucket
4. ``_create_studio_only_bucket``                         — ``Studio_Only_Bucket``
5. ``_create_studio_efs``                                 — ``Studio_EFS``
6. ``_create_execution_role_and_grants``                  — ``SageMaker_Execution_Role``
7. ``_grant_sagemaker_role_on_cluster_shared_bucket``     — cross-region IAM grant
8. ``_create_studio_domain``                              — ``sagemaker.CfnDomain``
9. ``_create_emr_app``                                    — ``emrserverless.CfnApplication``
10. ``_create_cognito_pool``                              — Cognito pool + client + domain
11. ``_create_presigned_url_lambda``                      — ``Presigned_URL_Lambda``
12. ``_apply_nag_suppressions``                           — analytics-branch nag dispatch

The API Gateway ``/studio/*`` wiring that consumes this Lambda lives in
``gco/stacks/api_gateway_global_stack.py``.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_efs as efs
from aws_cdk import aws_emrserverless as emrserverless
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sagemaker as sagemaker
from aws_cdk import custom_resources as cr
from constructs import Construct

from gco.config.config_loader import ConfigLoader
from gco.stacks.constants import (
    CLUSTER_SHARED_SSM_PARAMETER_PREFIX,
    COGNITO_DOMAIN_PREFIX_DEFAULT,
    EMR_SERVERLESS_RELEASE_LABEL,
    LAMBDA_PYTHON_RUNTIME,
    SAGEMAKER_ROLE_NAME_PREFIX,
)
from gco.stacks.nag_suppressions import apply_all_suppressions


def _parse_removal(value: str) -> RemovalPolicy:
    """Map a cdk.json removal-policy string to ``aws_cdk.RemovalPolicy``.

    Translates ``analytics_environment.{efs,cognito}.removal_policy`` into
    the matching enum member. Accepts ``"retain"`` / ``"destroy"``
    (case-insensitive); raises ``ValueError`` on anything else.
    """
    normalized = value.strip().lower()
    if normalized == "retain":
        return RemovalPolicy.RETAIN
    if normalized == "destroy":
        return RemovalPolicy.DESTROY
    raise ValueError(
        f"analytics_environment removal_policy must be 'retain' or 'destroy', got {value!r}"
    )


class GCOAnalyticsStack(Stack):
    """Optional ML/analytics environment: SageMaker Studio, EMR Serverless, Cognito.

    Only instantiated when ``analytics_environment.enabled=true``. Lives in
    the API gateway region so the presigned-URL Lambda can wire into the
    existing ``/studio/*`` routes on ``GCOApiGatewayGlobalStack`` without
    a cross-region hop.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ConfigLoader,
        api_gateway_secret_arn: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = config
        # ``api_gateway_secret_arn`` is reserved for future auth wiring;
        # accepted now so the constructor signature is stable.
        self.api_gateway_secret_arn = api_gateway_secret_arn

        cfg = config.get_analytics_config()
        self.hyperpod_enabled: bool = bool(cfg["hyperpod"]["enabled"])
        self.efs_removal: RemovalPolicy = _parse_removal(cfg["efs"]["removal_policy"])
        self.cognito_removal: RemovalPolicy = _parse_removal(cfg["cognito"]["removal_policy"])
        self._cognito_domain_prefix_override: str | None = cfg["cognito"].get("domain_prefix")

        # Wiring order is load-bearing — each helper consumes resources from
        # earlier helpers (EFS ARN → execution role → studio domain, etc.).
        self._create_kms_key()
        self._create_vpc_and_endpoints()
        self._create_access_logs_bucket()
        self._create_studio_only_bucket()
        self._create_studio_efs()
        self._create_execution_role_and_grants()
        self._grant_sagemaker_role_on_cluster_shared_bucket()
        self._create_studio_domain()
        self._create_emr_app()
        self._create_cognito_pool()
        self._create_presigned_url_lambda()
        self._apply_nag_suppressions()

    # ==================================================================
    # KMS + VPC (tasks 7.2, 7.3)
    # ==================================================================

    def _create_kms_key(self) -> None:
        """Create ``Analytics_KMS_Key`` with rotation + 7-day pending window.

        Customer-managed so every analytics-owned bucket, the Studio EFS,
        and SageMaker-written artifacts share a single encryption boundary.
        ``removal_policy=DESTROY`` follows the iteration-loop posture
        — the 7-day pending window gives recovery headroom without retaining
        the key past a ``cdk destroy gco-analytics`` cycle.
        """
        self.kms_key = kms.Key(
            self,
            "AnalyticsKmsKey",
            description="Analytics_KMS_Key - encrypts analytics S3 buckets, Studio EFS, SageMaker artifacts",
            enable_key_rotation=True,
            pending_window=Duration.days(7),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Grant encrypt/decrypt to service principals that need to operate
        # on analytics-owned resources encrypted by this key.
        service_principals = [
            f"logs.{self.region}.amazonaws.com",
            "sagemaker.amazonaws.com",
            "s3.amazonaws.com",
            "elasticfilesystem.amazonaws.com",
        ]
        for principal in service_principals:
            self.kms_key.add_to_resource_policy(
                iam.PolicyStatement(
                    sid=f"Allow{principal.split('.')[0].capitalize()}Encrypt",
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ServicePrincipal(principal)],
                    actions=[
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:ReEncrypt*",
                        "kms:GenerateDataKey*",
                        "kms:DescribeKey",
                    ],
                    resources=["*"],  # key-policy scope — always the key itself
                )
            )

    def _create_vpc_and_endpoints(self) -> None:
        """Create a private-isolated VPC plus every VPC endpoint Studio needs.

        Notebooks never land on public subnets (the VPC has none).
        The nine interface endpoints plus the S3 gateway endpoint
        keep all Studio/EMR/EFS traffic on the private-isolated network
        without NAT.
        """
        self.vpc = ec2.Vpc(
            self,
            "AnalyticsVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="AnalyticsPrivateIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                )
            ],
        )

        # Gateway endpoint for S3 — route tables are wired up automatically.
        self.vpc.add_gateway_endpoint(
            "S3GatewayEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        # Interface endpoints — one per AWS service required by Studio. Each
        # lands in the VPC's private-isolated subnets using the default
        # VPC-endpoint security group.
        interface_services: dict[str, ec2.InterfaceVpcEndpointAwsService] = {
            "SagemakerApiEndpoint": ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_API,
            "SagemakerRuntimeEndpoint": ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_RUNTIME,
            "SagemakerStudioEndpoint": ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_STUDIO,
            "SagemakerNotebookEndpoint": ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_NOTEBOOK,
            "StsEndpoint": ec2.InterfaceVpcEndpointAwsService.STS,
            "CloudWatchLogsEndpoint": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            "EcrEndpoint": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDockerEndpoint": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            "EfsEndpoint": ec2.InterfaceVpcEndpointAwsService.ELASTIC_FILESYSTEM,
        }
        for construct_id, service in interface_services.items():
            self.vpc.add_interface_endpoint(
                construct_id,
                service=service,
                subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            )

    # ==================================================================
    # S3 buckets
    # ==================================================================

    def _create_access_logs_bucket(self) -> None:
        """Create the dedicated access-logs bucket for ``Studio_Only_Bucket``.

        Server-side encryption uses S3-managed keys (SSE-S3) because S3
        server-access-log delivery does not support KMS-encrypted destinations
        without additional log-delivery role plumbing — the standard pattern
        is SSE-S3 for the log sink plus KMS for the bucket it logs. The
        resulting ``AwsSolutions-S1`` nag finding for the log sink targeting
        itself is scoped on the bucket construct by
        ``add_storage_suppressions`` via the analytics nag branch.
        """
        self.access_logs_bucket = s3.Bucket(
            self,
            "AnalyticsAccessLogsBucket",
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
                    expiration=Duration.days(90),
                )
            ],
        )

    def _create_studio_only_bucket(self) -> None:
        """Create ``Studio_Only_Bucket`` for notebook-private scratch + outputs.

        Named ``gco-analytics-studio-<account>-<region>`` so the cdk-nag
        deny-list assertion (``arn:aws:s3:::gco-analytics-studio-*``) stays
        stable. KMS-encrypted with ``self.kms_key``; every access path goes
        through the ``SageMaker_Execution_Role`` grant — no other principal
        is granted access.
        """
        self.studio_only_bucket = s3.Bucket(
            self,
            "StudioOnlyBucket",
            bucket_name=f"gco-analytics-studio-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.kms_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_logs_bucket,
            server_access_logs_prefix="studio-only/",
        )

        # Belt-and-suspenders Deny for insecure transport, duplicating the
        # ``enforce_ssl=True`` semantics with a verifiable SID in the
        # synthesized template (mirrors the ``DenyInsecureTransport`` pattern
        # used by ``Cluster_Shared_Bucket`` in ``GCOGlobalStack``).
        self.studio_only_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyInsecureTransport",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:*"],
                resources=[
                    self.studio_only_bucket.bucket_arn,
                    f"{self.studio_only_bucket.bucket_arn}/*",
                ],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )

    # ==================================================================
    # Studio EFS
    # ==================================================================

    def _create_studio_efs(self) -> None:
        """Create ``Studio_EFS`` with KMS encryption + TLS in transit.

        Per-user access points are created lazily by the presigned-URL
        Lambda on first profile creation. No access points are defined
        here, so the file system's ``/`` root is effectively inaccessible
        until the Lambda materializes a per-user AP.

        The dedicated security group only allows the VPC's private-isolated
        CIDR on TCP/2049 (NFS). SageMaker Studio mount traffic originates
        from the Studio compute subnet, which shares the VPC with this EFS.
        """
        self.studio_efs_security_group = ec2.SecurityGroup(
            self,
            "StudioEfsSecurityGroup",
            vpc=self.vpc,
            description="SG for Studio_EFS - allows NFS from the analytics VPC only",
            allow_all_outbound=False,
        )
        self.studio_efs_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2049),
            description="NFS from analytics VPC private-isolated subnets",
        )

        self.studio_efs = efs.FileSystem(
            self,
            "StudioEfs",
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            encrypted=True,
            kms_key=self.kms_key,
            enable_automatic_backups=True,
            removal_policy=self.efs_removal,
            security_group=self.studio_efs_security_group,
        )

    # ==================================================================
    # SageMaker execution role + grants
    # ==================================================================

    def _create_execution_role_and_grants(self) -> None:
        """Create ``SageMaker_Execution_Role`` and attach its (non-cluster-shared) grants.

        Role name begins with ``AmazonSageMaker`` — SageMaker
        requires this prefix for any role used by a Studio domain. Grants
        attached here:

        * RW on ``Studio_Only_Bucket`` + KMS on ``Analytics_KMS_Key``
        * Read-only ``execute-api:Invoke`` on GCO API Gateway ``/api/v1/*`` GET routes
        * ``sqs:SendMessage`` on regional job queues (wildcard ARN pattern)
        * EFS mount actions on ``Studio_EFS`` (specific AP arn is added by
          the presigned-URL Lambda at runtime; the role-level grant here is
          scoped to the EFS ARN)
        * HyperPod training-job actions when ``hyperpod.enabled=true``

        The ``Cluster_Shared_Bucket`` grant lives in its own helper
        (:meth:`_grant_sagemaker_role_on_cluster_shared_bucket`) because the
        bucket ARN is resolved via a cross-region SSM read.
        """
        self.sagemaker_execution_role = iam.Role(
            self,
            "SagemakerExecutionRole",
            role_name=f"{SAGEMAKER_ROLE_NAME_PREFIX}-gco-analytics-exec-{self.region}",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            description=(
                "SageMaker_Execution_Role - assumed by notebooks in the Studio "
                "domain. Grants RW on Studio_Only_Bucket and (via a separate "
                "cross-region policy) Cluster_Shared_Bucket, plus read-only GCO "
                "API access and SQS job submission."
            ),
        )

        # Bucket + KMS grants — studio-only scratch space. Analytics_KMS_Key
        # already has encrypt/decrypt in its key policy for the sagemaker
        # service principal, but role-level grants are still required for
        # IAM-side authorization per the double-auth model.
        self.studio_only_bucket.grant_read_write(self.sagemaker_execution_role)
        self.kms_key.grant_encrypt_decrypt(self.sagemaker_execution_role)

        # Read-only GCO API scope: every GET route under /api/v1/*. The
        # exact API id is not known here (it lives in the api-gateway stack
        # and is discovered through SSM or CfnOutput at synth time — see
        # the api_gateway_global_stack wiring). Scope to the api-gateway
        # region with any REST API id for now; tighter scope is applied
        # once ``AnalyticsApiConfig`` is wired in.
        api_gw_region = self.config.get_api_gateway_region()
        self.sagemaker_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["execute-api:Invoke"],
                resources=[
                    f"arn:aws:execute-api:{api_gw_region}:{self.account}:*/prod/GET/api/v1/*",
                ],
            )
        )

        # SQS job submission — scoped to the regional queue name pattern
        # ``<project>-jobs-<region>`` written by
        # ``GCORegionalStack._create_sqs_queue``. The exact region isn't
        # known at synth time (queues live in regional stacks), so we use
        # ``*`` in the region component with the project name fixed.
        project_name = self.config.get_project_name()
        self.sagemaker_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sqs:SendMessage"],
                resources=[
                    f"arn:aws:sqs:*:{self.account}:{project_name}-jobs-*",
                ],
            )
        )

        # EFS mount actions — the ``elasticfilesystem:AccessPointArn``
        # condition that scopes this to the per-user AP is attached by the
        # presigned-URL Lambda on AP creation. At this stage the grant is
        # scoped to the EFS file-system ARN.
        self.sagemaker_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "elasticfilesystem:ClientMount",
                    "elasticfilesystem:ClientWrite",
                    "elasticfilesystem:ClientRootAccess",
                ],
                resources=[self.studio_efs.file_system_arn],
            )
        )

        # HyperPod sub-toggle — additional SageMaker actions for training-job
        # submission and cluster-instance lifecycle management.
        # ``resources=["*"]`` is the documented scope; the HyperPod actions
        # themselves encode the per-training-job authorization model.
        if self.hyperpod_enabled:
            self.sagemaker_execution_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "sagemaker:CreateTrainingJob",
                        "sagemaker:DescribeTrainingJob",
                        "sagemaker:StopTrainingJob",
                        "sagemaker:ClusterInstance",
                        "sagemaker:ClusterInstanceGroup",
                        "sagemaker:DescribeClusterNode",
                        "sagemaker:ListClusterNodes",
                    ],
                    resources=["*"],
                )
            )

    def _grant_sagemaker_role_on_cluster_shared_bucket(self) -> None:
        """Attach RW + KMS on ``Cluster_Shared_Bucket`` to ``SageMaker_Execution_Role``.

        The bucket lives in ``GCOGlobalStack`` in the global region. Its
        ARN is resolved at synth time via an ``AwsCustomResource`` that
        issues ``ssm:GetParameter`` against the global region — mirroring
        the pattern used by ``GCORegionalStack._resolve_cluster_shared_bucket_from_ssm``.

        Two statements attach to the role:

        1. S3: ``GetObject``/``PutObject``/``DeleteObject``/``ListBucket``/
           ``GetBucketLocation`` on ``<arn>`` + ``<arn>/*``.
        2. KMS: ``Decrypt``/``GenerateDataKey`` with a
           ``kms:ViaService=s3.<global-region>.amazonaws.com`` condition.

        This is a role-side policy — the bucket policy is owned
        exclusively by ``GCOGlobalStack``.
        """
        from cdk_nag import NagSuppressions

        global_region = self.config.get_global_region()
        parameter_name = f"{CLUSTER_SHARED_SSM_PARAMETER_PREFIX}/arn"

        read_cr = cr.AwsCustomResource(
            self,
            "ReadClusterSharedBucketArn",
            on_create=cr.AwsSdkCall(
                service="SSM",
                action="getParameter",
                parameters={"Name": parameter_name},
                region=global_region,
                physical_resource_id=cr.PhysicalResourceId.of("analytics-cluster-shared-arn"),
            ),
            on_update=cr.AwsSdkCall(
                service="SSM",
                action="getParameter",
                parameters={"Name": parameter_name},
                region=global_region,
                physical_resource_id=cr.PhysicalResourceId.of("analytics-cluster-shared-arn"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )

        # Scoped suppression: same shape as
        # ``GCORegionalStack._resolve_cluster_shared_bucket_from_ssm``. The
        # CR policy is ``Resource::*`` because cross-region SSM does not
        # support resource-level scoping cleanly; the action is a fixed
        # ``ssm:GetParameter`` for a single literal parameter Name.
        NagSuppressions.add_resource_suppressions(
            read_cr,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Cross-region ssm:GetParameter for "
                        f"{parameter_name} in the global region. The "
                        "AwsCustomResource SDK-call policy is scoped to a "
                        "single fixed action (ssm:GetParameter) with a "
                        "fixed parameter Name — the Resource: * is the "
                        "CDK-documented escape hatch because the parameter "
                        "ARN is not known to the calling principal's "
                        "region. Effective blast radius: one parameter."
                    ),
                    "appliesTo": ["Resource::*"],
                },
            ],
            apply_to_children=True,
        )

        shared_arn = read_cr.get_response_field("Parameter.Value")

        # Attach the two policy statements as an inline Policy on the role
        # (policy on the role, not the bucket).
        iam.Policy(
            self,
            "SagemakerClusterSharedBucketGrant",
            roles=[self.sagemaker_execution_role],
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                        "s3:GetBucketLocation",
                    ],
                    resources=[shared_arn, f"{shared_arn}/*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["kms:Decrypt", "kms:GenerateDataKey"],
                    resources=["*"],
                    conditions={
                        "StringEquals": {
                            "kms:ViaService": f"s3.{global_region}.amazonaws.com",
                        }
                    },
                ),
            ],
        )

        # The S3 statement uses an <arn>/* object-key wildcard on the
        # literal cluster-shared bucket ARN resolved from SSM — identical
        # shape to the regional stack's analogous grant, with the same
        # reason text (bucket-scoped RW).
        NagSuppressions.add_resource_suppressions(
            self.sagemaker_execution_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "The SageMaker RW grant on Cluster_Shared_Bucket "
                        "uses an <arn>/* object-key wildcard on the literal "
                        "ARN resolved from SSM. The wildcard covers object "
                        "keys within the single always-on "
                        "gco-cluster-shared-<account>-<region> bucket."
                    ),
                    "appliesTo": [
                        {"regex": (r"/^Resource::<ReadClusterSharedBucketArn.*>\/\*$/")},
                    ],
                },
            ],
            apply_to_children=True,
        )

    # ==================================================================
    # SageMaker Studio domain
    # ==================================================================

    def _create_studio_domain(self) -> None:
        """Create the SageMaker Studio domain bound to the private VPC.

        ``auth_mode=IAM`` + ``app_network_access_type=VpcOnly`` keeps Studio
        traffic on the private-isolated subnets.
        ``DefaultUserSettings.ExecutionRole`` points at the role created in
        :meth:`_create_execution_role_and_grants`. ``CustomImages`` is
        intentionally left unset so Studio falls back to the stock AWS-
        published Distribution images (a tested invariant).

        ``CustomFileSystemConfigs`` mounts ``self.studio_efs`` at
        ``/home/sagemaker-user`` — per-user ``/home/<username>`` isolation
        is enforced by the access points that the presigned-URL Lambda
        creates lazily on first login.
        """
        private_subnets = self.vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
        ).subnets

        efs_fs_config = sagemaker.CfnDomain.EFSFileSystemConfigProperty(
            file_system_id=self.studio_efs.file_system_id,
            file_system_path="/home/sagemaker-user",
        )
        custom_fs_config = sagemaker.CfnDomain.CustomFileSystemConfigProperty(
            efs_file_system_config=efs_fs_config,
        )

        default_user_settings = sagemaker.CfnDomain.UserSettingsProperty(
            execution_role=self.sagemaker_execution_role.role_arn,
            custom_file_system_configs=[custom_fs_config],
            # ``jupyter_lab_app_settings`` is deliberately omitted so
            # ``CustomImages`` stays absent — the template contains no
            # SageMaker image resources and no CustomImages
            # key on the domain.
        )

        self.studio_domain = sagemaker.CfnDomain(
            self,
            "StudioDomain",
            auth_mode="IAM",
            app_network_access_type="VpcOnly",
            domain_name=f"gco-analytics-{self.region}",
            subnet_ids=[s.subnet_id for s in private_subnets],
            vpc_id=self.vpc.vpc_id,
            kms_key_id=self.kms_key.key_id,
            default_user_settings=default_user_settings,
        )

        # The domain validates that the EFS file system has mount targets in
        # every subnet before stabilizing. CDK doesn't infer this dependency
        # from the file_system_id reference alone, so we add it explicitly.
        self.studio_domain.node.add_dependency(self.studio_efs)

        CfnOutput(
            self,
            "StudioDomainName",
            value=self.studio_domain.domain_name or "",
            description="Name of the SageMaker Studio domain",
        )

    # ==================================================================
    # EMR Serverless application
    # ==================================================================

    def _create_emr_app(self) -> None:
        """Create an EMR Serverless Spark application on the private VPC.

        Pinned ``release_label`` lives in
        ``gco.stacks.constants.EMR_SERVERLESS_RELEASE_LABEL`` so analytics
        workloads get a reproducible Spark runtime across deployments. The
        application's network configuration uses the private-isolated
        subnets + a dedicated security group so Spark workers stay on the
        same network perimeter as the Studio notebooks.
        """
        private_subnet_ids = [
            s.subnet_id
            for s in self.vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED).subnets
        ]

        self.emr_security_group = ec2.SecurityGroup(
            self,
            "EmrServerlessSecurityGroup",
            vpc=self.vpc,
            description="SG for EMR Serverless Spark workers",
            allow_all_outbound=True,
        )

        self.emr_app = emrserverless.CfnApplication(
            self,
            "EmrServerlessApp",
            name=f"gco-analytics-spark-{self.region}",
            release_label=EMR_SERVERLESS_RELEASE_LABEL,
            type="SPARK",
            network_configuration=emrserverless.CfnApplication.NetworkConfigurationProperty(
                subnet_ids=private_subnet_ids,
                security_group_ids=[self.emr_security_group.security_group_id],
            ),
        )

    # ==================================================================
    # Cognito pool + client + domain
    # ==================================================================

    def _create_cognito_pool(self) -> None:
        """Create the Cognito user pool that authenticates SageMaker Studio logins.

        Password policy, standard threat-protection mode, and self-sign-up-
        disabled flags are configured for SRP-backed Studio logins. The
        attached ``UserPoolClient`` runs SRP auth
        (used by ``gco analytics studio login``) with token revocation
        enabled. The ``UserPoolDomain`` uses the configurable prefix from
        ``analytics_environment.cognito.domain_prefix`` or defaults to
        ``gco-studio-<account>``.
        """
        self.cognito_pool = cognito.UserPool(
            self,
            "StudioUserPool",
            self_sign_up_enabled=False,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_digits=True,
                require_symbols=True,
                require_uppercase=True,
                require_lowercase=True,
            ),
            sign_in_aliases=cognito.SignInAliases(username=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            # Replaces the deprecated ``advanced_security_mode`` kwarg
            # (aws-cdk-lib's AdvancedSecurityMode enum is gone as of the
            # Cognito November 2024 tier changes). Lite feature plan — the
            # default — does not support real threat protection, so we set
            # ``NO_ENFORCEMENT`` here to keep the synth warning-free.
            # TODO: operators who want real threat protection should opt
            # into the Essentials or Plus feature plan by also setting
            # ``feature_plan=cognito.FeaturePlan.ESSENTIALS`` (or
            # ``FeaturePlan.PLUS``) and flipping this to
            # ``StandardThreatProtectionMode.FULL_FUNCTION``. That path
            # changes the per-MAU price — see the Cognito pricing doc —
            # which is why the default stays on Lite+NO_ENFORCEMENT.
            standard_threat_protection_mode=(cognito.StandardThreatProtectionMode.NO_ENFORCEMENT),
            removal_policy=self.cognito_removal,
        )

        self.cognito_client = self.cognito_pool.add_client(
            "StudioUserPoolClient",
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                admin_user_password=True,
            ),
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
        )

        # Domain prefix — default is ``gco-studio-<account>`` (stock default
        # from constants.COGNITO_DOMAIN_PREFIX_DEFAULT + account suffix).
        # The override in cdk.json is used verbatim when non-None, without
        # appending the account id, because operators who override the
        # prefix typically want a short memorable value.
        if self._cognito_domain_prefix_override:
            domain_prefix = self._cognito_domain_prefix_override
        else:
            domain_prefix = f"{COGNITO_DOMAIN_PREFIX_DEFAULT}-{self.account}"

        self.cognito_domain = self.cognito_pool.add_domain(
            "StudioUserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        CfnOutput(
            self,
            "CognitoUserPoolId",
            value=self.cognito_pool.user_pool_id,
            description="ID of the Cognito user pool that gates SageMaker Studio",
        )
        CfnOutput(
            self,
            "CognitoUserPoolArn",
            value=self.cognito_pool.user_pool_arn,
            description="ARN of the Cognito user pool",
        )
        CfnOutput(
            self,
            "CognitoUserPoolClientId",
            value=self.cognito_client.user_pool_client_id,
            description="Client ID used by the GCO CLI for SRP auth",
        )

    # ==================================================================
    # Presigned-URL Lambda
    # ==================================================================

    def _create_presigned_url_lambda(self) -> None:
        """Create the ``Presigned_URL_Lambda`` that mints Studio login URLs.

        Wired into API Gateway's ``/studio/login`` route from
        ``GCOApiGatewayGlobalStack``. The function lives on
        ``GCOAnalyticsStack`` (not the API gateway stack) so its IAM role
        can reference ``SageMaker_Execution_Role.role_arn`` on ``PassRole``
        and ``Studio_EFS.file_system_arn`` on the EFS access-point actions
        without a cross-stack import.

        Key configuration:

        * Runtime: ``LAMBDA_PYTHON_RUNTIME`` from ``gco.stacks.constants``.
        * Timeout: 29 s — API Gateway's maximum integration timeout is 29
          seconds, so matching it here lets the Lambda time out *before*
          API Gateway does, producing a clean HTTP 500 with our opaque
          error token rather than API Gateway's 504.
        * Tracing: ``ACTIVE`` so X-Ray captures the
          ``sagemaker:CreatePresignedDomainUrl`` call.
        * Log group retention: 1 month.

        IAM scoping:

        * ``sagemaker:ListDomains`` — no resource-level scoping available;
          scoped with a documented ``Resource::*`` nag suppression.
        * ``sagemaker:DescribeDomain`` + ``CreatePresignedDomainUrl`` +
          ``DescribeUserProfile`` + ``CreateUserProfile`` + ``ListTags`` +
          ``AddTags`` scoped to the domain and user-profile ARN families
          in this region+account. We cannot pin the ``DomainId`` at synth
          time because ``list_domains`` runs at invoke time, so the ARN
          shape includes a wildcard segment covering "any domain id".
        * ``iam:PassRole`` on ``SageMaker_Execution_Role.role_arn`` with a
          ``StringEquals iam:PassedToService=sagemaker.amazonaws.com``
          condition so the role can only ever be handed to SageMaker.
        * ``elasticfilesystem:DescribeAccessPoints`` +
          ``CreateAccessPoint`` on ``Studio_EFS.file_system_arn`` for the
          lazy per-user access-point creation path in the handler.
        * ``AWSLambdaBasicExecutionRole`` managed policy for the CloudWatch
          Logs + X-Ray write path.
        """
        from cdk_nag import NagSuppressions

        # Dedicated IAM role — narrow-scoped, no reuse across other
        # Lambdas. We attach the basic execution role as a managed policy
        # so the nag rule for ``AwsSolutions-IAM4`` is happy; everything
        # else is an inline policy we own entirely.
        self.presigned_url_lambda_role = iam.Role(
            self,
            "PresignedUrlLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=(
                "Execution role for the analytics presigned-URL Lambda. "
                "Scoped to SageMaker domain + user-profile operations, "
                "PassRole on SageMaker_Execution_Role, and EFS access-"
                "point management on Studio_EFS."
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # ListDomains does not support resource-level scoping (AWS API
        # constraint). We use Resource::* and document the effective
        # blast radius in the nag suppression below — one list call per
        # invocation against the region's SageMaker control plane.
        self.presigned_url_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sagemaker:ListDomains"],
                resources=["*"],
            )
        )

        # Domain + user-profile actions. At synth time we don't know the
        # DomainId (list_domains is an invoke-time call), so the ARN
        # wildcards cover "any domain in this region+account" and "any
        # user profile under any domain in this region+account". The
        # account is still pinned, so the blast radius is bounded to
        # this account's SageMaker Studio installation.
        domain_arn_prefix = f"arn:aws:sagemaker:{self.region}:{self.account}:domain/*"
        user_profile_arn_prefix = f"arn:aws:sagemaker:{self.region}:{self.account}:user-profile/*/*"
        self.presigned_url_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "sagemaker:DescribeDomain",
                    "sagemaker:CreatePresignedDomainUrl",
                    "sagemaker:DescribeUserProfile",
                    "sagemaker:CreateUserProfile",
                    "sagemaker:ListTags",
                    "sagemaker:AddTags",
                ],
                resources=[domain_arn_prefix, user_profile_arn_prefix],
            )
        )

        # iam:PassRole — only SageMaker_Execution_Role, only to
        # sagemaker.amazonaws.com. This is what CreateUserProfile passes
        # on the ``ExecutionRole`` field.
        self.presigned_url_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[self.sagemaker_execution_role.role_arn],
                conditions={
                    "StringEquals": {
                        "iam:PassedToService": "sagemaker.amazonaws.com",
                    }
                },
            )
        )

        # EFS access-point management — scoped to the Studio_EFS file
        # system. The Lambda creates one access point per Cognito user
        # at first login (lazy-in-Lambda approach).
        self.presigned_url_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "elasticfilesystem:DescribeAccessPoints",
                    "elasticfilesystem:CreateAccessPoint",
                ],
                resources=[self.studio_efs.file_system_arn],
            )
        )

        # CloudWatch log group with 1-month retention. We own
        # the group explicitly (rather than letting Lambda auto-create
        # one) so the retention setting is captured in the template.
        presigned_url_log_group = logs.LogGroup(
            self,
            "PresignedUrlLambdaLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.presigned_url_lambda = lambda_.Function(
            self,
            "PresignedUrlFunction",
            runtime=getattr(lambda_.Runtime, LAMBDA_PYTHON_RUNTIME),
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda/analytics-presigned-url"),
            role=self.presigned_url_lambda_role,
            timeout=Duration.seconds(29),
            memory_size=256,
            tracing=lambda_.Tracing.ACTIVE,
            log_group=presigned_url_log_group,
            description=(
                "Exchanges a Cognito-authorized event for a presigned "
                "SageMaker Studio URL. Wired into /studio/login by "
                "GCOApiGatewayGlobalStack."
            ),
            environment={
                "STUDIO_DOMAIN_NAME": f"gco-analytics-{self.region}",
                "SAGEMAKER_EXECUTION_ROLE_ARN": self.sagemaker_execution_role.role_arn,
                "STUDIO_EFS_ID": self.studio_efs.file_system_id,
                "URL_EXPIRES_SECONDS": "300",
                "SESSION_EXPIRES_SECONDS": "43200",
            },
        )

        CfnOutput(
            self,
            "PresignedUrlLambdaArn",
            value=self.presigned_url_lambda.function_arn,
            description=(
                "ARN of the presigned-URL Lambda - consumed by the API "
                "Gateway stack's /studio/login integration."
            ),
        )

        # Nag suppressions. Each one carries a literal-ARN or documented
        # wildcard ``applies_to`` and a ``reason`` string explaining why
        # tighter scoping isn't possible.
        NagSuppressions.add_resource_suppressions(
            self.presigned_url_lambda_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "sagemaker:ListDomains does not support resource-"
                        "level scoping — the AWS API only accepts "
                        "Resource: *. Effective blast radius: a single "
                        "paginated list call per Lambda invocation "
                        "against this account's SageMaker control plane "
                        "in this region. The remaining SageMaker actions "
                        "(DescribeDomain, CreatePresignedDomainUrl, "
                        "DescribeUserProfile, CreateUserProfile, "
                        "ListTags, AddTags) are scoped to the literal "
                        "arn:aws:sagemaker:<region>:<account>:domain/* "
                        "and arn:aws:sagemaker:<region>:<account>:"
                        "user-profile/*/* ARN families, which is the "
                        "tightest we can achieve at synth time because "
                        "DomainId is only resolvable at invoke time."
                    ),
                    "appliesTo": [
                        "Resource::*",
                        ("Resource::arn:aws:sagemaker:<AWS::Region>:" "<AWS::AccountId>:domain/*"),
                        (
                            "Resource::arn:aws:sagemaker:<AWS::Region>:"
                            "<AWS::AccountId>:user-profile/*/*"
                        ),
                    ],
                },
            ],
            apply_to_children=True,
        )

    # ==================================================================
    # Nag suppressions
    # ==================================================================

    def _apply_nag_suppressions(self) -> None:
        """Dispatch to the analytics branch in ``gco/stacks/nag_suppressions.py``.

        The analytics branch calls ``add_sagemaker_suppressions``,
        ``add_cognito_suppressions``, ``add_emr_serverless_suppressions``,
        ``add_storage_suppressions`` (for ``Studio_Only_Bucket`` + access-
        logs bucket), ``add_lambda_suppressions`` (for the presigned-URL
        Lambda provider framework), and ``add_iam_suppressions`` (for
        cross-region SSM reads + CDK custom resources).
        """
        apply_all_suppressions(
            self,
            stack_type="analytics",
            regions=None,
            global_region=self.config.get_global_region(),
            api_gateway_region=self.config.get_api_gateway_region(),
        )
