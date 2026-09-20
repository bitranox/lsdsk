"""Root CLI command group and global option handling.

Defines the top-level Click command group that serves as the entry point for
all subcommands. Handles global flags like --traceback, --profile, and --set.

Contents:
    * :func:`cli` - Root command group with global options.
    * :func:`_report_profile_that_named_nothing` - warn about a profile nothing answered to.
    * :func:`_report_keys_nothing_reads` - warn about a file key nothing reads.
    * :func:`_report_values_nothing_uses` - warn about a value nothing can use.
    * :func:`_print_version` - the version line, written through the guarded sink.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import rich_click as click
from lib_layered_config import ConfigError

from lsdsk import __init__conf__
from lsdsk.adapters.config.history import read_history_settings
from lsdsk.adapters.config.known_keys import nearest_known_key, unknown_owned_keys
from lsdsk.adapters.config.overrides import apply_overrides
from lsdsk.adapters.config.profiles import contributed_layers, existing_profiles, nearest_profile
from lsdsk.adapters.config.tunables import read_display_settings, read_thresholds
from lsdsk.domain.enums import TreeDensity

from . import safe_console
from .constants import CLICK_CONTEXT_SETTINGS, TREE_DENSITY_TOKENS
from .context import CLIContext, apply_traceback_preferences, store_cli_context
from .exit_codes import ExitCode
from .typed_click import option

if TYPE_CHECKING:
    from lib_layered_config import Config

    from lsdsk.composition import AppServices


def _print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Write the version line through the guarded sink and stop.

    Our own printer rather than click's ``version_option``, which writes through
    click's own echo: when that meets a reader that has gone, ``cli.main()`` catches
    the ``EPIPE`` itself (``click/core.py``, ``except OSError`` on ``errno.EPIPE``),
    swaps both streams for a ``_PacifyFlushWrapper`` and calls ``sys.exit(1)``. It
    does that REGARDLESS of ``standalone_mode``, so it fires before anything here is
    reached, and 1 is this tool's code for an actionable finding - measured, with
    nothing on either stream to say otherwise. Writing it ourselves keeps the
    failure where :func:`safe_console.echo` can answer it with 141.

    The text is byte-identical to what click produced, pinned by
    ``test_the_version_line_is_exactly_what_it_has_always_been``.

    Args:
        ctx: The Click context, used to stop once the version is printed.
        param: The option Click is processing. Unused; part of the callback shape.
        value: Whether the flag was given.

    Side Effects:
        Writes one line to stdout and ends the run.
    """
    if not value or ctx.resilient_parsing:
        return
    safe_console.echo(f"{__init__conf__.shell_command} version {__init__conf__.version}")
    ctx.exit()


def _report_keys_nothing_reads(config: Config) -> None:
    """Say so on stderr when a config FILE carries a key this tool does not read.

    Warned about rather than refused, unlike the same typo in ``--set``. A
    ``--set`` is typed for one run and has one consumer, so an unknown key there
    is always a mistake; a config file is durable and shared with the libraries
    that read the same namespace, so refusing would make it brittle. The value
    is inert either way, and the whole point is that an inert value must not
    read like an applied one.

    On stderr in both output modes, so a parsed stdout stays exactly what a
    caller expects.

    Args:
        config: The configuration as the file and environment layers left it,
            before any ``--set`` is merged in - those are refused rather than
            warned about, so they never reach here.

    Side Effects:
        Writes a line per unknown key to stderr.
    """
    for dotted in unknown_owned_keys(config.as_dict()):
        section, _, key = dotted.partition(".")
        suggestion = nearest_known_key(section, key)
        meant = f" Did you mean {section}.{suggestion}?" if suggestion else ""
        safe_console.echo(f"Warning: ignoring {dotted}: [{section}] has no such key.{meant}", err=True)


def _load_or_refuse(services: AppServices, *, profile: str | None, env_file: str | None) -> Config:
    """Load the configuration, or refuse as a CONFIGURATION error and say so.

    A configuration file this tool cannot parse used to escape as the layered
    library's own exception. It reached the top-level handler, which printed
    ``LayerLoadError: Invalid TOML in ...`` - the one refusal in the whole
    program without ``Error:`` in front of it - and left the code that means
    lsdsk itself broke. What broke is the file the reader wrote.

    ``78`` is ``EX_CONFIG``, and this is the one failure in the tool that is
    literally a configuration error; a malformed CAPTURE already gets it. Only
    the library's own error type is caught, so a bug here still reaches the
    handler that honours ``--traceback``.

    The message goes to stderr in prose rather than through the envelope,
    because the failure happens in the root group: the subcommand's
    ``--format`` has not been parsed yet, so there is no format to answer in.

    Args:
        services: The wired adapters, whose loader is called.
        profile: The profile asked for on the command line.
        env_file: An explicit ``.env`` path, or ``None``.

    Returns:
        The merged configuration.

    Raises:
        SystemExit: With ``CONFIG_ERROR`` when the configuration cannot load.
    """
    try:
        return services.get_config(profile=profile, dotenv_path=env_file)
    except ConfigError as error:
        safe_console.echo(f"Error: {error}", err=True)
        raise SystemExit(ExitCode.CONFIG_ERROR) from None


def _report_profile_that_named_nothing(config: Config, profile: str | None) -> None:
    """Say so on stderr when ``--profile`` loaded nothing under that name.

    A profile REPLACES the configuration directories rather than adding to
    them, so a name with one letter wrong reads no file at all and every value
    falls back to the shipped one at exit 0. Every other identifier typed at
    this CLI answers back - an unknown ``--set`` key is refused, an unknown file
    key gets a did-you-mean, an invalid profile SYNTAX exits 22 - and this was
    the one well-formed name allowed to mean nothing quietly.

    Warned rather than refused, for the same reason an unknown file key is: the
    values are inert either way and the run still diagnoses the hardware, which
    is what somebody is at the terminal for.

    Args:
        config: The configuration as loaded, carrying which files it came from.
        profile: The name that was asked for, or ``None`` when none was.

    Side Effects:
        Writes one line to stderr when the name answered nothing.
    """
    if profile is None or contributed_layers(config, profile):
        return
    suggestion = nearest_profile(profile, existing_profiles())
    meant = f" Did you mean {suggestion}?" if suggestion else ""
    safe_console.echo(
        f"Warning: profile {profile} named nothing, so every value is the one configured without it.{meant}",
        err=True,
    )


def _report_values_nothing_uses(config: Config, *, history_file: Path | None) -> None:
    """Say so on stderr when a configured VALUE is not one this tool can use.

    The key half of this is :func:`_report_keys_nothing_reads`, and the reasoning is
    the same: the value is inert, and an inert value must not read like an applied
    one. ``lsdsk config`` reports the refused text back as the value in force, so
    without this line an operator gets positive confirmation of a setting that
    decided nothing.

    Warned about rather than refused on BOTH surfaces, unlike an unknown ``--set``
    key. The fallback is deliberate and documented: a malformed threshold must never
    stop somebody diagnosing a failing drive, so the run goes on with the shipped
    figure and says which one it used.

    Read once here rather than where the values are used, because
    ``resolve_tunables`` is called by the root group and again by every view that
    draws - a warning emitted there would repeat itself a different number of times
    per subcommand.

    Args:
        config: The configuration with any ``--set`` already merged in, so a value
            refused from the command line is reported alongside one from a file.
        history_file: What ``--history-file`` asked for, so a refused
            ``history.path`` names the location that really ends up in force.

    Side Effects:
        Writes a line per refused value to stderr.
    """
    for rejected in (
        *read_thresholds(config).rejected,
        *read_display_settings(config).rejected,
        *read_history_settings(config, path_override=history_file).rejected,
    ):
        safe_console.echo(rejected.as_sentence(), err=True)


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
@option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the version and exit.",
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
    "--expand-virtual",
    "expand_virtual",
    is_flag=True,
    default=False,
    help="List every kernel-virtual device instead of tallying them in one line.",
)
@option(
    "--tree-density",
    "tree_density",
    type=click.Choice(TREE_DENSITY_TOKENS, case_sensitive=False),
    default=None,
    help="How much of the PCI fabric the topology shows.",
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
    expand_virtual: bool,
    tree_density: str | None,
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
    config = _load_or_refuse(services, profile=profile, env_file=env_file)
    _report_profile_that_named_nothing(config, profile)
    _report_keys_nothing_reads(config)
    config = _apply_cli_overrides(config, set_overrides)
    # After the overrides, not before: an unknown --set KEY is refused outright and
    # never reaches here, but a --set VALUE falls back exactly as a file's does, so
    # both surfaces have to be read once the two are merged.
    _report_values_nothing_uses(config, history_file=history_file)
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
            expand_virtual=expand_virtual,
            tree_density=None if tree_density is None else TreeDensity(tree_density.casefold()),
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
