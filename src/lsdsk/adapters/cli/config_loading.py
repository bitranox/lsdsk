"""Load one profile's configuration and settle it, with one answer for each way that fails.

The root group loads the configuration once, for the profile the run uses - its own
``--profile``, or the one ``config`` or ``config-deploy`` names after it - and every decision
about the loaded configuration lives here: which exit code a failed load leaves with, the
refusal of a mode that could be octal digits, and the warnings about a profile that named
nothing, a key nothing reads and a value nothing can use. The caller supplies only HOW it
refuses: the root group does so before the subcommand has parsed its ``--format``
(:class:`~.envelope.RefusedBeforeTheRun`).

Contents:
    * :class:`UnusableConfigError` - a refusal, and the exit code it leaves with.
    * :func:`load_profile_config` - load, mapping each failure to its code, past a file a
      forced deploy replaces.
    * :func:`refuse_an_ambiguous_mode` - stop on an int mode that could be octal digits.
    * :func:`report_profile_that_named_nothing` - warn about a profile nothing answered to.
    * :func:`report_keys_nothing_reads` - warn about a file key nothing reads.
    * :func:`report_values_nothing_uses` - warn about a value nothing can use.

System Role:
    Adapter layer, CLI. Used by :mod:`.root`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib_layered_config import ConfigError

from lsdsk.adapters.config.deploy import paths_the_deploy_writes
from lsdsk.adapters.config.history import read_history_settings
from lsdsk.adapters.config.known_keys import nearest_known_key, unknown_owned_keys
from lsdsk.adapters.config.loader import read_config_skipping, unparsable_files
from lsdsk.adapters.config.permissions import read_permission_defaults
from lsdsk.adapters.config.profiles import contributed_layers, existing_profiles, nearest_profile
from lsdsk.adapters.config.tunables import read_display_settings, read_thresholds
from lsdsk.adapters.config.values import AmbiguousModeError

from . import safe_console
from .exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from lib_layered_config import Config

    from lsdsk.composition import AppServices
    from lsdsk.domain.enums import DeployTarget


class UnusableConfigError(Exception):
    """The configuration cannot be used, and the code the run leaves with because of it.

    Carried to the caller rather than answered here, because the two callers answer at
    different moments: the root group cannot see ``--format`` yet, a subcommand can.

    Attributes:
        message: The refusal, as one sentence, with no ``Error:`` prefix.
        code: The exit code it leaves with.
    """

    def __init__(self, message: str, *, code: ExitCode) -> None:
        """Record the sentence and the code.

        Args:
            message: The refusal, as one sentence, with no ``Error:`` prefix.
            code: The exit code it leaves with.
        """
        super().__init__(message)
        self.message = message
        self.code = code


def load_profile_config(
    services: AppServices,
    *,
    profile: str | None,
    env_file: str | None,
    overwrites: tuple[DeployTarget, ...] = (),
) -> Config:
    """Load the configuration for `profile`, or refuse with the code its failure means.

    ``78`` is ``EX_CONFIG``: what broke is a file the reader wrote. A read the system refused
    is ``13``, because the reader fixes it with ``chmod`` rather than an editor. Only the
    library's own error and the ``OSError`` family are caught, so a bug in the loader still
    reaches the handler that honours ``--traceback``.

    A forced deploy is the one run a file that does not parse may not stop, when it is a file
    that deploy rewrites: the deploy exists to replace it, and its own read of the permission
    modes already skips every file it writes. Such a file is skipped here too, with one
    warning naming it, and the configuration is the one the deploy itself reads (see
    :func:`~lsdsk.adapters.config.loader.read_config_skipping`). An unreadable file, and a
    broken file the deploy does not rewrite, still refuse.

    Args:
        services: The wired adapters, whose loader is called.
        profile: The profile to load, or ``None`` for none.
        env_file: An explicit ``.env`` path, or ``None`` for the loader's own search.
        overwrites: The targets a ``config-deploy --force`` of this run rewrites; empty for
            any other run.

    Returns:
        The merged configuration.

    Raises:
        UnusableConfigError: ``PERMISSION_DENIED`` when a file is unreadable for lack of
            permission, ``CONFIG_ERROR`` when the configuration cannot load for any other
            reason.
    """
    try:
        return _mapped(lambda: services.get_config(profile=profile, dotenv_path=env_file))
    except UnusableConfigError as refused:
        if refused.code is not ExitCode.CONFIG_ERROR or not overwrites:
            raise
        broken = unparsable_files(paths_the_deploy_writes(overwrites, profile=profile))
        if not broken:
            raise
    for _path, reason in broken:
        safe_console.echo(
            f"Warning: {reason}. config-deploy --force replaces it, so it is skipped while reading the configuration.",
            err=True,
        )
    return _mapped(lambda: read_config_skipping(profile=profile, skip=frozenset(path for path, _ in broken)))


def _mapped(load: Callable[[], Config]) -> Config:
    """Run `load`, turning each way a configuration read fails into the refusal it means.

    Raises:
        UnusableConfigError: See :func:`load_profile_config`.
    """
    try:
        return load()
    except ConfigError as error:
        code = ExitCode.PERMISSION_DENIED if _refused_for_permission(error) else ExitCode.CONFIG_ERROR
        raise UnusableConfigError(str(error), code=code) from error
    except PermissionError as error:
        raise UnusableConfigError(str(error), code=ExitCode.PERMISSION_DENIED) from error
    except OSError as error:
        raise UnusableConfigError(str(error), code=ExitCode.CONFIG_ERROR) from error


def _refused_for_permission(error: BaseException) -> bool:
    """Whether a configuration error was, underneath, a read the system refused.

    The layered library reports an unreadable configuration file as its own
    ``LayerLoadError`` and keeps the ``PermissionError`` as the cause, so the
    refusal is a permission problem the reader fixes with ``chmod``, not a file
    they wrote wrong, and it keeps the exit code that says so.

    Args:
        error: The error the loader raised.

    Returns:
        ``True`` when a ``PermissionError`` stands anywhere in its cause chain.

    Example:
        >>> try:
        ...     try:
        ...         raise PermissionError(13, "denied")
        ...     except PermissionError as inner:
        ...         raise ValueError("cannot be read") from inner
        ... except ValueError as outer:
        ...     _refused_for_permission(outer)
        True
        >>> _refused_for_permission(ValueError("not valid TOML"))
        False
    """
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, PermissionError):
            return True
        seen = seen.__cause__
    return False


def refuse_an_ambiguous_mode(config: Config) -> None:
    """Refuse a permission mode that is an int which could be octal digits.

    The one configured value that stops a run instead of falling back: ``755`` from an
    unquoted ``.env`` line is the int 755, which is either octal digits or the decimal mode
    0o1363, and guessing either gives a mode nobody can be sure they meant. Every other
    refused value keeps the warn-and-use-the-default behaviour.

    Args:
        config: The configuration with any ``--set`` already merged in.

    Raises:
        UnusableConfigError: With ``CONFIG_ERROR`` for such a mode.
    """
    try:
        read_permission_defaults(config)
    except AmbiguousModeError as error:
        raise UnusableConfigError(str(error), code=ExitCode.CONFIG_ERROR) from error


def report_profile_that_named_nothing(config: Config, profile: str | None) -> None:
    """Say so on stderr when `profile` loaded nothing under that name.

    A profile REPLACES the configuration directories rather than adding to them, so a name
    with one letter wrong reads no configuration file at all - not even the ones read without
    a profile - and every value the environment or ``--set`` does not give is the shipped one,
    at exit 0. Every other identifier typed at this CLI answers back - an unknown ``--set``
    key is refused, an unknown file key gets a did-you-mean, an invalid profile SYNTAX exits
    22 - and this was the one well-formed name allowed to mean nothing quietly.

    Warned rather than refused, for the same reason an unknown file key is: the values are
    inert either way and the run still diagnoses the hardware, which is what somebody is at
    the terminal for. The suggestion is left out when it is the name typed, which happens for
    a profile directory that exists and holds nothing readable.

    Args:
        config: The configuration as loaded, carrying which files it came from.
        profile: The name that was asked for, or ``None`` when none was.

    Side Effects:
        Writes one line to stderr when the name answered nothing.
    """
    if profile is None or contributed_layers(config, profile):
        return
    suggestion = nearest_profile(profile, existing_profiles())
    meant = f" Did you mean {suggestion}?" if suggestion not in (None, profile) else ""
    safe_console.echo(
        f"Warning: profile {profile} named nothing, so every value the environment or --set "
        f"does not give is the shipped default.{meant}",
        err=True,
    )


def report_keys_nothing_reads(config: Config) -> None:
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


def report_values_nothing_uses(config: Config, *, history_file: Path | None) -> None:
    """Say so on stderr when a configured VALUE is not one this tool can use.

    The key half of this is :func:`report_keys_nothing_reads`, and the reasoning is
    the same: the value is inert, and an inert value must not read like an applied
    one. ``lsdsk config`` reports the refused text back as the value in force, so
    without this line an operator gets positive confirmation of a setting that
    decided nothing.

    Warned about rather than refused on BOTH surfaces, unlike an unknown ``--set``
    key. The fallback is deliberate and documented: a malformed threshold must never
    stop somebody diagnosing a failing drive, so the run goes on with the shipped
    figure and says which one it used.

    Read once per load rather than where the values are used, because
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


__all__ = [
    "UnusableConfigError",
    "load_profile_config",
    "refuse_an_ambiguous_mode",
    "report_keys_nothing_reads",
    "report_profile_that_named_nothing",
    "report_values_nothing_uses",
]
