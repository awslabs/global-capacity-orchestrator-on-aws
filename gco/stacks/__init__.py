"""
CDK stacks for GCO (Global Capacity Orchestrator on AWS).

This package contains AWS CDK stack definitions for deploying GCO infrastructure:

- GCOGlobalStack: Global Accelerator and cross-region resources
- GCOApiGatewayGlobalStack: Centralized IAM-authenticated API Gateway
- GCORegionalStack: Per-region EKS cluster, ALB, and services
- GCOMonitoringStack: Cross-region CloudWatch dashboards and alarms

Deployment Order:
1. GCOGlobalStack (creates Global Accelerator)
2. GCOApiGatewayGlobalStack (creates API Gateway, depends on GA DNS)
3. GCORegionalStack (creates EKS, ALB; depends on both global stacks)
4. GCOMonitoringStack (creates dashboards; depends on all regional stacks)

Usage:
    cdk deploy --all                    # Deploy all stacks
    cdk deploy gco-us-east-1        # Deploy single region
"""

from .api_gateway_global_stack import GCOApiGatewayGlobalStack
from .global_stack import GCOGlobalStack
from .monitoring_stack import GCOMonitoringStack
from .regional_stack import GCORegionalStack

__all__ = [
    "GCOApiGatewayGlobalStack",
    "GCOGlobalStack",
    "GCOMonitoringStack",
    "GCORegionalStack",
]
