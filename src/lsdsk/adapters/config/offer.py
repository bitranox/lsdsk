"""Turn the desktop-shortcut offer off in the reader's own configuration.

When a reader answers "never" to the offer, lsdsk records it by setting
``display.offer_desktop_shortcut = false`` in the user ``config.toml``, so the
question is never asked again. Two things make that safe to do to a file a
reader owns: the write round-trips through tomlkit, so every comment, key and
blank line already there survives, and it lands atomically through the shared
writer, so a failure leaves the old file untouched. The written file is read
back once, which turns a malformed round-trip into a failure here rather than a
puzzle in the loader later.

System Role:
    Adapter-layer configuration write boundary - the one place this tool edits a
    file the reader, not the tool, owns.

Contents:
    * :func:`user_config_path` - where this platform's user ``config.toml`` lives.
    * :func:`disable_offer` - set the opt-out key and write it back.
"""

from __future__ import annotations

import tomllib
from collections.abc import MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import tomlkit
from lib_layered_config import Layer, ValidationError

# The resolver and the deploy strategies are the code ``config-deploy`` and the
# loader run themselves, reached directly because the public
# ``deploy_config_paths`` answers with RESOLVED paths: through a symlinked
# ``config.toml`` those carry the link target's name, so the user file cannot
# be picked out of them by name (``profiles.py`` reaches the resolver the same way).
from lib_layered_config.adapters.path_resolvers.default import DefaultPathResolver
from lib_layered_config.deploy import (
    DeploymentStrategy,
    LinuxDeployment,
    MacDeployment,
    WindowsDeployment,
)

from lsdsk import __init__conf__
from lsdsk.adapters.atomicfile import replace_atomically
from lsdsk.adapters.config.reader_strategy import reader_strategy
from lsdsk.adapters.config.tunables import DISPLAY_SECTION

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The key the offer writes, the same name the model and the shipped file carry.
_OFFER_KEY = "offer_desktop_shortcut"

#: The user layer's own file, as opposed to the ``config.d`` dropins beside it.
_USER_CONFIG_NAME = "config.toml"

#: A user file may hold secrets in other keys, so it is owner-only like the store.
_USER_FILE_MODE = 0o600

#: The variables each platform's user directory can be built from, in the
#: library's own order of preference. A relative directory is refused, and the
#: refusal names whichever of these it came from: telling a Windows reader to
#: set an XDG variable names one Windows never reads.
_BASE_VARIABLES: dict[str, tuple[str, ...]] = {
    "win": ("APPDATA", "LOCALAPPDATA", "USERPROFILE"),
    "darwin": ("HOME",),
    "linux": ("XDG_CONFIG_HOME", "HOME"),
}

#: The one variable whose absence is itself a remedy: the XDG spec then falls back to ``~/.config``.
_UNSETTABLE_BASE = "XDG_CONFIG_HOME"


class NoUserConfigPathError(OSError):
    """No user configuration file can be named on this machine, so nothing is written there."""


class DisplayNotATableError(ValueError):
    """The user file holds ``display`` as something other than a table, so the opt-out cannot be added."""


def user_config_path(*, platform: str | None = None) -> Path:
    """The user ``config.toml`` the loader reads, whether or not it exists yet.

    Named by the deploy's own user destination, so it lands in the one place
    this platform's user layer is read from - Linux ``~/.config``, macOS
    ``~/Library/Application Support``, Windows ``%APPDATA%`` - rather than a
    path guessed here. The path is NOT resolved: where ``config.toml`` is a
    symlink, this is the link, and its directory is the configuration directory
    the reader sees.

    Args:
        platform: ``sys.platform`` to answer for; this machine's when not given.

    Returns:
        The absolute path of the user configuration file.

    Raises:
        NoUserConfigPathError: The configuration library reads no user layer on
            ``platform``, or the base it would use is relative (an
            ``XDG_CONFIG_HOME`` such as ``config``), which would put the file
            wherever lsdsk happened to be started.

    Example:
        >>> user_config_path().name
        'config.toml'
    """
    resolver = DefaultPathResolver(
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        profile=None,
        platform=platform,
    )
    deployment = _deployment_for(resolver)
    if deployment is None:
        raise NoUserConfigPathError(f"lsdsk reads no user configuration on {resolver.platform}")
    remedy = _relative_base_remedy(platform=resolver.platform, env=resolver.env)
    _refuse_a_relative_config_home(resolver, remedy=remedy)
    try:
        destination = deployment.destination_for(Layer.USER)
    except ValidationError as refused:
        # lib_layered_config 8.0.0 refuses a relative base directory itself, with
        # LayerRootNotAbsoluteError (a ValidationError); 7.7.0 returned the relative
        # path, which the check below refuses. Both reach the same refusal, this
        # one naming the base the directory would be under, since there is no path.
        base = _relative_base(platform=resolver.platform, env=resolver.env)
        where = f" under {base}" if base else ""
        raise NoUserConfigPathError(
            f"the user configuration directory{where} is not an absolute path; {remedy}"
        ) from refused
    if not destination.is_absolute():
        raise NoUserConfigPathError(
            f"the user configuration directory {destination.parent} is not an absolute path; {remedy}"
        )
    return destination


def _refuse_a_relative_config_home(resolver: DefaultPathResolver, *, remedy: str) -> None:
    """Refuse a relative ``XDG_CONFIG_HOME`` on a Linux-layout platform, whichever library version reads it.

    lsdsk refuses a relative XDG directory everywhere it meets one (the shortcut
    adapter does the same), because it names a different folder wherever lsdsk is
    started. lib_layered_config 7.7.0 builds a relative user directory from it,
    which the absolute check in :func:`user_config_path` refuses; 8.0.0 ignores it
    and reads ``~/.config``, as the XDG specification says. Checked here first, so
    the same environment gets the same refusal on both.

    Args:
        resolver: The path resolver for the platform being answered for.
        remedy: The remedy clause, naming the variables to fix.

    Raises:
        NoUserConfigPathError: ``XDG_CONFIG_HOME`` is set to a relative path.
    """
    if resolver.platform.startswith("win") or resolver.platform == "darwin":
        return
    configured = resolver.env.get(_UNSETTABLE_BASE, "")
    if configured and not Path(configured).is_absolute():
        directory = Path(configured) / __init__conf__.LAYEREDCONF_SLUG
        raise NoUserConfigPathError(f"the user configuration directory {directory} is not an absolute path; {remedy}")


def _candidates(platform: str) -> tuple[str, ...]:
    """The variables ``platform``'s user directory can be built from, in the library's order."""
    if platform.startswith("win"):
        return _BASE_VARIABLES["win"]
    return _BASE_VARIABLES.get(platform, _BASE_VARIABLES["linux"])


def _relative_variables(*, platform: str, env: Mapping[str, str]) -> list[str]:
    """Each candidate variable that is set to a relative path, in the library's order."""
    return [name for name in _candidates(platform) if env.get(name, "") and not Path(env[name]).is_absolute()]


def _relative_base(*, platform: str, env: Mapping[str, str]) -> str:
    """The value of the first candidate variable set to a relative path, or ``""`` when none is.

    Example:
        >>> _relative_base(platform="win32", env={"APPDATA": "rel", "LOCALAPPDATA": "C:/x"})
        'rel'
    """
    relative = _relative_variables(platform=platform, env=env)
    return env[relative[0]] if relative else ""


def _relative_base_remedy(*, platform: str, env: Mapping[str, str]) -> str:
    """What to set so the user directory becomes absolute, naming each variable that is relative.

    Every candidate the platform builds its user directory from is named when it
    is set to a relative path, not only the one the directory came from: from
    lib_layered_config 8.0.0 on, a relative ``APPDATA`` is refused even when the
    directory comes from ``LOCALAPPDATA``, so fixing one of two relative
    variables could leave the next run refused on the other.

    Args:
        platform: The resolver's platform, which decides the candidate variables.
        env: The environment the resolver read.

    Returns:
        The remedy clause of the refusal.

    Examples:
        >>> _relative_base_remedy(platform="darwin", env={"HOME": "rel"})
        'set HOME to an absolute path'
        >>> _relative_base_remedy(platform="linux", env={"XDG_CONFIG_HOME": "rel"})
        'set XDG_CONFIG_HOME to an absolute path or unset it'
        >>> _relative_base_remedy(platform="win32", env={"APPDATA": "a", "LOCALAPPDATA": "b"})
        'set APPDATA and LOCALAPPDATA to absolute paths'
    """
    candidates = _candidates(platform)
    relative = _relative_variables(platform=platform, env=env)
    if not relative:
        return f"set {' or '.join(candidates)} to an absolute path"
    if len(relative) == 1:
        suffix = " or unset it" if relative[0] == _UNSETTABLE_BASE else ""
        return f"set {relative[0]} to an absolute path{suffix}"
    suffix = f" ({_UNSETTABLE_BASE} may be unset instead)" if _UNSETTABLE_BASE in relative else ""
    return f"set {' and '.join(relative)} to absolute paths{suffix}"


def _deployment_for(resolver: DefaultPathResolver) -> DeploymentStrategy | None:
    """The deploy strategy for the resolver's platform, or ``None`` where the loader reads no user layer.

    Keyed on the resolver's own read strategy, so a platform the loader reads no
    user file on gets no path to write one to either.
    """
    if reader_strategy(resolver) is None:
        return None
    if resolver.platform.startswith("win"):
        return WindowsDeployment(resolver)
    if resolver.platform == "darwin":
        return MacDeployment(resolver)
    return LinuxDeployment(resolver)


def disable_offer() -> Path:
    """Set ``display.offer_desktop_shortcut = false`` in the user configuration.

    The file is edited in place when it exists and created when it does not; in
    either case every other key and comment is left exactly as it was. The write
    is atomic, so a failure leaves the previous file intact.

    Where ``config.toml`` is a symlink, the file it leads to is edited and the
    link is kept.

    Returns:
        The path written: the link's target where ``config.toml`` is a link.

    Raises:
        NoUserConfigPathError: No user configuration file can be named here
            (see :func:`user_config_path`); nothing is written.
        OSError: The file could not be written - a full disk, a permission wall,
            or a destination that is not a regular file. The previous file, if
            any, is untouched.
        DisplayNotATableError: The file holds ``display`` as something other
            than a table; nothing is written.
        tomlkit.exceptions.ParseError: The file exists but is not valid TOML
            (a reader hand-edited it), so there is nothing safe to edit in place;
            nothing is written.
        tomllib.TOMLDecodeError: The written file did not round-trip back to valid
            TOML, caught here at the write rather than later as an unreadable layer.
    """
    # Resolved for the write only: a rename replaces a symlink rather than
    # following it, and a reader's dotfile-manager link must survive the opt-out.
    path = user_config_path().resolve()
    document = _load_or_new(path)
    _set_offer_false(document, path=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_atomically(path, tomlkit.dumps(document), mode=_USER_FILE_MODE)
    # Read the written file back so a malformed round-trip fails here, at the
    # write, rather than surfacing later as an unreadable configuration layer.
    tomllib.loads(path.read_text(encoding="utf-8"))
    return path


def _load_or_new(path: Path) -> tomlkit.TOMLDocument:
    """The existing file as an editable document, or a fresh empty one."""
    if path.exists():
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    return tomlkit.document()


def _set_offer_false(document: tomlkit.TOMLDocument, *, path: Path) -> None:
    """Set the opt-out key false, adding a ``[display]`` table if there is none.

    The document is read as the mapping it is - tomlkit's own ``get`` types its
    result loosely, so it is reached through the standard mapping interface for
    a value the type checker can follow.

    Raises:
        DisplayNotATableError: ``display`` is there but is not a table - an
            ``[[display]]`` array of tables, a number, a list. Replacing it with a
            table would discard what the reader wrote, so nothing is changed.
    """
    container = cast("MutableMapping[str, Any]", document)
    if DISPLAY_SECTION not in container:
        container[DISPLAY_SECTION] = tomlkit.table()
    display = container[DISPLAY_SECTION]
    if not isinstance(display, MutableMapping):
        raise DisplayNotATableError(
            f"{DISPLAY_SECTION!r} in {path} is not a table, so the opt-out was not written; the file is unchanged"
        )
    cast("MutableMapping[str, Any]", display)[_OFFER_KEY] = False


__all__ = ["DisplayNotATableError", "NoUserConfigPathError", "disable_offer", "user_config_path"]
