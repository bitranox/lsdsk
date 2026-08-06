"""CLI command implementations.

Collects all subcommand functions and re-exports them for registration
with the root CLI group.

Contents:
    * Info commands from :mod:`.info`
    * Config commands from :mod:`.config`
    * Logging commands from :mod:`.logging`
    * Storage commands from :mod:`.scan`
"""

from __future__ import annotations

from .config import cli_config, cli_config_deploy, cli_config_generate_examples
from .history import cli_record, cli_trend
from .info import cli_fail, cli_info
from .logging import cli_logdemo
from .scan import (
    cli_controllers,
    cli_disks,
    cli_findings,
    cli_health,
    cli_slots,
    cli_smart,
    cli_snapshot,
    cli_topology,
    cli_tui,
)

__all__ = [
    "cli_config",
    "cli_config_deploy",
    "cli_config_generate_examples",
    "cli_controllers",
    "cli_disks",
    "cli_fail",
    "cli_findings",
    "cli_health",
    "cli_info",
    "cli_logdemo",
    "cli_record",
    "cli_slots",
    "cli_smart",
    "cli_snapshot",
    "cli_topology",
    "cli_trend",
    "cli_tui",
]
