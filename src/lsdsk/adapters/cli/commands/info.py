"""Basic CLI commands: resolved metadata, and a deliberate failure testing.

Provides simple commands that demonstrate success and failure paths.

Contents:
    * :func:`cli_info` - Display package metadata.
    * :func:`cli_fail` - Trigger intentional failure for testing.
"""

from __future__ import annotations

import logging

import lib_log_rich.runtime
import rich_click as click

from lsdsk import __init__conf__
from lsdsk.domain.enums import OutputFormat

from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import get_cli_context
from ..envelope import emit_action
from ..typed_click import option

logger = logging.getLogger(__name__)


@click.command("info", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--format",
    "output_format",
    type=click.Choice([choice.value for choice in OutputFormat], case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)
@click.pass_context
def cli_info(ctx: click.Context, output_format: str) -> None:
    """Print resolved metadata so users can inspect installation details."""
    with lib_log_rich.runtime.bind(job_id="cli-info", extra={"command": "info"}):
        logger.info("Displaying package information")
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            # The same fields the human form prints, so a caller asking which
            # version is installed does not have to parse a padded table.
            emit_action(
                "info",
                {
                    "name": __init__conf__.name,
                    "title": __init__conf__.title,
                    "version": __init__conf__.version,
                    "homepage": __init__conf__.homepage,
                    "author": __init__conf__.author,
                    "author_email": __init__conf__.author_email,
                    "shell_command": __init__conf__.shell_command,
                },
            )
        else:
            # Through the services container rather than the module, so a test
            # can substitute it at the seam instead of patching this project's
            # own internals - the last CLI-reachable behaviour that needed it.
            get_cli_context(ctx).services.print_info()


@click.command("fail", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_fail() -> None:
    """Trigger the intentional failure helper to test error handling."""
    with lib_log_rich.runtime.bind(job_id="cli-fail", extra={"command": "fail"}):
        logger.warning("Executing intentional failure command")
        raise RuntimeError("I should fail")


__all__ = ["cli_fail", "cli_info"]
