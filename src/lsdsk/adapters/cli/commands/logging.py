"""Logging demonstration CLI command.

Provides a command to preview log output, one line per severity, on the same
guarded console every other command logs through.

Contents:
    * :class:`LogDemoTheme` - The console themes a preview can be asked for.
    * :func:`cli_logdemo` - Run a logging demonstration.
"""

from __future__ import annotations

from enum import StrEnum

import rich_click as click

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..typed_click import option


class LogDemoTheme(StrEnum):
    """A console theme the preview can be asked for.

    The set is closed: lib_log_rich publishes exactly these in
    ``lib_log_rich.domain.palettes.CONSOLE_STYLE_THEMES``, and an unknown name is
    refused by that library with a message about a dict key rather than by the
    command line with the list of choices. Named here so click validates it like every other
    mode this CLI takes, and so a reader sees the options in ``--help``.

    The names are not invented here, so they cannot be renamed here either:
    ``test_the_theme_enum_matches_what_the_library_publishes`` fails if the
    library's set and this one drift apart.

    Example:
        >>> LogDemoTheme.CLASSIC.value
        'classic'
    """

    CLASSIC = "classic"
    DARK = "dark"
    NEON = "neon"
    PASTEL = "pastel"


@click.command("logdemo", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--theme",
    type=click.Choice(LogDemoTheme, case_sensitive=False),
    default=LogDemoTheme.CLASSIC.value,
    show_default=True,
    help="Logging theme to preview",
)
def cli_logdemo(theme: LogDemoTheme) -> None:
    """Run a logging demonstration to preview log output."""
    import lib_log_rich.runtime  # noqa: PLC0415 - deferred: imported only when the demo is invoked

    # Local for an import cycle: the logging adapter imports safe_console from
    # this package, whose __init__ imports this module.
    from lsdsk.adapters.logging.setup import run_log_demo  # noqa: PLC0415 - import cycle, see above

    # One runtime at a time: the demo builds its own, so the run's goes first.
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.shutdown()

    shown = run_log_demo(str(theme))
    safe_console.echo(f"\nLog demo completed (theme: {shown})")


__all__ = ["LogDemoTheme", "cli_logdemo"]
