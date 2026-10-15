"""Load one profile's configuration and settle it, with one answer for each way that fails.

The root group loads the configuration once. ``config`` and ``config-deploy`` load it again
when their own ``--profile`` names another one, and that second load used to be a bare call:
a file it could not read escaped as the layered library's ``LayerLoadError`` (exit 70 and no
envelope, where the root answers 78 or 13 with one), a profile that named nothing loaded
without a word, and a mode that could be octal digits was never asked about. So every
decision about a freshly loaded configuration lives here, and each caller supplies only HOW
it refuses: the root group before the subcommand has parsed its ``--format``
(:class:`~.envelope.RefusedBeforeTheRun`), a subcommand afterwards (:func:`~.envelope.fail`).

Contents:
    * :class:`UnusableConfigError` - a refusal, and the exit code it leaves with.
    * :func:`load_profile_config` - load, mapping each failure to its code.
    * :func:`refuse_an_ambiguous_mode` - stop on an int mode that could be octal digits.
    * :func:`report_profile_that_named_nothing` - warn about a profile nothing answered to.

System Role:
    Adapter layer, CLI. Shared by :mod:`.root` and :mod:`.commands.config`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib_layered_config import ConfigError

from lsdsk.adapters.config.permissions import read_permission_defaults
from lsdsk.adapters.config.profiles import contributed_layers, existing_profiles, nearest_profile
from lsdsk.adapters.config.values import AmbiguousModeError

from . import safe_console
from .exit_codes import ExitCode

if TYPE_CHECKING:
    from lib_layered_config import Config

    from lsdsk.composition import AppServices


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


def load_profile_config(services: AppServices, *, profile: str | None, env_file: str | None) -> Config:
    """Load the configuration for `profile`, or refuse with the code its failure means.

    ``78`` is ``EX_CONFIG``: what broke is a file the reader wrote. A read the system refused
    is ``13``, because the reader fixes it with ``chmod`` rather than an editor. Only the
    library's own error and the ``OSError`` family are caught, so a bug in the loader still
    reaches the handler that honours ``--traceback``.

    Args:
        services: The wired adapters, whose loader is called.
        profile: The profile to load, or ``None`` for none.
        env_file: An explicit ``.env`` path, or ``None`` for the loader's own search.

    Returns:
        The merged configuration.

    Raises:
        UnusableConfigError: ``PERMISSION_DENIED`` when a file is unreadable for lack of
            permission, ``CONFIG_ERROR`` when the configuration cannot load for any other
            reason.
    """
    try:
        return services.get_config(profile=profile, dotenv_path=env_file)
    except ConfigError as error:
        code = ExitCode.PERMISSION_DENIED if refused_for_permission(error) else ExitCode.CONFIG_ERROR
        raise UnusableConfigError(str(error), code=code) from error
    except PermissionError as error:
        raise UnusableConfigError(str(error), code=ExitCode.PERMISSION_DENIED) from error
    except OSError as error:
        raise UnusableConfigError(str(error), code=ExitCode.CONFIG_ERROR) from error


def refused_for_permission(error: BaseException) -> bool:
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
        ...     refused_for_permission(outer)
        True
        >>> refused_for_permission(ValueError("not valid TOML"))
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


__all__ = [
    "UnusableConfigError",
    "load_profile_config",
    "refuse_an_ambiguous_mode",
    "refused_for_permission",
    "report_profile_that_named_nothing",
]
