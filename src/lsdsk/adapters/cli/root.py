"""Root CLI command group and global option handling.

Defines the top-level Click command group that serves as the entry point for
all subcommands. Handles global flags like --traceback, --profile, and --set.

Contents:
    * :func:`cli` - Root command group with global options.
    * :func:`_refuse_an_ambiguous_mode` - refuse an int permission mode that could be octal digits.
    * :func:`_print_version` - the version line, written through the guarded sink.
    * :class:`_RootGroup` - the group's class, keeping the subcommand's arguments for the callback.
    * :func:`_read_subcommand_request` - what ``config``/``config-deploy`` ask of the configuration.

``config`` and ``config-deploy`` repeat ``--profile``, and ``config-deploy --force`` rewrites
files the configuration is read from. The group loads the configuration before click has parsed a
subcommand, so it reads those few options itself, from the subcommand's own arguments with the
subcommand's own parser, and loads once: loading the configuration without the subcommand's
profile first made the two spellings of one request answer apart. Nothing in that read decides
anything about the values: an invalid profile name is refused as it always was, and a command line
click will refuse is read as asking for nothing, because the run stops at click's refusal anyway.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast

import rich_click as click

from lsdsk import __init__conf__
from lsdsk.adapters.config.loader import (
    DamagedInstallationError,
    invalid_profile_message,
    require_an_intact_installation,
)
from lsdsk.adapters.config.overrides import apply_overrides
from lsdsk.domain.enums import DeployTarget, TreeDensity

from . import safe_console
from .config_loading import (
    UnusableConfigError,
    load_profile_config,
    refuse_an_ambiguous_mode,
    report_keys_nothing_reads,
    report_profile_that_named_nothing,
    report_values_nothing_uses,
)
from .constants import CLICK_CONTEXT_SETTINGS, TREE_DENSITY_TOKENS
from .context import CLIContext, apply_traceback_preferences, store_cli_context
from .envelope import RefusedBeforeTheRun
from .exit_codes import ExitCode
from .typed_click import NonEmptyPath, option

if TYPE_CHECKING:
    import os
    from typing import NoReturn

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


def _load_or_refuse(ctx: click.Context, services: AppServices, *, profile: str | None, env_file: str | None) -> Config:
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

    A profile whose NAME the library refuses is refused here too, as ``22``,
    before anything is read. Validated on its own rather than by catching what
    the loader raises, because the loader signals a bad name with a plain
    ``ValueError`` and catching that around the whole load would turn any bug
    inside it into a refusal with no traceback.

    Both refusals answer through :func:`_refuse_before_the_run`, in JSON where
    the subcommand's own arguments asked for it.

    Args:
        ctx: The root group's context, holding the subcommand and its arguments.
        services: The wired adapters, whose loader is called.
        profile: The profile asked for on the command line.
        env_file: An explicit ``.env`` path, or ``None``.

    Returns:
        The merged configuration.

    Raises:
        RefusedBeforeTheRun: With ``INVALID_ARGUMENT`` for a profile name the
            library refuses, ``PERMISSION_DENIED`` when a configuration file is
            unreadable for lack of permission, and ``CONFIG_ERROR`` when the
            configuration cannot load for any other reason or the package's
            own shipped configuration is damaged.
    """
    refusal = invalid_profile_message(profile)
    if refusal is not None:
        _refuse_before_the_run(ctx, refusal, ExitCode.INVALID_ARGUMENT)
    try:
        require_an_intact_installation()
    except DamagedInstallationError as error:
        _refuse_before_the_run(ctx, str(error), ExitCode.CONFIG_ERROR)
    # The mapping of a failed load to its code lives with the rest of what is decided about a
    # loaded configuration, in config_loading.
    try:
        return load_profile_config(services, profile=profile, env_file=env_file)
    except UnusableConfigError as refused:
        _refuse_before_the_run(ctx, refused.message, refused.code)


def _refuse_before_the_run(ctx: click.Context, message: str, code: ExitCode) -> NoReturn:
    """Refuse from the root group: the sentence now, the envelope at the entry point.

    The sentence goes to stderr here. The envelope cannot: click has taken the
    subcommand's arguments off this context before the group's callback runs, so
    whether JSON was asked for is only known where the command line is, and
    :class:`~.envelope.RefusedBeforeTheRun` carries the refusal there.

    Not :func:`~.envelope.fail`, which names the command from the CURRENT
    context - here the root group, where the envelope should name the
    subcommand that was about to run.

    Args:
        ctx: The root group's context.
        message: The refusal, as one sentence, with no ``Error:`` prefix.
        code: The exit code to leave with, whose name becomes the error type.

    Raises:
        RefusedBeforeTheRun: Always, with `code`.
    """
    safe_console.echo(f"Error: {message}", err=True)
    raise RefusedBeforeTheRun(message, code=code, command=ctx.invoked_subcommand) from None


def _refuse_an_ambiguous_mode(ctx: click.Context, config: Config) -> None:
    """Refuse the run when a permission mode is an int that could be octal digits.

    The decision is :func:`~.config_loading.refuse_an_ambiguous_mode`, shared with the
    subcommands that reload under their own ``--profile``; this answers it from the root.

    Args:
        ctx: The root group's context.
        config: The configuration with any ``--set`` already merged in.

    Raises:
        RefusedBeforeTheRun: With ``CONFIG_ERROR`` for such a mode.
    """
    try:
        refuse_an_ambiguous_mode(config)
    except UnusableConfigError as refused:
        _refuse_before_the_run(ctx, refused.message, refused.code)


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


#: Where :class:`_RootGroup` leaves the subcommand's arguments on the group's context.
_ARGUMENTS_KEY: Final = "lsdsk.subcommand_arguments"

#: The subcommands whose own ``--profile`` names the configuration the run uses.
_PROFILE_COMMANDS: Final = frozenset({"config", "config-deploy"})

#: The subcommand that rewrites configuration files.
_DEPLOY_COMMAND: Final = "config-deploy"


class _RootGroup(click.RichGroup):
    """The root group, which keeps the subcommand's arguments where its own callback can read them.

    Click takes those arguments off the group's context before the group's callback runs and
    hands them to the subcommand only afterwards, so without this the callback cannot know what
    the subcommand was asked for until the configuration is already loaded.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Parse the group's own options as click does, and keep what is left for the subcommand.

        Args:
            ctx: The group's context.
            args: The command line, without the program name.

        Returns:
            The subcommand's arguments, as click returns them.
        """
        rest = super().parse_args(ctx, args)
        ctx.meta[_ARGUMENTS_KEY] = tuple(rest)
        return rest


class _SubcommandRequest(NamedTuple):
    """The profile a subcommand names, and which targets a forced deploy overwrites.

    Attributes:
        profile: The subcommand's own ``--profile``, or ``None`` when it named none.
        overwrites: The targets ``config-deploy --force`` rewrites; empty for anything else.

    Example:
        >>> _SubcommandRequest()
        _SubcommandRequest(profile=None, overwrites=())
    """

    profile: str | None = None
    overwrites: tuple[DeployTarget, ...] = ()


def _asks_for_help(command: click.Command, ctx: click.Context, args: list[str]) -> bool:
    """Whether the arguments ask for the subcommand's help, which is not asking to use a profile.

    Read with the command's own parser rather than by looking for ``-h`` among the words,
    because ``--profile -h`` names a profile called ``-h``.
    """
    help_option = command.get_help_option(ctx)
    if help_option is None:
        return False
    found, _rest, _order = command.make_parser(ctx).parse_args(args=list(args))
    return help_option.name in found


def _words(stored: object) -> list[str]:
    """The arguments :class:`_RootGroup` kept, as a list; empty when it kept none."""
    if not isinstance(stored, tuple):
        return []
    return [word for word in cast("tuple[object, ...]", stored) if isinstance(word, str)]


def _overwrites(params: dict[str, Any]) -> tuple[DeployTarget, ...]:
    """The targets a deploy rewrites: those named, when ``--force`` was given."""
    targets: object = params.get("targets")
    if params.get("force") is not True or not isinstance(targets, tuple):
        return ()
    named = cast("tuple[object, ...]", targets)
    return tuple(target for target in named if isinstance(target, DeployTarget))


def _read_subcommand_request(ctx: click.Context) -> _SubcommandRequest:
    """Read what the subcommand about to run asks of the configuration.

    Parsed with the subcommand's own parser in click's resilient mode, so a command line click
    will refuse yields nothing rather than an error: the refusal comes, as always, when click
    parses the subcommand for real.

    Args:
        ctx: The root group's context, after :class:`_RootGroup` parsed the command line.

    Returns:
        The subcommand's profile and what a forced deploy overwrites; empty for a subcommand
        that names neither, and for a request for its help.
    """
    name = ctx.invoked_subcommand
    group = ctx.command
    if name not in _PROFILE_COMMANDS or not isinstance(group, click.Group):
        return _SubcommandRequest()
    command = group.get_command(ctx, name)
    if command is None:
        return _SubcommandRequest()
    args = _words(ctx.meta.get(_ARGUMENTS_KEY))
    sub_ctx = command.make_context(name, list(args), parent=ctx, resilient_parsing=True)
    if _asks_for_help(command, sub_ctx, args):
        return _SubcommandRequest()
    profile: object = sub_ctx.params.get("profile")
    return _SubcommandRequest(
        profile=profile if isinstance(profile, str) else None,
        overwrites=_overwrites(sub_ctx.params) if name == _DEPLOY_COMMAND else (),
    )


class _OptionalFile(NonEmptyPath):
    """A file path where the empty string means "not given".

    ``Path("")`` is the current directory, so an empty ``--history-file`` was
    refused as a directory that cannot be a store. CONFIG.md reads an empty
    ``history.path`` as "use the default"; the option reads its empty value the
    same way, so the two spellings of "nothing" agree.
    """

    def convert(self, value: str | os.PathLike[str], param: click.Parameter | None, ctx: click.Context | None) -> Any:
        """Answer ``None`` for an empty value and the converted path otherwise.

        Args:
            value: What the command line carried.
            param: The option being converted.
            ctx: The invocation.

        Returns:
            ``None`` for the empty string, else what :class:`click.Path` makes of `value`.
        """
        if value == "":
            return None
        return super().convert(value, param, ctx)


@click.group(
    cls=_RootGroup,
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
    type=NonEmptyPath(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Render a snapshot captured earlier instead of reading this machine.",
)
@option(
    "--history-file",
    "history_file",
    type=_OptionalFile(dir_okay=False, path_type=Path),
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
    type=NonEmptyPath(exists=True, file_okay=True, dir_okay=False, readable=True),
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
    and stores it in the Click context for all subcommands to access. The profile
    is the subcommand's own ``--profile`` where ``config`` or ``config-deploy`` names
    one, so the two spellings of one request load the same configuration. Mirrors
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
    # Applied before anything else runs, not after the configuration is loaded
    # and logging is started: a crash inside either of those used to escape with
    # --traceback given and print the same one-line summary a plain run would,
    # because lib_cli_exit_tools.config.traceback was not synced from the flag
    # yet. The two refusals that already print one line on purpose - a damaged
    # installation, a configuration file this tool cannot parse - raise
    # RefusedBeforeTheRun, a SystemExit the entry point answers without ever
    # calling the traceback printer, so moving this earlier does not add a
    # traceback to either of them.
    apply_traceback_preferences(traceback)
    # ctx.obj is always the services factory (production or test)
    if not callable(ctx.obj):
        raise RuntimeError("Services factory not provided. This is a bug.")
    # cast, not a type: ignore - Click types ``obj`` as Any, and this project
    # closes such gaps with a cast to the real type (see typed_click.py).
    services = cast("AppServices", ctx.obj())
    # The subcommand's own --profile wins, as it always did, and is read before anything is
    # loaded: loading the root's first answered for a configuration the run never used.
    named_by_subcommand = _read_subcommand_request(ctx).profile
    profile = named_by_subcommand if named_by_subcommand is not None else profile
    config = _load_or_refuse(ctx, services, profile=profile, env_file=env_file)
    report_profile_that_named_nothing(config, profile)
    report_keys_nothing_reads(config)
    config = _apply_cli_overrides(config, set_overrides)
    # After the overrides, not before: an unknown --set KEY is refused outright and
    # never reaches here, but a --set VALUE falls back exactly as a file's does, so
    # both surfaces have to be read once the two are merged.
    _refuse_an_ambiguous_mode(ctx, config)
    report_values_nothing_uses(config, history_file=history_file)
    services.init_logging(config)
    store_cli_context(
        ctx,
        CLIContext(
            traceback=traceback,
            config=config,
            services=services,
            profile=profile,
            env_file=env_file,
            set_overrides=set_overrides,
            replay=replay,
            history_file=history_file,
            no_record=no_record,
            expand_virtual=expand_virtual,
            tree_density=None if tree_density is None else TreeDensity(tree_density.casefold()),
        ),
    )
    # One run, one telling. The set that keeps `health` from printing the same
    # refused store twice is scoped to an invocation, and this callback is what
    # an invocation begins with - including the second `main([...])` an embedder
    # makes in one process, which was told nothing at all.
    from .commands.history import (  # noqa: PLC0415 - deferred, same cycle as _register_commands
        forget_announced_refusals,
    )

    forget_announced_refusals()

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
        cli_filesystems,
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
        cli_filesystems,
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
