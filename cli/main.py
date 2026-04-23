"""
GCO CLI - Main entry point.

A comprehensive CLI for managing GCO multi-region EKS clusters.

Commands:
    gco stacks deploy-all -y                    # Deploy all infrastructure
    gco jobs submit-sqs job.yaml -r us-east-1   # Submit job via SQS (recommended)
    gco jobs submit job.yaml -n gco-jobs        # Submit job via API Gateway
    gco jobs list --all-regions                 # List jobs across regions
    gco capacity check -t g4dn.xlarge           # Check GPU capacity
    gco inference deploy my-llm -i ...          # Deploy inference endpoint
    gco stacks destroy-all -y                   # Tear down everything

Full reference: docs/CLI.md
"""

import click

from . import __version__
from .commands import (
    capacity,
    config_cmd,
    costs,
    dag,
    files,
    inference,
    jobs,
    models,
    nodepools,
    queue,
    stacks,
    templates,
    webhooks,
)
from .config import GCOConfig, get_config


@click.group()
@click.version_option(version=__version__, prog_name="gco")
@click.option("--config", "-c", "config_file", help="Path to config file")
@click.option("--region", "-r", "default_region", help="Default AWS region")
@click.option(
    "--output",
    "-o",
    "output_format",
    type=click.Choice(["table", "json", "yaml"]),
    default="table",
    help="Output format",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.option(
    "--regional-api",
    is_flag=True,
    envvar="GCO_REGIONAL_API",
    help="Use regional API endpoints (for private access when public is disabled)",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config_file: str | None,
    default_region: str | None,
    output_format: str | None,
    verbose: bool,
    regional_api: bool,
) -> None:
    """GCO CLI - Manage multi-region EKS clusters for AI/ML workloads."""
    config = get_config()

    if config_file:
        config = GCOConfig.from_file(config_file)
    if default_region:
        config.default_region = default_region
    if output_format:
        config.output_format = output_format
    if verbose:
        config.verbose = verbose

    # Store regional_api flag in config for use by aws_client
    config.use_regional_api = regional_api

    ctx.obj = config


# Register command groups
cli.add_command(jobs)
cli.add_command(dag)
cli.add_command(queue)
cli.add_command(templates)
cli.add_command(webhooks)
cli.add_command(capacity)
cli.add_command(inference)
cli.add_command(models)
cli.add_command(nodepools)
cli.add_command(costs)
cli.add_command(stacks)
cli.add_command(files)
cli.add_command(config_cmd)


def main() -> None:
    """Main entry point for the CLI."""
    cli(obj=None)


if __name__ == "__main__":
    main()
