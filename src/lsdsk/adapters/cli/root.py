"""Root CLI command group and global option handling.

Defines the top-level Click command group that serves as the entry point for
all subcommands. Handles global flags like --traceback, --profile, and --set.

Contents:
    * :func:`cli` - Root command group with global options.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import rich_click as click

from lsdsk import __init__conf__
from lsdsk.adapters.config.overrides import apply_overrides

from .constants import CLICK_CONTEXT_SETTINGS
from .context import CLIContext, apply_traceback_preferences, store_cli_context
from .typed_click import option, version_option

if TYPE_CHECKING:
    from lib_layered_config import Config

    from lsdsk.composition import AppServices


def _apply_cli_overrides(config: Config, set_overrides: tuple[str, ...]) -> Config:
    """Apply ``--set`` overrides to a Config, raising UsageError on failure.

    Args:
        config: Base configuration loaded from file/env layers.
        set_overrides: Raw ``SECTION.KEY=VALUE`` strings from the CLI.

    Returns:
        New Config with overrides applied, or original if none given.

    Raises:
        click.UsageError: If any override string is malformed or targets
            a non-dict section/intermediate.
    """
    try:
        return apply_overrides(config, set_overrides)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


@click.group(
    help=__init__conf__.title,
    context_settings=CLICK_CONTEXT_SETTINGS,
    invoke_without_command=True,
)
@version_option(
    version=__init__conf__.version,
    prog_name=__init__conf__.shell_command,
    message=f"{__init__conf__.shell_command} version {__init__conf__.version}",
)
@option(
    "--traceback/--no-traceback",
    is_flag=True,
    default=False,
    help="Show full Python traceback on errors",
)
@option(
    "--profile",
    type=str,
    default=None,
    help="Load configuration from a named profile (e.g., 'production', 'test')",
)
@option(
    "--set",
    "set_overrides",
    multiple=True,
    default=(),
    metavar="SECTION.KEY=VALUE",
    help="Override a configuration setting (repeatable).",
)
@option(
    "--replay",
    "replay",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Render a snapshot captured earlier instead of reading this machine.",
)
@option(
    "--history-file",
    "history_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Read and write counter history here instead of the per-user state file.",
)
@option(
    "--no-record",
    "no_record",
    is_flag=True,
    default=False,
    help="Judge counters against recorded history without adding this reading to it.",
)
@option(
    "--env-file",
    "env_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
    default=None,
    help="Explicit .env file path (skips upward directory search).",
)
@click.pass_context
def cli(
    ctx: click.Context,
    # Click always passes option values by keyword, so making them keyword-only
    # changes nothing at the call site and keeps the signature within the
    # positional-argument limit.
    *,
    traceback: bool,
    profile: str | None,
    set_overrides: tuple[str, ...],
    replay: Path | None,
    history_file: Path | None,
    no_record: bool,
    env_file: str | None,
) -> None:
    """Root command storing global flags and syncing shared traceback state.

    Loads configuration once with the profile, applies any ``--set`` overrides,
    and stores it in the Click context for all subcommands to access. Mirrors
    the traceback flag into ``lib_cli_exit_tools.config`` so downstream helpers
    observe the preference.

    Example:
        >>> from click.testing import CliRunner
        >>> runner = CliRunner()
        >>> result = runner.invoke(cli, ["info"])
        >>> result.exit_code
        0

    Note that a doctest in a Click-decorated docstring is never collected: the
    docstring belongs to the Command object rather than to a function, so
    pytest's scanner does not reach it. The behaviour above is covered for real
    in ``tests/test_cli_core.py``.
    """
    # ctx.obj is always the services factory (production or test)
    if not callable(ctx.obj):
        raise RuntimeError("Services factory not provided. This is a bug.")
    # cast, not a type: ignore - Click types ``obj`` as Any, and this project
    # closes such gaps with a cast to the real type (see typed_click.py).
    services = cast("AppServices", ctx.obj())
    config = services.get_config(profile=profile, dotenv_path=env_file)
    config = _apply_cli_overrides(config, set_overrides)
    services.init_logging(config)
    store_cli_context(
        ctx,
        CLIContext(
            traceback=traceback,
            config=config,
            services=services,
            profile=profile,
            set_overrides=set_overrides,
            replay=replay,
            history_file=history_file,
            no_record=no_record,
        ),
    )
    apply_traceback_preferences(traceback)

    if ctx.invoked_subcommand is not None:
        return

    # Bare `lsdsk` still answers "what is wrong here" without being asked for
    # a section, but it does it interactively on a terminal: the whole
    # machine on one page is more than a reader takes in at once. Off a
    # terminal the page is printed exactly as before, so every pipe,
    # redirect and CI log is unchanged. See run_default_view.
    from .commands.scan import (  # noqa: PLC0415 - deferred, same cycle as _register_commands
        resolve_history,
        resolve_tunables,
        run_default_view,
    )

    # The tunables have to be resolved here, exactly as every subcommand
    # does. Without them the default view fell back to the shipped defaults
    # for both sections, so a configured threshold or layout value was
    # honoured by `lsdsk findings` and silently ignored by bare `lsdsk` -
    # the view that exists to be the one you run when you do not yet know
    # which section to ask for.
    thresholds, display = resolve_tunables(ctx)
    run_default_view(replay, settings=resolve_history(ctx), thresholds=thresholds, display=display)


# Deferred import required to break a circular dependency: this module defines
# the ``cli`` group, commands register themselves onto it, and those command
# modules import from package ancestors. This is the standard Click pattern.
def _register_commands() -> None:
    from .commands import (  # noqa: PLC0415 - deferred: breaks the root<->commands circular import (see above)
        cli_config,
        cli_config_deploy,
        cli_config_generate_examples,
        cli_controllers,
        cli_disks,
        cli_fail,
        cli_findings,
        cli_health,
        cli_info,
        cli_logdemo,
        cli_record,
        cli_report,
        cli_slots,
        cli_smart,
        cli_snapshot,
        cli_topology,
        cli_trend,
        cli_tui,
    )

    for cmd in (
        cli_report,
        cli_topology,
        cli_controllers,
        cli_disks,
        cli_health,
        cli_findings,
        cli_record,
        cli_slots,
        cli_smart,
        cli_snapshot,
        cli_trend,
        cli_tui,
        cli_info,
        cli_fail,
        cli_config,
        cli_config_deploy,
        cli_config_generate_examples,
        cli_logdemo,
    ):
        cli.add_command(cmd)


_register_commands()


__all__ = ["cli"]
