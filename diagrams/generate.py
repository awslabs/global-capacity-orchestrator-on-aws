#!/usr/bin/env python3
"""
Generate infrastructure diagrams for GCO using AWS PDK cdk-graph.

This script synthesizes the CDK app and generates architecture diagrams
for each stack type (global, api-gateway, regional, regional-api, monitoring) as well
as a combined full architecture diagram.

Usage:
    python diagrams/generate.py              # Generate all diagrams
    python diagrams/generate.py --stack all  # Generate all diagrams
    python diagrams/generate.py --stack global
    python diagrams/generate.py --stack api-gateway
    python diagrams/generate.py --stack regional
    python diagrams/generate.py --stack regional-api
    python diagrams/generate.py --stack monitoring
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import aws_cdk as cdk
from aws_pdk.cdk_graph import CdkGraph, FilterPreset
from aws_pdk.cdk_graph_plugin_diagram import (
    CdkGraphDiagramPlugin,
    DiagramFormat,
)

from gco.config.config_loader import ConfigLoader
from gco.stacks.analytics_stack import GCOAnalyticsStack
from gco.stacks.api_gateway_global_stack import GCOApiGatewayGlobalStack
from gco.stacks.global_stack import GCOGlobalStack
from gco.stacks.monitoring_stack import GCOMonitoringStack
from gco.stacks.regional_api_gateway_stack import GCORegionalApiGatewayStack
from gco.stacks.regional_stack import GCORegionalStack


def generate_global_stack_diagram(output_dir: Path) -> None:
    """Generate diagram for the Global Stack (Global Accelerator)."""
    print("\n📊 Generating Global Stack diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app = cdk.App(outdir=str(tmp_path / "cdk.out"))
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]

        GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
            description="Global resources including AWS Global Accelerator",
        )

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "global-stack",
                            "title": "GCO Global Stack - AWS Global Accelerator",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "global")


def generate_api_gateway_stack_diagram(output_dir: Path) -> None:
    """Generate diagram for the API Gateway Stack."""
    print("\n📊 Generating API Gateway Stack diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app = cdk.App(outdir=str(tmp_path / "cdk.out"))
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]
        api_gateway_region = deployment_regions["api_gateway"]

        # Need global stack for dependency
        global_stack = GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
        )

        api_gateway_stack = GCOApiGatewayGlobalStack(
            app,
            f"{project_name}-api-gateway",
            global_accelerator_dns=global_stack.accelerator.dns_name,
            env=cdk.Environment(region=api_gateway_region),
            description="Global API Gateway with IAM authentication",
        )
        api_gateway_stack.add_dependency(global_stack)

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "api-gateway-stack",
                            "title": "GCO API Gateway Stack",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "api-gateway")


def generate_regional_stack_diagram(output_dir: Path) -> None:
    """Generate diagram for the Regional Stack (EKS, ALB, etc.)."""
    print("\n📊 Generating Regional Stack diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app = cdk.App(outdir=str(tmp_path / "cdk.out"))
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]
        api_gateway_region = deployment_regions["api_gateway"]
        regional_regions = deployment_regions["regional"]

        # Need dependencies
        global_stack = GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
        )

        api_gateway_stack = GCOApiGatewayGlobalStack(
            app,
            f"{project_name}-api-gateway",
            global_accelerator_dns=global_stack.accelerator.dns_name,
            env=cdk.Environment(region=api_gateway_region),
        )
        api_gateway_stack.add_dependency(global_stack)

        # Create regional stack for first region
        region = regional_regions[0]
        regional_stack = GCORegionalStack(
            app,
            f"{project_name}-{region}",
            config=config,
            region=region,
            auth_secret_arn=api_gateway_stack.secret.secret_arn,
            env=cdk.Environment(region=region),
            description=f"Regional resources for {region} - EKS, ALB, Services",
        )
        regional_stack.add_dependency(global_stack)
        regional_stack.add_dependency(api_gateway_stack)

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "regional-stack",
                            "title": f"GCO Regional Stack ({region})",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "regional")


def generate_regional_api_stack_diagram(output_dir: Path) -> None:
    """Generate diagram for the Regional API Gateway Stack (Private Access)."""
    print("\n📊 Generating Regional API Gateway Stack diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app = cdk.App(outdir=str(tmp_path / "cdk.out"))
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]
        api_gateway_region = deployment_regions["api_gateway"]
        regional_regions = deployment_regions["regional"]

        # Need dependencies
        global_stack = GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
        )

        api_gateway_stack = GCOApiGatewayGlobalStack(
            app,
            f"{project_name}-api-gateway",
            global_accelerator_dns=global_stack.accelerator.dns_name,
            env=cdk.Environment(region=api_gateway_region),
        )
        api_gateway_stack.add_dependency(global_stack)

        # Create regional stack for first region
        region = regional_regions[0]
        regional_stack = GCORegionalStack(
            app,
            f"{project_name}-{region}",
            config=config,
            region=region,
            auth_secret_arn=api_gateway_stack.secret.secret_arn,
            env=cdk.Environment(region=region),
            description=f"Regional resources for {region}",
        )
        regional_stack.add_dependency(global_stack)
        regional_stack.add_dependency(api_gateway_stack)

        # Create regional API gateway stack
        # Note: ALB DNS name is a placeholder since it's created by EKS ingress controller
        regional_api_stack = GCORegionalApiGatewayStack(
            app,
            f"{project_name}-regional-api-{region}",
            config=config,
            region=region,
            vpc=regional_stack.vpc,
            alb_dns_name=f"internal-{project_name}-{region}.elb.amazonaws.com",  # Placeholder
            auth_secret_arn=api_gateway_stack.secret.secret_arn,
            env=cdk.Environment(region=region),
            description=f"Regional API Gateway for {region} (private access)",
        )
        regional_api_stack.add_dependency(regional_stack)

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "regional-api-stack",
                            "title": f"GCO Regional API Gateway Stack ({region})",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "regional-api")


def generate_monitoring_stack_diagram(output_dir: Path) -> None:
    """Generate diagram for the Monitoring Stack."""
    print("\n📊 Generating Monitoring Stack diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app = cdk.App(outdir=str(tmp_path / "cdk.out"))
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]
        api_gateway_region = deployment_regions["api_gateway"]
        monitoring_region = deployment_regions["monitoring"]
        regional_regions = deployment_regions["regional"]

        # Need all dependencies
        global_stack = GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
        )

        api_gateway_stack = GCOApiGatewayGlobalStack(
            app,
            f"{project_name}-api-gateway",
            global_accelerator_dns=global_stack.accelerator.dns_name,
            env=cdk.Environment(region=api_gateway_region),
        )
        api_gateway_stack.add_dependency(global_stack)

        regional_stacks = []
        for region in regional_regions:
            regional_stack = GCORegionalStack(
                app,
                f"{project_name}-{region}",
                config=config,
                region=region,
                auth_secret_arn=api_gateway_stack.secret.secret_arn,
                env=cdk.Environment(region=region),
            )
            regional_stack.add_dependency(global_stack)
            regional_stack.add_dependency(api_gateway_stack)
            regional_stacks.append(regional_stack)

        monitoring_stack = GCOMonitoringStack(
            app,
            f"{project_name}-monitoring",
            config=config,
            global_stack=global_stack,
            regional_stacks=regional_stacks,
            api_gateway_stack=api_gateway_stack,
            env=cdk.Environment(region=monitoring_region),
            description="Cross-region monitoring and observability",
        )
        for rs in regional_stacks:
            monitoring_stack.add_dependency(rs)

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "monitoring-stack",
                            "title": "GCO Monitoring Stack",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "monitoring")


def generate_analytics_stack_diagram(output_dir: Path) -> None:
    """Generate diagram for the Analytics Stack (SageMaker Studio, EMR, Cognito)."""
    print("\n📊 Generating Analytics Stack diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Force-enable the analytics toggle via a CDK context override so
        # ``ConfigLoader.get_analytics_enabled()`` returns True during this
        # synth. Mirrors the overlay the property tests use in
        # ``tests/_analytics_cdk_overlays.build_overlay``.
        app = cdk.App(
            context={
                "analytics_environment": {
                    "enabled": True,
                    "hyperpod": {"enabled": False},
                    "cognito": {"domain_prefix": None, "removal_policy": "destroy"},
                    "efs": {"removal_policy": "destroy"},
                    "studio": {"user_profile_name_prefix": None},
                },
            },
            outdir=str(tmp_path / "cdk.out"),
        )
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]
        api_gateway_region = deployment_regions["api_gateway"]

        # GCOGlobalStack owns the Cluster_Shared_Bucket SSM parameters that
        # the analytics stack reads via a cross-region AwsCustomResource, so
        # the analytics diagram needs the global stack wired up.
        global_stack = GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
        )

        api_gateway_stack = GCOApiGatewayGlobalStack(
            app,
            f"{project_name}-api-gateway",
            global_accelerator_dns=global_stack.accelerator.dns_name,
            env=cdk.Environment(region=api_gateway_region),
        )
        api_gateway_stack.add_dependency(global_stack)

        analytics_stack = GCOAnalyticsStack(
            app,
            f"{project_name}-analytics",
            config=config,
            env=cdk.Environment(region=api_gateway_region),
            description="Optional ML and analytics environment (SageMaker Studio, EMR Serverless, Cognito)",
        )
        analytics_stack.add_dependency(global_stack)

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "analytics-stack",
                            "title": "GCO Analytics Stack - SageMaker Studio + EMR + Cognito",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "analytics")


def generate_full_architecture_diagram(output_dir: Path) -> None:
    """Generate diagram for the complete architecture."""
    print("\n📊 Generating Full Architecture diagram...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app = cdk.App(outdir=str(tmp_path / "cdk.out"))
        config = ConfigLoader(app)

        project_name = config.get_project_name()
        deployment_regions = config.get_deployment_regions()
        global_region = deployment_regions["global"]
        api_gateway_region = deployment_regions["api_gateway"]
        monitoring_region = deployment_regions["monitoring"]
        regional_regions = deployment_regions["regional"]

        global_stack = GCOGlobalStack(
            app,
            f"{project_name}-global",
            config=config,
            env=cdk.Environment(region=global_region),
        )

        api_gateway_stack = GCOApiGatewayGlobalStack(
            app,
            f"{project_name}-api-gateway",
            global_accelerator_dns=global_stack.accelerator.dns_name,
            env=cdk.Environment(region=api_gateway_region),
        )
        api_gateway_stack.add_dependency(global_stack)

        regional_stacks = []
        for region in regional_regions:
            regional_stack = GCORegionalStack(
                app,
                f"{project_name}-{region}",
                config=config,
                region=region,
                auth_secret_arn=api_gateway_stack.secret.secret_arn,
                env=cdk.Environment(region=region),
            )
            regional_stack.add_dependency(global_stack)
            regional_stack.add_dependency(api_gateway_stack)
            regional_stacks.append(regional_stack)

        monitoring_stack = GCOMonitoringStack(
            app,
            f"{project_name}-monitoring",
            config=config,
            global_stack=global_stack,
            regional_stacks=regional_stacks,
            api_gateway_stack=api_gateway_stack,
            env=cdk.Environment(region=monitoring_region),
        )
        for rs in regional_stacks:
            monitoring_stack.add_dependency(rs)

        graph = CdkGraph(
            app,
            plugins=[
                CdkGraphDiagramPlugin(
                    defaults={"format": [DiagramFormat.PNG, DiagramFormat.SVG]},
                    diagrams=[
                        {
                            "name": "full-architecture",
                            "title": "GCO Complete Infrastructure Architecture",
                            "filter_plan": {"preset": FilterPreset.COMPACT},
                        },
                        {
                            "name": "full-architecture-detailed",
                            "title": "GCO Detailed Architecture",
                            "filter_plan": {"preset": FilterPreset.NONE},
                            "theme": "dark",
                        },
                    ],
                )
            ],
        )

        app.synth()
        graph.report()
        _copy_diagrams_from_temp(tmp_path, output_dir, "full")


def _copy_diagrams_from_temp(tmp_dir: Path, output_dir: Path, prefix: str) -> None:
    """Copy generated diagrams from temp cdk.out to output directory."""
    cdkgraph_dir = tmp_dir / "cdk.out" / "cdkgraph"
    if cdkgraph_dir.exists():
        for f in cdkgraph_dir.glob("*.png"):
            dest_name = f.name if prefix == "full" else f"{prefix}-stack.png"
            shutil.copy(f, output_dir / dest_name)
            print(f"   ✓ Created {dest_name}")
        for f in cdkgraph_dir.glob("*.svg"):
            dest_name = f.name if prefix == "full" else f"{prefix}-stack.svg"
            shutil.copy(f, output_dir / dest_name)
            print(f"   ✓ Created {dest_name}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate GCO infrastructure diagrams")
    parser.add_argument(
        "--stack",
        choices=[
            "all",
            "global",
            "api-gateway",
            "regional",
            "regional-api",
            "monitoring",
            "analytics",
        ],
        default="all",
        help="Which stack diagram to generate (default: all)",
    )
    args = parser.parse_args()

    output_dir = Path(__file__).parent

    print("🏗️  GCO Infrastructure Diagram Generator")
    print("=" * 50)

    if args.stack in ("all", "global"):
        generate_global_stack_diagram(output_dir)

    if args.stack in ("all", "api-gateway"):
        generate_api_gateway_stack_diagram(output_dir)

    if args.stack in ("all", "regional"):
        generate_regional_stack_diagram(output_dir)

    if args.stack in ("all", "regional-api"):
        generate_regional_api_stack_diagram(output_dir)

    if args.stack in ("all", "monitoring"):
        generate_monitoring_stack_diagram(output_dir)

    if args.stack in ("all", "analytics"):
        generate_analytics_stack_diagram(output_dir)

    if args.stack == "all":
        generate_full_architecture_diagram(output_dir)

    print("\n" + "=" * 50)
    print("✅ Diagram generation complete!")
    print(f"   Output directory: {output_dir.absolute()}")
    print("\n   Generated files:")
    for f in sorted(output_dir.glob("*.png")):
        print(f"   - {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
    for f in sorted(output_dir.glob("*.svg")):
        print(f"   - {f.name} ({f.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
