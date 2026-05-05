"""
GCO CLI command groups.

Each module defines a Click command group that is registered
on the root ``cli`` group via ``cli.add_command()``.
"""

from .analytics_cmd import analytics
from .capacity_cmd import capacity
from .config_cmd import config_cmd
from .costs_cmd import costs
from .dag_cmd import dag
from .files_cmd import files
from .inference_cmd import inference
from .jobs_cmd import jobs
from .models_cmd import models
from .nodepools_cmd import nodepools
from .queue_cmd import queue
from .stacks_cmd import stacks
from .templates_cmd import templates
from .webhooks_cmd import webhooks

__all__ = [
    "analytics",
    "capacity",
    "config_cmd",
    "costs",
    "dag",
    "files",
    "inference",
    "jobs",
    "models",
    "nodepools",
    "queue",
    "stacks",
    "templates",
    "webhooks",
]
