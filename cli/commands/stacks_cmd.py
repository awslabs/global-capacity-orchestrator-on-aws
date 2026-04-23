"""Stack deployment and management commands."""

import sys
from typing import Any

import click

from ..config import GCOConfig
from ..output import get_output_formatter

pass_config = click.make_pass_decorator(GCOConfig, ensure=True)


@click.group()
@pass_config
def stacks(config: Any) -> None:
    """Deploy and manage GCO CDK stacks."""
    pass


@stacks.command("list")
@click.option("--refresh", is_flag=True, help="Force refresh from AWS")
@pass_config
def list_stacks(config: Any, refresh: Any) -> None:
    """List all GCO stacks (local CDK and deployed)."""
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    try:
        manager = get_stack_manager(config)
        local_stacks = manager.list_stacks()

        formatter.print_info("Available CDK stacks:")
        for stack in local_stacks:
            print(f"  - {stack}")

    except Exception as e:
        formatter.print_error(f"Failed to list stacks: {e}")
        sys.exit(1)


@stacks.command("synth")
@click.argument("stack_name", required=False)
@click.option("--quiet", "-q", is_flag=True, default=True, help="Quiet output")
@pass_config
def synth_stack(config: Any, stack_name: Any, quiet: Any) -> None:
    """Synthesize CloudFormation templates."""
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    try:
        manager = get_stack_manager(config)
        output = manager.synth(stack_name, quiet=quiet)
        if output:
            print(output)
        formatter.print_success("CDK synthesis completed")
    except Exception as e:
        formatter.print_error(f"CDK synth failed: {e}")
        sys.exit(1)


@stacks.command("diff")
@click.argument("stack_name", required=False)
@pass_config
def diff_stack(config: Any, stack_name: Any) -> None:
    """Show differences between deployed and local stacks."""
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    try:
        manager = get_stack_manager(config)
        diff_output = manager.diff(stack_name)
        if diff_output:
            print(diff_output)
        else:
            formatter.print_success("No differences found")
    except Exception as e:
        formatter.print_error(f"CDK diff failed: {e}")
        sys.exit(1)


@stacks.command("deploy")
@click.argument("stack_name")
@click.option("--yes", "-y", is_flag=True, help="Skip approval prompts")
@click.option("--outputs-file", "-o", help="Write outputs to file")
@click.option("--tag", "-t", multiple=True, help="Add tags (key=value)")
@pass_config
def deploy_stack(config: Any, stack_name: Any, yes: Any, outputs_file: Any, tag: Any) -> None:
    """Deploy a single CDK stack to AWS.

    For deploying all stacks in the correct order, use 'deploy-all'.

    Examples:
        gco stacks deploy gco-us-east-1
        gco stacks deploy gco-global -y
        gco stacks deploy gco-us-east-1 -t Environment=prod
    """
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    # Parse tags
    tags = {}
    for t in tag:
        if "=" in t:
            k, v = t.split("=", 1)
            tags[k] = v

    try:
        manager = get_stack_manager(config)

        formatter.print_info(f"Deploying {stack_name}...")

        success = manager.deploy(
            stack_name=stack_name,
            require_approval=not yes,
            outputs_file=outputs_file,
            tags=tags if tags else None,
        )

        if success:
            formatter.print_success("Deployment completed successfully")
        else:
            formatter.print_error("Deployment failed")
            sys.exit(1)

    except Exception as e:
        formatter.print_error(f"Deployment failed: {e}")
        sys.exit(1)


@stacks.command("destroy")
@click.argument("stack_name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@pass_config
def destroy_stack(config: Any, stack_name: Any, yes: Any) -> None:
    """Destroy a single CDK stack.

    For destroying all stacks in the correct order, use 'destroy-all'.

    Examples:
        gco stacks destroy gco-us-east-1
        gco stacks destroy gco-us-east-1 -y
    """
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    if not yes:
        click.confirm(f"Are you sure you want to destroy {stack_name}?", abort=True)

    try:
        manager = get_stack_manager(config)

        formatter.print_info(f"Destroying {stack_name}...")

        success = manager.destroy(
            stack_name=stack_name,
            force=yes,
        )

        if success:
            formatter.print_success(f"Stack {stack_name} destroyed successfully")
        else:
            formatter.print_error("Destroy failed")
            sys.exit(1)

    except Exception as e:
        formatter.print_error(f"Destroy failed: {e}")
        sys.exit(1)


@stacks.command("deploy-all")
@click.option("--yes", "-y", is_flag=True, help="Skip approval prompts")
@click.option("--outputs-file", "-o", help="Write outputs to file")
@click.option("--tag", "-t", multiple=True, help="Add tags (key=value)")
@click.option("--parallel", "-p", is_flag=True, help="Deploy regional stacks in parallel")
@click.option("--max-workers", "-w", default=4, help="Max parallel deployments (default: 4)")
@pass_config
def deploy_all_orchestrated(
    config: Any, yes: Any, outputs_file: Any, tag: Any, parallel: Any, max_workers: Any
) -> None:
    """Deploy all stacks in the correct order.

    Deploys in three phases:
    1. Global stacks (gco-global, gco-api-gateway)
    2. Regional stacks (gco-us-east-1, etc.) - can be parallelized
    3. Monitoring stack (gco-monitoring) - depends on regional stacks

    Use --parallel to deploy regional stacks concurrently, which can
    significantly reduce total deployment time when deploying to
    multiple regions.

    Examples:
        gco stacks deploy-all -y
        gco stacks deploy-all -y --parallel
        gco stacks deploy-all -y -p --max-workers 8
        gco stacks deploy-all -y -t Environment=prod
    """
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    # Parse tags
    tags = {}
    for t in tag:
        if "=" in t:
            k, v = t.split("=", 1)
            tags[k] = v

    try:
        manager = get_stack_manager(config)
        stacks = manager.list_stacks()

        formatter.print_info(f"Found {len(stacks)} stacks to deploy")
        if parallel:
            formatter.print_info(f"Parallel mode enabled (max workers: {max_workers})")

        def on_start(stack_name: str) -> None:
            formatter.print_info(f"Deploying {stack_name}...")

        def on_complete(stack_name: str, success: bool) -> None:
            if success:
                formatter.print_success(f"  ✓ {stack_name} deployed")
            else:
                formatter.print_error(f"  ✗ {stack_name} failed")

        success, successful, failed = manager.deploy_orchestrated(
            require_approval=not yes,
            outputs_file=outputs_file,
            tags=tags if tags else None,
            on_stack_start=on_start,
            on_stack_complete=on_complete,
            parallel=parallel,
            max_workers=max_workers,
        )

        formatter.print_info("")
        formatter.print_info(f"Deployed: {len(successful)}/{len(stacks)} stacks")

        if success:
            formatter.print_success("All stacks deployed successfully")
        else:
            formatter.print_error(f"Deployment failed. Failed stacks: {', '.join(failed)}")
            sys.exit(1)

    except Exception as e:
        formatter.print_error(f"Deployment failed: {e}")
        sys.exit(1)


@stacks.command("destroy-all")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.option("--parallel", "-p", is_flag=True, help="Destroy regional stacks in parallel")
@click.option("--max-workers", "-w", default=4, help="Max parallel destructions (default: 4)")
@pass_config
def destroy_all_orchestrated(config: Any, yes: Any, parallel: Any, max_workers: Any) -> None:
    """Destroy all stacks in the correct order.

    Destroys in three phases:
    1. Monitoring stack (gco-monitoring)
    2. Regional stacks (gco-us-east-1, etc.) - can be parallelized
    3. Global stacks (gco-api-gateway, gco-global)

    Automatically retries up to 3 times (with 30s waits) if any stacks fail,
    which handles transient issues like orphaned resources during teardown.

    Use --parallel to destroy regional stacks concurrently, which can
    significantly reduce total teardown time when destroying multiple
    regional stacks.

    Examples:
        gco stacks destroy-all -y
        gco stacks destroy-all -y --parallel
        gco stacks destroy-all -y -p --max-workers 8
    """
    import time

    from ..stacks import get_stack_destroy_order, get_stack_manager

    formatter = get_output_formatter(config)
    # Retry up to 3 times total. CloudFormation stack deletions can fail
    # transiently — e.g., EKS leaves behind a cluster security group that
    # blocks VPC deletion, but it gets cleaned up async. A 30-second wait
    # between attempts is usually enough for the orphaned resources to clear.
    max_attempts = 3

    try:
        manager = get_stack_manager(config)
        stacks = manager.list_stacks()
        ordered = get_stack_destroy_order(stacks)

        if not yes:
            formatter.print_warning("This will destroy ALL GCO stacks:")
            for stack in ordered:
                formatter.print_info(f"  - {stack}")
            click.confirm("\nAre you sure you want to destroy all stacks?", abort=True)

        total_stacks = len(stacks)

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                # Clean up EKS-managed security groups between retries.
                # After the first attempt, the EKS cluster is deleted but its
                # security group (eks-cluster-sg-*) may linger and block VPC deletion.
                formatter.print_info("Cleaning up orphaned EKS resources...")
                manager.cleanup_eks_security_groups()
                formatter.print_warning(
                    f"Attempt {attempt}/{max_attempts}: waiting 30 seconds before retrying..."
                )
                time.sleep(30)

            formatter.print_info(f"Destroying {len(stacks)} stacks...")
            if parallel:
                formatter.print_info(f"Parallel mode enabled (max workers: {max_workers})")

            def on_start(stack_name: str) -> None:
                formatter.print_info(f"Destroying {stack_name}...")

            def on_complete(stack_name: str, success: bool) -> None:
                if success:
                    formatter.print_success(f"  ✓ {stack_name} destroyed")
                else:
                    formatter.print_error(f"  ✗ {stack_name} failed")

            success, successful, failed = manager.destroy_orchestrated(
                force=True,
                on_stack_start=on_start,
                on_stack_complete=on_complete,
                parallel=parallel,
                max_workers=max_workers,
            )

            if success:
                break

            if attempt < max_attempts:
                formatter.print_warning(f"{len(failed)} stack(s) failed: {', '.join(failed)}")

        formatter.print_info("")
        formatter.print_info(f"Destroyed: {total_stacks - len(failed)}/{total_stacks} stacks")

        if success:
            formatter.print_success("All stacks destroyed successfully")
        else:
            formatter.print_error(f"Some stacks failed to destroy: {', '.join(failed)}")
            sys.exit(1)

    except Exception as e:
        formatter.print_error(f"Destroy failed: {e}")
        sys.exit(1)


@stacks.command("bootstrap")
@click.option("--account", "-a", help="AWS account ID")
@click.option("--region", "-r", required=True, help="AWS region")
@pass_config
def bootstrap_cdk(config: Any, account: Any, region: Any) -> None:
    """Bootstrap CDK in an AWS account/region.

    This is required before deploying stacks to a new account/region.

    Example:
        gco stacks bootstrap --region us-east-1
        gco stacks bootstrap -a 123456789012 -r eu-west-1
    """
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    try:
        manager = get_stack_manager(config)
        formatter.print_info(f"Bootstrapping CDK in {region}...")

        success = manager.bootstrap(account=account, region=region)

        if success:
            formatter.print_success(f"CDK bootstrapped in {region}")
        else:
            formatter.print_error("Bootstrap failed")
            sys.exit(1)

    except Exception as e:
        formatter.print_error(f"Bootstrap failed: {e}")
        sys.exit(1)


@stacks.command("status")
@click.argument("stack_name")
@click.option("--region", "-r", required=True, help="AWS region")
@pass_config
def stack_status(config: Any, stack_name: Any, region: Any) -> None:
    """Get detailed status of a deployed stack."""
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    try:
        manager = get_stack_manager(config)
        status = manager.get_stack_status(stack_name, region)

        if status:
            formatter.print(status.to_dict())
        else:
            formatter.print_error(f"Stack {stack_name} not found in {region}")
            sys.exit(1)

    except Exception as e:
        formatter.print_error(f"Failed to get stack status: {e}")
        sys.exit(1)


@stacks.command("outputs")
@click.argument("stack_name")
@click.option("--region", "-r", required=True, help="AWS region")
@pass_config
def stack_outputs(config: Any, stack_name: Any, region: Any) -> None:
    """Get outputs from a deployed stack."""
    from ..stacks import get_stack_manager

    formatter = get_output_formatter(config)

    try:
        manager = get_stack_manager(config)
        outputs = manager.get_outputs(stack_name, region)

        if outputs:
            formatter.print(outputs)
        else:
            formatter.print_warning(f"No outputs found for {stack_name}")

    except Exception as e:
        formatter.print_error(f"Failed to get outputs: {e}")
        sys.exit(1)


@stacks.command("access")
@click.option("--cluster", "-c", help="Cluster name (default: gco-{region})")
@click.option("--region", "-r", help="AWS region (default: first deployment region)")
@pass_config
def setup_access(config: Any, cluster: Any, region: Any) -> None:
    """Configure kubectl access to a GCO EKS cluster.

    Updates kubeconfig, creates an EKS access entry for your IAM principal,
    and associates the cluster admin policy. Handles assumed roles automatically.

    Examples:
        gco stacks access
        gco stacks access -r us-west-2
        gco stacks access -c my-cluster -r eu-west-1
    """
    import subprocess

    from ..config import _load_cdk_json

    formatter = get_output_formatter(config)

    # Determine region
    if not region:
        cdk_regions = _load_cdk_json()
        if cdk_regions and "regional" in cdk_regions:
            region = cdk_regions["regional"][0]
        else:
            region = config.default_region or "us-east-1"

    # Determine cluster name
    if not cluster:
        cluster = f"gco-{region}"

    formatter.print_info(f"Setting up access to cluster: {cluster} in region: {region}")

    try:
        # Step 1: Update kubeconfig
        formatter.print_info("Updating kubeconfig...")
        subprocess.run(
            ["aws", "eks", "update-kubeconfig", "--name", cluster, "--region", region],
            check=True,
            capture_output=True,
            text=True,
        )

        # Step 2: Get IAM principal
        formatter.print_info("Getting your IAM principal...")
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
            check=True,
            capture_output=True,
            text=True,
        )
        principal_arn = result.stdout.strip()
        formatter.print_info(f"Principal: {principal_arn}")

        # Handle assumed roles — extract the role ARN from the assumed-role ARN
        if ":assumed-role/" in principal_arn:
            import re

            role_name = re.search(r":assumed-role/([^/]+)/", principal_arn)
            if role_name:
                account_result = subprocess.run(
                    [
                        "aws",
                        "sts",
                        "get-caller-identity",
                        "--query",
                        "Account",
                        "--output",
                        "text",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                account_id = account_result.stdout.strip()
                principal_arn = f"arn:aws:iam::{account_id}:role/{role_name.group(1)}"
                formatter.print_info(f"Using role ARN: {principal_arn}")

        # Step 3: Create access entry
        formatter.print_info("Creating EKS access entry...")
        try:
            subprocess.run(
                [
                    "aws",
                    "eks",
                    "create-access-entry",
                    "--cluster-name",
                    cluster,
                    "--region",
                    region,
                    "--principal-arn",
                    principal_arn,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            formatter.print_info("Access entry may already exist")

        # Step 4: Associate admin policy
        formatter.print_info("Associating cluster admin policy...")
        try:
            subprocess.run(
                [
                    "aws",
                    "eks",
                    "associate-access-policy",
                    "--cluster-name",
                    cluster,
                    "--region",
                    region,
                    "--principal-arn",
                    principal_arn,
                    "--policy-arn",
                    "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
                    "--access-scope",
                    "type=cluster",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            formatter.print_info("Policy may already be associated")

        # Step 5: Verify access
        formatter.print_info("Waiting for permissions to propagate...")
        import time

        time.sleep(10)

        result = subprocess.run(
            ["kubectl", "get", "nodes", "--request-timeout=10s"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            node_count = len(
                [line for line in result.stdout.strip().split("\n")[1:] if line.strip()]
            )
            print(result.stdout)
            formatter.print_info(f"Access configured successfully. {node_count} node(s) ready.")
        else:
            formatter.print_warning(
                "kubectl connected but no nodes found (cluster may be scaling to zero)"
            )

    except subprocess.CalledProcessError as e:
        formatter.print_error(f"Command failed: {e.stderr or e.stdout or str(e)}")
        sys.exit(1)
    except FileNotFoundError as e:
        formatter.print_error(f"Required tool not found: {e}")
        sys.exit(1)
    except Exception as e:
        formatter.print_error(f"Failed to set up access: {e}")
        sys.exit(1)


@stacks.group("fsx")
@pass_config
def fsx_cmd(config: Any) -> None:
    """Manage FSx for Lustre configuration."""
    pass


@fsx_cmd.command("status")
@click.option("--region", "-r", help="Show config for specific region")
@pass_config
def fsx_status(config: Any, region: Any) -> None:
    """Show current FSx for Lustre configuration status."""
    from ..stacks import get_fsx_config

    formatter = get_output_formatter(config)

    try:
        fsx_config = get_fsx_config(region)
        if region:
            formatter.print_info(f"FSx config for region: {region}")
        else:
            formatter.print_info("Global FSx config:")
        formatter.print(fsx_config)
    except Exception as e:
        formatter.print_error(f"Failed to get FSx config: {e}")
        sys.exit(1)


@fsx_cmd.command("enable")
@click.option("--region", "-r", help="Enable FSx for specific region only")
@click.option("--storage-capacity", "-s", default=1200, help="Storage capacity in GiB (min 1200)")
@click.option(
    "--deployment-type",
    "-d",
    type=click.Choice(["SCRATCH_1", "SCRATCH_2", "PERSISTENT_1", "PERSISTENT_2"]),
    default="SCRATCH_2",
    help="FSx deployment type",
)
@click.option("--throughput", "-t", default=200, help="Per-unit storage throughput (MB/s)")
@click.option("--compression", "-c", type=click.Choice(["LZ4", "NONE"]), default="LZ4")
@click.option("--import-path", help="S3 path for data import (s3://bucket/prefix)")
@click.option("--export-path", help="S3 path for data export (s3://bucket/prefix)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@pass_config
def fsx_enable(
    config: Any,
    region: Any,
    storage_capacity: Any,
    deployment_type: Any,
    throughput: Any,
    compression: Any,
    import_path: Any,
    export_path: Any,
    yes: Any,
) -> None:
    """Enable FSx for Lustre in the stack configuration.

    FSx for Lustre provides high-performance parallel file system storage
    ideal for ML training workloads requiring high throughput and low latency.

    Examples:
        gco stacks fsx enable
        gco stacks fsx enable --region us-east-1
        gco stacks fsx enable --storage-capacity 2400 --deployment-type PERSISTENT_2
        gco stacks fsx enable -r us-west-2 --import-path s3://my-bucket/training-data
    """
    from ..stacks import update_fsx_config

    formatter = get_output_formatter(config)

    if storage_capacity < 1200:
        formatter.print_error("Storage capacity must be at least 1200 GiB")
        sys.exit(1)

    scope = f"region {region}" if region else "all regions (global)"

    if not yes:
        formatter.print_info(f"FSx for Lustre configuration for {scope}:")
        formatter.print_info(f"  Storage Capacity: {storage_capacity} GiB")
        formatter.print_info(f"  Deployment Type: {deployment_type}")
        formatter.print_info(f"  Throughput: {throughput} MB/s per TiB")
        formatter.print_info(f"  Compression: {compression}")
        if import_path:
            formatter.print_info(f"  Import Path: {import_path}")
        if export_path:
            formatter.print_info(f"  Export Path: {export_path}")
        click.confirm(f"\nEnable FSx for Lustre for {scope}?", abort=True)

    try:
        fsx_settings = {
            "enabled": True,
            "storage_capacity_gib": storage_capacity,
            "deployment_type": deployment_type,
            "per_unit_storage_throughput": throughput,
            "data_compression_type": compression,
            "import_path": import_path,
            "export_path": export_path,
            "auto_import_policy": "NEW_CHANGED_DELETED" if import_path else None,
        }

        update_fsx_config(fsx_settings, region)
        formatter.print_success(f"FSx for Lustre enabled in cdk.json for {scope}")
        if region:
            formatter.print_info(f"Run 'gco stacks deploy gco-{region}' to apply changes")
        else:
            formatter.print_info("Run 'gco stacks deploy' to apply changes")

    except Exception as e:
        formatter.print_error(f"Failed to enable FSx: {e}")
        sys.exit(1)


@fsx_cmd.command("disable")
@click.option("--region", "-r", help="Disable FSx for specific region only")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@pass_config
def fsx_disable(config: Any, region: Any, yes: Any) -> None:
    """Disable FSx for Lustre in the stack configuration.

    Note: This only updates the configuration. Run 'gco stacks deploy'
    to apply changes. Existing FSx file systems will be deleted.

    Examples:
        gco stacks fsx disable
        gco stacks fsx disable --region us-east-1
    """
    from ..stacks import update_fsx_config

    formatter = get_output_formatter(config)

    scope = f"region {region}" if region else "all regions (global)"

    if not yes:
        formatter.print_warning(f"This will disable FSx for Lustre for {scope}.")
        formatter.print_warning("Existing FSx file systems will be deleted on next deploy.")
        click.confirm("Are you sure?", abort=True)

    try:
        update_fsx_config({"enabled": False}, region)
        formatter.print_success(f"FSx for Lustre disabled in cdk.json for {scope}")
        if region:
            formatter.print_info(f"Run 'gco stacks deploy gco-{region}' to apply changes")
        else:
            formatter.print_info("Run 'gco stacks deploy' to apply changes")

    except Exception as e:
        formatter.print_error(f"Failed to disable FSx: {e}")
        sys.exit(1)
