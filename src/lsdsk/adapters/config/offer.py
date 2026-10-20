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
from typing import TYPE_CHECKING, Any, cast

import tomlkit
from lib_layered_config import deploy_config_paths

from lsdsk import __init__conf__
from lsdsk.adapters.atomicfile import replace_atomically
from lsdsk.adapters.config.loader import get_default_config_path
from lsdsk.adapters.config.tunables import DISPLAY_SECTION

if TYPE_CHECKING:
    from pathlib import Path

#: The key the offer writes, the same name the model and the shipped file carry.
_OFFER_KEY = "offer_desktop_shortcut"

#: The user layer's own file, as opposed to the ``config.d`` dropins beside it.
_USER_CONFIG_NAME = "config.toml"

#: A user file may hold secrets in other keys, so it is owner-only like the store.
_USER_FILE_MODE = 0o600


def user_config_path() -> Path:
    """The user ``config.toml`` the loader reads, whether or not it exists yet.

    Resolved through the same library call the deploy uses, so it lands in the
    one place this platform's user layer is read from - Linux ``~/.config``,
    macOS ``~/Library/Application Support``, Windows ``%APPDATA%`` - rather than
    a path guessed here.

    Returns:
        The absolute path of the user configuration file.

    Raises:
        FileNotFoundError: The shipped default configuration is missing, so the
            library cannot derive the companion paths.
    """
    paths = deploy_config_paths(
        get_default_config_path(),
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        profile=None,
        targets=["user"],
    )
    return next(path for path in paths if path.name == _USER_CONFIG_NAME)


def disable_offer() -> Path:
    """Set ``display.offer_desktop_shortcut = false`` in the user configuration.

    The file is edited in place when it exists and created when it does not; in
    either case every other key and comment is left exactly as it was. The write
    is atomic, so a failure leaves the previous file intact.

    Returns:
        The path written.

    Raises:
        OSError: The file could not be written - a full disk, a permission wall,
            or a destination that is not a regular file. The previous file, if
            any, is untouched.
        tomllib.TOMLDecodeError: The file exists but is not valid TOML, so there
            is nothing safe to edit in place; nothing is written.
    """
    path = user_config_path()
    document = _load_or_new(path)
    _set_offer_false(document)
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


def _set_offer_false(document: tomlkit.TOMLDocument) -> None:
    """Set the opt-out key false, adding a ``[display]`` table if there is none.

    The document is read as the mapping it is - tomlkit's own ``get`` types its
    result loosely, so it is reached through the standard mapping interface for
    a value the type checker can follow.
    """
    container = cast("MutableMapping[str, Any]", document)
    display = container.get(DISPLAY_SECTION)
    if not isinstance(display, MutableMapping):
        display = tomlkit.table()
        container[DISPLAY_SECTION] = display
    display[_OFFER_KEY] = False


__all__ = ["disable_offer", "user_config_path"]
