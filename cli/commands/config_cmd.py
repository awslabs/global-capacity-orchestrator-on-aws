"""Configuration commands."""

from typing import Any

import click

from ..config import GCOConfig
from ..output import get_output_formatter

pass_config = click.make_pass_decorator(GCOConfig, ensure=True)


@click.group(name="config-cmd")
@pass_config
def config_cmd(config: Any) -> None:
    """Manage CLI configuration."""
    pass


@config_cmd.command("show")
@pass_config
def show_config(config: Any) -> None:
    """Show current configuration."""
    formatter = get_output_formatter(config)
    formatter.print(config.to_dict())


@config_cmd.command("init")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing config")
@pass_config
def init_config(config: Any, force: Any) -> None:
    """Initialize configuration file."""
    from pathlib import Path

    config_path = Path.home() / ".gco" / "config.yaml"

    if config_path.exists() and not force:
        click.confirm(f"Config file exists at {config_path}. Overwrite?", abort=True)

    config.save(str(config_path))
    click.echo(f"Configuration saved to {config_path}")
