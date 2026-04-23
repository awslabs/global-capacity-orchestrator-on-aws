"""
Configuration management for GCO (Global Capacity Orchestrator on AWS).

This package provides configuration loading and validation from CDK context.
Configuration is defined in cdk.json and accessed via the ConfigLoader class.

Usage:
    from gco.config import ConfigLoader

    config = ConfigLoader(app)
    regions = config.get_regions()
    cluster_config = config.get_cluster_config("us-east-1")
"""

from .config_loader import ConfigLoader, ConfigValidationError

__all__ = ["ConfigLoader", "ConfigValidationError"]
