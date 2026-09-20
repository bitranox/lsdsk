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
from lib_layered_config import Config, generate_examples, redact_mapping

from lsdsk import __init__conf__
from lsdsk.adapters.config.overrides import apply_overrides
from lsdsk.adapters.config.permissions import get_permission_defaults
from lsdsk.adapters.config.secrets import redact_secrets
from lsdsk.domain.deployment import DeployRequest
from lsdsk.domain.enums import ActionCommand, DeployTarget, OutputFormat
from lsdsk.domain.errors import ConfigurationError

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import CLIContext, get_cli_context
from ..envelope import ActionResult, MappingResult, emit_action, fail
from ..exit_codes import ExitCode
from ..typed_click import option

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class DeployResult(ActionResult):
    """Which configuration files were written, and under which profile.

    An empty `deployed` is the documented outcome when the files already exist,
    not a failure; the envelope's `skipped` says which of the two it was.
    """

    deployed: list[str]
    profile: str | None
    permissions_set: bool


class GenerateExamplesResult(ActionResult):
    """Which example files were scaffolded, and where."""

    generated: list[str]
    destination: str


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
    effective_config, effective_profile = _resolve_config(cli_ctx, profile)

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
            emit_action(ActionCommand.CONFIG, MappingResult.model_validate(data))
            return
        safe_console.echo()
        try:
            cli_ctx.services.display_config(
                effective_config, output_format=output_format, section=section, profile=effective_profile
            )
        except ValueError as exc:
            # A backstop, not the guard: the lookup above is what a missing
            # section now meets. The library decides for itself what it can
            # render, and a disagreement between the two must still leave 22
            # rather than reading as a crash in this tool.
            _fail_after_output(str(exc), ExitCode.INVALID_ARGUMENT, output_format=output_format)


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


def _get_effective_profile(cli_ctx: CLIContext, profile_override: str | None) -> str | None:
    """Get effective profile: override takes precedence over context."""
    return profile_override if profile_override else cli_ctx.profile


def _resolve_config(cli_ctx: CLIContext, profile: str | None) -> tuple[Config, str | None]:
    """Resolve configuration from context or reload with profile override.

    When a subcommand-level profile override is specified, reloads config
    with that profile and reapplies any root-level ``--set`` overrides
    stored in the CLI context.

    Args:
        cli_ctx: CLI context containing stored config and services.
        profile: Optional profile override.

    Returns:
        Tuple of (config, effective_profile).
    """
    effective_profile = _get_effective_profile(cli_ctx, profile)
    if profile:
        config = cli_ctx.services.get_config(profile=profile)
        return apply_overrides(config, cli_ctx.set_overrides), effective_profile
    return cli_ctx.config, effective_profile


def _parse_octal_mode(ctx: click.Context, param: click.Parameter, value: str | None) -> int | None:
    """Parse octal mode string (e.g., '750' or '0o750') to int.

    Args:
        ctx: Click context (unused but required by callback signature).
        param: Click parameter (unused but required by callback signature).
        value: Octal mode string from CLI, or None.

    Returns:
        Integer permission mode, or None if value was None.

    Raises:
        click.BadParameter: If value cannot be parsed as octal.
    """
    if value is None:
        return None
    try:
        # Handle both '750' and '0o750' formats
        return int(value, 8) if not value.startswith("0o") else int(value, 0)
    except ValueError as exc:
        raise click.BadParameter(f"Invalid octal mode: {value}") from exc


@click.command("config-deploy", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--format",
    "output_format",
    type=click.Choice(OutputFormat, case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)
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
    effective_profile = _get_effective_profile(cli_ctx, profile)
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
            DeployRequest(
                targets=targets,
                force=force,
                profile=effective_profile,
                set_permissions=set_permissions,
                dir_mode=dir_mode,
                file_mode=file_mode,
            ),
            output_format=output_format,
        )


def _execute_deploy(
    cli_ctx: CLIContext,
    request: DeployRequest,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> None:
    """Execute configuration deployment with error handling.

    Args:
        cli_ctx: CLI context containing services.
        request: What to deploy, as the command line asked for it. Its
            ``set_permissions`` is still ``None`` where neither
            ``--permissions`` nor ``--no-permissions`` was given, and this is
            where the configured default settles it.
        output_format: What the caller asked for, which decides how a failure answers.

    Raises:
        SystemExit: On permission or other errors.
    """
    # Get permission defaults from config
    perm_defaults = get_permission_defaults(cli_ctx.config)

    # CLI --permissions/--no-permissions overrides config enabled setting
    settled = request.with_changes(
        set_permissions=request.set_permissions if request.set_permissions is not None else perm_defaults.enabled
    )

    try:
        deployed_paths = cli_ctx.services.deploy_configuration(settled)
        _report_deployment_result(deployed_paths, settled.profile, bool(settled.set_permissions), output_format)
    except PermissionError as exc:
        logger.error("Permission denied when deploying configuration", extra={"error": str(exc)})
        _fail_after_output(
            f"Permission denied. {exc}",
            ExitCode.PERMISSION_DENIED,
            output_format=output_format,
            hint="Hint: System-wide deployment (--target app/host) may require sudo.",
        )
    # A rejected --profile is ordinary user input, not a fault in the
    # deployment: the configuration library validates the name and raises
    # ValueError before it writes anything. It earns the invalid-argument code
    # that a bad --section already uses, rather than the generic failure one.
    except ValueError as exc:
        logger.error("Rejected profile name", extra={"error": str(exc)})
        _fail_after_output(str(exc), ExitCode.INVALID_ARGUMENT, output_format=output_format)
    # OSError and ConfigurationError are what deployment legitimately fails
    # with: a full disk, a read-only target, a config that will not resolve.
    # Anything else is a bug in lsdsk, and catching it here would print it as a
    # one-line user error and swallow the traceback that --traceback asked for,
    # leaving the only copy of it in the log. Let it reach the handler in
    # main.py, which is what honours the flag.
    except (OSError, ConfigurationError) as exc:
        logger.error("Failed to deploy configuration", extra={"error": str(exc), "error_type": type(exc).__name__})
        _fail_after_output(
            f"Failed to deploy configuration: {exc}", ExitCode.GENERAL_ERROR, output_format=output_format
        )


def _report_deployment_result(
    deployed_paths: list[Path],
    profile: str | None,
    set_permissions: bool,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> None:
    """Report deployment results to the user.

    Args:
        deployed_paths: List of paths where configs were deployed.
        profile: Optional profile name for display.
        set_permissions: Whether permissions were set.
        output_format: What the caller asked for, which decides how the result is written.
    """
    if output_format is OutputFormat.JSON:
        emit_action(
            ActionCommand.CONFIG_DEPLOY,
            DeployResult(
                deployed=[str(path) for path in deployed_paths],
                profile=profile,
                permissions_set=set_permissions,
            ),
            # Writing nothing is the documented outcome when the files already
            # exist, not an error; a caller has to be able to tell it from a
            # deploy that failed, which exits non-zero instead.
            skipped=[] if deployed_paths else ["every target file already exists; --force overwrites"],
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
    else:
        safe_console.echo("\nNo files were created (all target files already exist).")
        safe_console.echo("Use --force to overwrite existing configuration files.")


@click.command("config-generate-examples", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--format",
    "output_format",
    type=click.Choice(OutputFormat, case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)
@option("--destination", type=click.Path(file_okay=False), required=True, help="Directory to write example files")
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
    extra = {"command": ActionCommand.CONFIG_GENERATE_EXAMPLES.value, "destination": destination, "force": force}
    with lib_log_rich.runtime.bind(job_id="cli-config-generate-examples", extra=extra):
        logger.info("Generating example configuration files", extra={"destination": destination, "force": force})
        try:
            paths = generate_examples(
                destination=destination,
                slug=__init__conf__.LAYEREDCONF_SLUG,
                vendor=__init__conf__.LAYEREDCONF_VENDOR,
                app=__init__conf__.LAYEREDCONF_APP,
                force=force,
            )
            if output_format is OutputFormat.JSON:
                emit_action(
                    ActionCommand.CONFIG_GENERATE_EXAMPLES,
                    GenerateExamplesResult(generated=[str(p) for p in paths], destination=str(destination)),
                    skipped=[] if paths else ["every example file already exists; --force overwrites"],
                )
            elif paths:
                safe_console.echo(f"\nGenerated {len(paths)} example file(s):")
                for p in paths:
                    safe_console.echo(f"  {p}")
            else:
                safe_console.echo("\nNo files generated (all already exist). Use --force to overwrite.")
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
            _fail_after_output(str(exc), ExitCode.GENERAL_ERROR, output_format=output_format)


__all__ = ["cli_config", "cli_config_deploy", "cli_config_generate_examples"]
