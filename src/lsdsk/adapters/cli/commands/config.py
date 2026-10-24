"""Configuration display and deployment CLI commands.

Provides commands to inspect, deploy, and generate example configuration.

Contents:
    * :func:`cli_config` - Display merged configuration.
    * :func:`cli_config_deploy` - Deploy configuration to target locations.
    * :func:`cli_config_generate_examples` - Generate example configuration files.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NoReturn

import lib_log_rich.runtime
import rich_click as click
from lib_layered_config import Config, ConfigError, DeployModeError, redact_mapping

# Below the top-level exports because the library exports the parser only inside
# DeployMode.from_text, which also judges safety - the deploy's job, not the option's.
from lib_layered_config.domain.deploy_mode import parse_mode_text
from lib_layered_config.domain.permissions import modes_apply
from rich.console import Console

from lsdsk import __init__conf__
from lsdsk.adapters.config.deploy import paths_the_deploy_writes
from lsdsk.adapters.config.loader import read_config_skipping, shipped_table_paths
from lsdsk.adapters.config.permissions import (
    ModeTheDeployIgnores,
    SectionGivenAsAValueError,
    modes_the_deploy_ignores,
    permission_overrides_from,
    read_permission_defaults,
)
from lsdsk.adapters.config.secrets import redact_secrets
from lsdsk.adapters.config.values import REASON_SECTION, RejectedValue, rendered
from lsdsk.domain.deployment import DeployRequest
from lsdsk.domain.enums import ActionCommand, DeployTarget, OutputFormat, WriteOutcome
from lsdsk.domain.errors import ConfigurationError

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS, FORMAT_OPTION
from ..context import CLIContext, get_cli_context
from ..envelope import ActionResult, MappingResult, PayloadTooDeepError, emit_action, fail
from ..exit_codes import ExitCode
from ..typed_click import NonEmptyPath, option

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class DeployResult(ActionResult):
    """Which configuration files were written, and under which profile.

    An empty `deployed` is the documented outcome when the files already exist,
    not a failure; `outcome` says which of the two it was, and the envelope is
    `ok` either way.
    """

    outcome: WriteOutcome
    deployed: list[str]
    profile: str | None
    permissions_set: bool


class GenerateExamplesResult(ActionResult):
    """Which example files were scaffolded, and where; `outcome` says whether any were."""

    outcome: WriteOutcome
    generated: list[str]
    destination: str


def _outcome_of(written: list[Path]) -> WriteOutcome:
    """Whether a run wrote files, or found every one already in place.

    Args:
        written: The paths the run wrote.

    Returns:
        ``WRITTEN`` when there are any, otherwise ``ALREADY_PRESENT``.
    """
    return WriteOutcome.WRITTEN if written else WriteOutcome.ALREADY_PRESENT


@click.command("config", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--format",
    "output_format",
    type=click.Choice(OutputFormat, case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    help="Output format (human-readable or JSON)",
)
@option(
    "--section",
    type=str,
    default=None,
    help="Show only a specific configuration section (e.g., 'lib_log_rich')",
)
@option(
    "--profile",
    type=str,
    default=None,
    help="Override profile from root command (e.g., 'production', 'test')",
)
@click.pass_context
def cli_config(ctx: click.Context, output_format: OutputFormat, section: str | None, profile: str | None) -> None:
    """Display the current merged configuration from all sources.

    Shows configuration loaded from defaults, application/user config files,
    .env files, and environment variables.

    Precedence: defaults -> app -> host -> user -> dotenv -> env

    """
    cli_ctx = get_cli_context(ctx)
    effective_config, effective_profile = _settled_config(cli_ctx, profile)
    ignored_sections = _ignored_sections(effective_config)

    extra = {"command": ActionCommand.CONFIG.value, "format": output_format.value, "profile": effective_profile}
    with lib_log_rich.runtime.bind(job_id="cli-config", extra=extra):
        logger.info(
            "Displaying configuration",
            extra={"format": output_format.value, "section": section, "profile": effective_profile},
        )
        # Before a byte is written, in both arms: the human arm used to let the
        # library refuse mid-render, so a redirect of a failed run held the blank
        # line and a header of a page that does not exist while the json arm of
        # the same command left the file empty.
        try:
            _refuse_a_section_that_resolves_to_nothing(effective_config, section)
        except ValueError as exc:
            fail(str(exc), ExitCode.INVALID_ARGUMENT, output_format=output_format)
        if output_format is OutputFormat.JSON:
            data = _redacted_config_data(effective_config, section)
            try:
                emit_action(
                    ActionCommand.CONFIG,
                    MappingResult.model_validate(data),
                    skipped=[_ignored_sentence(ignored) for ignored in ignored_sections],
                )
            except PayloadTooDeepError:
                # The library accepts 100 levels and the JSON writer stops at 98
                # on Windows, so a file both layers accepted reached this.
                fail(
                    "the configuration nests deeper than JSON output can carry on this platform",
                    ExitCode.CONFIG_ERROR,
                    output_format=output_format,
                    hint="--format human prints it.",
                )
            return
        safe_console.echo()
        try:
            # A Console built on the module's own default writer, like
            # ``scan.py``'s ``report``, rather than the ``console=None`` this
            # command used to pass through: that let lib_layered_config build
            # its OWN unguarded ``rich.console.Console``, whose
            # ``on_broken_pipe`` catches the EPIPE and raises
            # ``SystemExit(1)`` - this tool's code for an actionable finding -
            # before any handler here ever sees the write fail. Wrapped in
            # ``safe_console.safe_stream()`` the failure is answered where
            # every other write on this stream is: 141.
            cli_ctx.services.display_config(
                effective_config,
                output_format=output_format,
                section=section,
                profile=effective_profile,
                console=Console(file=safe_console.safe_stream()),
            )
            for ignored in ignored_sections:
                safe_console.echo(_ignored_sentence(ignored))
        except ValueError as exc:
            # A backstop, not the guard: the lookup above is what a missing
            # section now meets. The library decides for itself what it can
            # render, and a disagreement between the two must still leave 22
            # rather than reading as a crash in this tool.
            _fail_after_output(str(exc), ExitCode.INVALID_ARGUMENT, output_format=output_format)


def _ignored_sections(config: Config) -> list[RejectedValue]:
    """The sections given as a single value, which the tool reads none of.

    The page shows such a section as the layer it came from, so without this a
    reader takes it for a setting in force. Only a whole section is judged here:
    a refused VALUE inside a real table is already named on stderr by the root
    group, and the table it sits in is still read.

    Args:
        config: The merged configuration the command is about to show.

    Returns:
        One refusal per section that is not a table, in the order read.
    """
    return [_not_a_table(config, path) for path in shipped_table_paths() if _is_given_as_a_value(config, path)]


def _is_given_as_a_value(config: Config, path: str) -> bool:
    """Whether the user's configuration gives `path` as something other than a table.

    Args:
        config: The merged configuration.
        path: A table the package ships, as a dotted path.

    Returns:
        ``True`` for a value that is present and not a table.
    """
    sentinel = object()
    found: object = config.get(path, default=sentinel)
    return found is not sentinel and not isinstance(found, dict)


def _not_a_table(config: Config, path: str) -> RejectedValue:
    """The refusal for a shipped table given as a single value.

    Args:
        config: The merged configuration.
        path: A table the package ships, as a dotted path.

    Returns:
        What was given and what is in force instead.
    """
    return RejectedValue(
        dotted=path, raw=rendered(config.get(path)), reason=REASON_SECTION, used=f"the shipped [{path}] section"
    )


def _ignored_sentence(ignored: RejectedValue) -> str:
    """One ignored section as the line the page and the envelope share.

    Args:
        ignored: The refusal of a section that is not a table.

    Returns:
        The sentence, without the ``Warning:`` prefix stderr already carried.
    """
    return f"Ignored: {ignored.dotted}={ignored.raw}: {ignored.reason}. Using {ignored.used}."


def _fail_after_output(
    message: str, code: ExitCode, *, output_format: OutputFormat, hint: str | None = None
) -> NoReturn:
    """Report a failure that follows this command's own output.

    Every failure in this module comes after the command has already written
    something, so the sentence starts on a fresh line. ``scan``'s failures come
    before any output and use :func:`~lsdsk.adapters.cli.envelope.fail` directly;
    the blank line is the only difference between them.

    Args:
        message: The failure, as one sentence, with no ``Error:`` prefix.
        code: The exit code to leave with.
        output_format: What the caller asked for.
        hint: An extra line for the person only.

    Raises:
        SystemExit: Always, with `code`.
    """
    safe_console.echo("", err=True)
    fail(message, code, output_format=output_format, hint=hint)


def _refuse_a_section_that_resolves_to_nothing(config: Config, section: str | None) -> None:
    """Refuse a `--section` that names nothing, before either arm writes anything.

    One resolution for both formats, which is what keeps them agreeing about
    what a section is: `Config.get` resolves a dotted path, and an empty string
    is a request for everything rather than a section that is missing.

    Args:
        config: The merged configuration.
        section: What the caller asked for, or ``None`` for all of it.

    Raises:
        ValueError: If a section was named that does not resolve.
    """
    if not section:
        return
    if config.get(section, default=None) is None:
        message = f"Section '{section}' not found"
        raise ValueError(message)


def _redacted_config_data(config: Config, section: str | None) -> dict[str, Any]:
    """The merged configuration as envelope-ready data, with secrets redacted.

    Two passes, and both are load-bearing. The library's own is not optional:
    ``config.as_dict()`` alone puts every token a ``.env`` contributed into the
    machine-readable output in full. Its pattern only matches a sensitive word at
    an underscore boundary though, so ``SmtpPassword``, ``dbPassword`` and
    ``db_pass`` all came through unredacted; the second pass judges a key by the
    words it is made of, camelCase humps included, and only ever hides more. Verified to produce exactly what
    the library's own JSON rendering does, so the envelope changed the wrapper
    and not the contents.

    Selection follows ``Config.get``, which is what the human path resolves
    through, so ``--section`` means the same thing in both. That matters for the
    two cases a top-level key lookup gets wrong: a dotted path names a nested
    table, and an empty string is not a section at all but a request for
    everything.

    It inherits one quirk from that resolution: a section whose value is
    literally null reads as absent. Matching the human path is worth more than
    correcting it here, because the two disagreeing is the failure a caller
    cannot diagnose, and no shipped section holds a null.

    Args:
        config: The merged configuration.
        section: A section or dotted path to return, or ``None`` for all of it.

    Returns:
        The configuration, redacted, optionally narrowed to one section.

    Raises:
        ValueError: If a section was named that does not resolve.
    """
    if not section:
        return redact_secrets(redact_mapping(config.as_dict()))
    # One decider for both arms, so what counts as a missing section cannot
    # drift between the format that refuses early and the one that renders.
    _refuse_a_section_that_resolves_to_nothing(config, section)
    selected: Any = config.get(section, default=None)
    return redact_secrets(redact_mapping({section: selected}))


def _settled_config(cli_ctx: CLIContext, profile: str | None) -> tuple[Config, str | None]:
    """The configuration this command works from, which the root group has already loaded.

    The root group reads this command's own ``--profile`` before it loads anything, so the
    configuration it stored is the one of the profile named here, loaded and judged once, with
    the root's ``--set`` overrides and ``--env-file`` applied: ``config --profile X`` and
    ``--profile X config`` answer alike because they ARE one load. Loading the root's own first
    and this one again judged a configuration the run never used and reported its warnings.

    Args:
        cli_ctx: CLI context containing the stored configuration and the profile it is of.
        profile: This command's own ``--profile``, as click parsed it.

    Returns:
        Tuple of (config, effective_profile).

    Raises:
        RuntimeError: The root group loaded another profile than this command names, which
            only a defect in reading the command's options can cause.
    """
    if profile is not None and profile != cli_ctx.profile:
        message = f"the root group loaded profile {cli_ctx.profile!r}, not {profile!r}. This is a bug."
        raise RuntimeError(message)
    return cli_ctx.config, cli_ctx.profile


def _parse_octal_mode(ctx: click.Context, param: click.Parameter, value: str | None) -> int | None:
    """Parse an octal mode (``750`` or ``0o750``) by the rule the files and ``--set`` are read by.

    That rule is lib_layered_config's ``parse_mode_text``, so the option has no reading of its
    own: ``int(value, 8)`` also took a ``_`` separator, surrounding whitespace, a sign, ``0O``
    and any Unicode digit, which deployed a mode the same text in a file was refused for.
    Whether a parsed mode is SAFE is still the deploy's to judge, as it is for every source.

    Args:
        ctx: Click context (unused but required by callback signature).
        param: Click parameter (unused but required by callback signature).
        value: Octal mode string from CLI, or None.

    Returns:
        Integer permission mode, or None if value was None.

    Raises:
        click.BadParameter: If the library refuses the text, with its reason.
    """
    if value is None:
        return None
    try:
        return parse_mode_text(value)
    except DeployModeError as exc:
        raise click.BadParameter(f"Invalid octal mode: {exc}") from exc


@click.command("config-deploy", context_settings=CLICK_CONTEXT_SETTINGS)
@FORMAT_OPTION
@option(
    "--target",
    "targets",
    type=click.Choice(DeployTarget, case_sensitive=False),
    multiple=True,
    required=True,
    help="Target configuration layer(s) to deploy to (can specify multiple)",
)
@option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing configuration files",
)
@option(
    "--profile",
    type=str,
    default=None,
    help="Override profile from root command (e.g., 'production', 'test')",
)
@option(
    "--permissions/--no-permissions",
    "set_permissions",
    default=None,
    help="Set Unix permissions (755/644 for app/host, 700/600 for user). Default: enabled.",
)
@option(
    "--dir-mode",
    type=str,
    default=None,
    callback=_parse_octal_mode,
    help="Override directory mode (octal, e.g., 750 or 0o750)",
)
@option(
    "--file-mode",
    type=str,
    default=None,
    callback=_parse_octal_mode,
    help="Override file mode (octal, e.g., 640 or 0o640)",
)
@click.pass_context
def cli_config_deploy(
    ctx: click.Context,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    targets: tuple[DeployTarget, ...],
    force: bool,
    profile: str | None,
    set_permissions: bool | None,
    dir_mode: int | None,
    file_mode: int | None,
) -> None:
    """Deploy default configuration to system or user directories.

    Creates configuration files in platform-specific locations:

    - app:  System-wide application config (requires privileges)
    - host: System-wide host config (requires privileges)
    - user: User-specific config (~/.config on Linux)

    By default, existing files are not overwritten. Use --force to overwrite.

    Permission options (POSIX only, no-op on Windows):
    - --permissions/--no-permissions: Enable/disable permission setting
    - --dir-mode: Override directory mode (octal, e.g., 750)
    - --file-mode: Override file mode (octal, e.g., 640)

    """
    cli_ctx = get_cli_context(ctx)
    try:
        permission_overrides = permission_overrides_from(cli_ctx.set_overrides)
    except SectionGivenAsAValueError as refused:
        fail(str(refused), ExitCode.INVALID_ARGUMENT, output_format=output_format)
    # The configuration of the profile this deploy writes, so ``enabled`` and every refusal
    # are read from it; the root group loaded that profile, whichever place named it.
    config, effective_profile = _settled_config(cli_ctx, profile)
    target_values = tuple(t.value for t in targets)

    extra = {
        "command": ActionCommand.CONFIG_DEPLOY.value,
        "targets": target_values,
        "force": force,
        "profile": effective_profile,
    }
    with lib_log_rich.runtime.bind(job_id="cli-config-deploy", extra=extra):
        logger.info(
            "Deploying configuration",
            extra={"targets": target_values, "force": force, "profile": effective_profile},
        )
        _execute_deploy(
            cli_ctx,
            config,
            DeployRequest(
                targets=targets,
                force=force,
                profile=effective_profile,
                set_permissions=set_permissions,
                dir_mode=dir_mode,
                file_mode=file_mode,
                permission_overrides=permission_overrides,
            ),
            output_format=output_format,
        )


def _execute_deploy(
    cli_ctx: CLIContext,
    config: Config,
    request: DeployRequest,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> None:
    """Execute configuration deployment with error handling.

    Args:
        cli_ctx: CLI context containing services.
        config: The configuration of the profile being deployed, which ``enabled`` is read from.
        request: What to deploy, as the command line asked for it. Its
            ``set_permissions`` is still ``None`` where neither
            ``--permissions`` nor ``--no-permissions`` was given, and this is
            where the configured default settles it.
        output_format: What the caller asked for, which decides how a failure answers.

    Raises:
        SystemExit: On permission or other errors.
    """
    # What lsdsk decides itself. The modes are not judged here: the deploy reads them
    # from its own layers and is the one place that refuses one it cannot use.
    reading = read_permission_defaults(config)
    perm_defaults = reading.defaults
    # A section given as one value is said only once the deploy has run: the deploy reads
    # that section too, and where it does it refuses the run over it, so announcing the
    # shipped section first contradicted the refusal printed underneath.
    held_back = [rejected for rejected in reading.rejected if rejected.reason == REASON_SECTION]
    for rejected in reading.rejected:
        if rejected not in held_back:
            safe_console.echo(rejected.as_sentence(), err=True)

    # CLI --permissions/--no-permissions overrides config enabled setting
    settled = request.with_changes(
        set_permissions=request.set_permissions if request.set_permissions is not None else perm_defaults.enabled
    )
    # Worked out before the deploy rewrites the files they are read from, and said only after
    # it: a refused deploy reads no mode, so a sentence about how it would have read one is moot.
    unused_modes = _modes_the_deploy_ignores(config, settled)

    try:
        deployed_paths = cli_ctx.services.deploy_configuration(settled)
    except PermissionError as exc:
        logger.error("Permission denied when deploying configuration", extra={"error": str(exc)})
        _fail_after_output(
            f"Permission denied. {exc}",
            ExitCode.PERMISSION_DENIED,
            output_format=output_format,
            hint="Hint: System-wide deployment (--target app/host) may require sudo.",
        )
    # A ValueError is the library refusing the request itself - a mode out of
    # range, a name it will not take - which is ordinary user input rather than a
    # fault in the deployment. A bad --profile is refused before this is reached,
    # so what arrives here is logged for what it says, not named after one cause.
    except ValueError as exc:
        logger.error("Deployment refused", extra={"error": str(exc), "error_type": type(exc).__name__})
        _fail_after_output(str(exc), ExitCode.INVALID_ARGUMENT, output_format=output_format)
    # OSError is what deployment legitimately fails with - a full disk, a
    # read-only target - and ConfigurationError is what the port may report for
    # a configuration it cannot use; the shipped adapter raises only the first.
    # Anything else is a bug in lsdsk, and catching it here would print it as a
    # one-line user error and swallow the traceback that --traceback asked for,
    # leaving the only copy of it in the log. Let it reach the handler in
    # main.py, which is what honours the flag. Neither answer is 1, which a
    # reporting command means as "a warning or a critical was found": a failed
    # write leaves the code every failed write leaves, and a configuration
    # problem the code every other command leaves for one.
    except OSError as exc:
        logger.error("Failed to deploy configuration", extra={"error": str(exc), "error_type": type(exc).__name__})
        _fail_after_output(f"Failed to deploy configuration: {exc}", ExitCode.IO_ERROR, output_format=output_format)
    except ConfigurationError as exc:
        logger.error("Failed to deploy configuration", extra={"error": str(exc), "error_type": type(exc).__name__})
        _fail_after_output(f"Failed to deploy configuration: {exc}", ExitCode.CONFIG_ERROR, output_format=output_format)
    for rejected in held_back:
        safe_console.echo(rejected.as_sentence(), err=True)
    for ignored in unused_modes:
        safe_console.echo(ignored.as_sentence(), err=True)
    # Outside the try on purpose: the files are written by now, and a report that
    # cannot reach a full standard output is a failure to REPORT, which is not the
    # "Failed to deploy configuration" the arms above would have called it.
    _report_deployment_result(deployed_paths, settled, output_format)


def _reads_configured_modes(request: DeployRequest) -> bool:
    """Whether the deploy reads any mode from the configuration at all.

    It does not on Windows, which applies no mode; with ``--no-permissions`` or an ``enabled``
    that settled to false, which set none; or when ``--dir-mode`` and ``--file-mode`` both
    give the modes outright.

    Args:
        request: The deploy, with ``set_permissions`` already settled.

    Returns:
        ``False`` when every configured mode is unused whatever layer gave it.
    """
    explicit = request.dir_mode is not None and request.file_mode is not None
    return modes_apply() and request.set_permissions is not False and not explicit


def _modes_the_deploy_ignores(config: Config, request: DeployRequest) -> tuple[ModeTheDeployIgnores, ...]:
    """The modes in force that the deploy will not use, read against the deploy's own read.

    Args:
        config: The configuration of the profile being deployed.
        request: The deploy, with ``set_permissions`` already settled.

    Returns:
        Each mode to name; none where the deploy reads no configured mode, and none where its
        own read fails, because the deploy then refuses with its own sentence.
    """
    if not _reads_configured_modes(request):
        return ()
    try:
        written = paths_the_deploy_writes(request.targets, profile=request.profile)
        used = read_config_skipping(profile=request.profile, skip=written)
    except (ConfigError, OSError, ValueError):
        return ()
    # Read before the deploy runs, which is when this is called: a file there now is one a run
    # without --force leaves alone.
    kept: frozenset[Path] = frozenset(path for path in written if not request.force and path.exists())
    return modes_the_deploy_ignores(config, used=used, written=written, kept=kept)


def _report_deployment_result(
    deployed_paths: list[Path],
    request: DeployRequest,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> None:
    """Report deployment results to the user.

    A deploy that wrote nothing says why: without ``--force`` the files already there are kept,
    and ``--force`` is the way to overwrite them; with it, every file already held exactly what
    the deploy would have written, which the library skips, so offering ``--force`` again would
    be advice the reader has just followed.

    Args:
        deployed_paths: List of paths where configs were deployed.
        request: The deploy as it ran, with ``set_permissions`` settled.
        output_format: What the caller asked for, which decides how the result is written.
    """
    profile = request.profile
    set_permissions = bool(request.set_permissions)
    if output_format is OutputFormat.JSON:
        emit_action(
            ActionCommand.CONFIG_DEPLOY,
            DeployResult(
                outcome=_outcome_of(deployed_paths),
                deployed=[str(path) for path in deployed_paths],
                profile=profile,
                permissions_set=set_permissions,
            ),
            # Writing nothing is the documented outcome when the files already
            # exist, not an error: `ok` agrees with the exit code, and `outcome`
            # tells it from a deploy that wrote. A deploy that failed exits
            # non-zero instead.
        )
        return
    if deployed_paths:
        profile_msg = f" (profile: {profile})" if profile else ""
        perm_msg = "" if set_permissions else " (permissions not set)"
        safe_console.echo(f"\nConfiguration deployed successfully{profile_msg}{perm_msg}:")
        for path in deployed_paths:
            # ASCII marker on purpose: a non-ASCII glyph here crashes config-deploy with a
            # UnicodeEncodeError on a legacy Windows console codepage (cp1252) even though the
            # files were already written, so exit 1 misreports a deploy that actually succeeded.
            safe_console.echo(f"  + {path}")
    elif request.force:
        safe_console.echo("\nNo files were written: every target file already holds the shipped configuration.")
    else:
        safe_console.echo("\nNo files were created (all target files already exist).")
        safe_console.echo("Use --force to overwrite existing configuration files.")


def _report_generated_examples(paths: list[Path], destination: str, output_format: OutputFormat) -> None:
    """Say which example files were written, in the format the caller asked for.

    Args:
        paths: The files the run wrote.
        destination: The directory they were written to.
        output_format: What the caller asked for.
    """
    if output_format is OutputFormat.JSON:
        emit_action(
            ActionCommand.CONFIG_GENERATE_EXAMPLES,
            GenerateExamplesResult(
                outcome=_outcome_of(paths), generated=[str(p) for p in paths], destination=str(destination)
            ),
        )
    elif paths:
        safe_console.echo(f"\nGenerated {len(paths)} example file(s):")
        for p in paths:
            safe_console.echo(f"  {p}")
    else:
        safe_console.echo("\nNo files generated (all already exist). Use --force to overwrite.")


@click.command("config-generate-examples", context_settings=CLICK_CONTEXT_SETTINGS)
@FORMAT_OPTION
@option("--destination", type=NonEmptyPath(file_okay=False), required=True, help="Directory to write example files")
@option("--force", is_flag=True, default=False, help="Overwrite existing files")
@click.pass_context
def cli_config_generate_examples(
    ctx: click.Context, destination: str, force: bool, output_format: OutputFormat
) -> None:
    """Generate example configuration files in a target directory.

    Creates example TOML configuration files showing all available options
    with their default values and documentation comments. Useful for learning
    the configuration structure, creating initial configuration files, or
    documenting available settings.

    By default, existing files are not overwritten. Use --force to overwrite.

    """
    # Through the container, like every other adapter call these commands make.
    # Reached directly, the six tests that cover this command could only replace
    # the module's own attribute, so each proved that the command forwards a
    # flag to a function the test installed.
    cli_ctx = get_cli_context(ctx)
    extra = {"command": ActionCommand.CONFIG_GENERATE_EXAMPLES.value, "destination": destination, "force": force}
    with lib_log_rich.runtime.bind(job_id="cli-config-generate-examples", extra=extra):
        logger.info("Generating example configuration files", extra={"destination": destination, "force": force})
        try:
            paths = cli_ctx.services.generate_examples(
                destination=destination,
                slug=__init__conf__.LAYEREDCONF_SLUG,
                vendor=__init__conf__.LAYEREDCONF_VENDOR,
                app=__init__conf__.LAYEREDCONF_APP,
                force=force,
            )
        # Before the OSError arm, because PermissionError IS one: caught
        # wholesale below, a refused directory left 1 where _execute_deploy and
        # snapshot both leave 13 with a sudo hint for the identical errno on the
        # identical operation, and 1 is the code a caller reads as a failing
        # disk. Same failure, same answer.
        except PermissionError as exc:
            logger.error("Permission denied when generating examples", extra={"error": str(exc)})
            _fail_after_output(
                f"Permission denied. {exc}",
                ExitCode.PERMISSION_DENIED,
                output_format=output_format,
                hint="Hint: Writing outside your own directories may require sudo.",
            )
        # Writing example files fails with OSError and nothing else. A wider
        # catch here would turn a bug in lsdsk into the same one-line message a
        # full disk produces, with the traceback discarded even under
        # --traceback. See the matching note in _execute_deploy.
        except OSError as exc:
            logger.error("Failed to generate examples", extra={"error": str(exc)})
            _fail_after_output(str(exc), ExitCode.IO_ERROR, output_format=output_format)
        # What the writer refused before writing: a directory where a file belongs.
        except ConfigurationError as exc:
            logger.error("Refused to generate examples", extra={"error": str(exc)})
            _fail_after_output(str(exc), ExitCode.CONFIG_ERROR, output_format=output_format)
        # Outside the try on purpose: the files are written by now, see _execute_deploy.
        _report_generated_examples(paths, destination, output_format)


__all__ = ["cli_config", "cli_config_deploy", "cli_config_generate_examples"]
