"""The Linux desktop shortcut: an executable ``.desktop`` that runs lsdsk in a terminal.

lsdsk reads SMART and link data through passthrough commands that need root, so
the interactive view self-elevates with ``sudo`` - which needs a terminal to ask
in. The entry is therefore ``Terminal=true``. It is written to the Desktop, made
executable (a desktop environment refuses to launch a ``.desktop`` that is not),
and also to the application menu so it is found by name. The Desktop entry is
the shortcut; the menu copy is the extra, so a menu that will not take one is a
warning about a shortcut that does exist rather than a failure to make one.

The Desktop is the folder the desktop environment itself names in
``user-dirs.dirs`` (``~/Schreibtisch`` on a German one), not always
``~/Desktop``. An account with no such folder - a headless server - has no
desktop, and this adapter never creates one: a folder conjured on a machine
nobody looks at graphically holds a shortcut nobody can double-click.

System Role:
    Adapter-layer file-writing boundary; satisfies
    :class:`~lsdsk.application.ports.DesktopShortcuts` on Linux.
"""

from __future__ import annotations

import errno
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from lsdsk.adapters.atomicfile import replace_atomically
from lsdsk.adapters.shortcut.probe import probe
from lsdsk.adapters.textfile import read_text_bounded
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.shortcut import SHORTCUT_COMMENT, ShortcutWritten

if TYPE_CHECKING:
    from collections.abc import Sequence

_FILENAME = "lsdsk.desktop"
_NAME = "lsdsk"
#: Executable for everyone, as a desktop environment requires before it launches
#: an entry; readable for everyone, since it holds no secret.
_EXECUTABLE_MODE = 0o755

#: The desktop line ``xdg-user-dirs-update`` writes. The spec allows only
#: ``"$HOME/<path>"`` or an absolute path, always double-quoted.
_DESKTOP_LINE = re.compile(r'XDG_DESKTOP_DIR="(?P<value>[^"]*)"')
_HOME_PREFIX = "$HOME"

#: The characters a backslash must escape inside a double-quoted ``Exec``
#: argument, per the Desktop Entry spec's "The Exec key" section.
_QUOTED_ESCAPES = frozenset('"`$\\')

#: The C0 controls and DEL: no desktop-entry value can carry one, and a newline
#: would end the key and start another.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


class UnwritableDesktopEntryError(OSError):
    """A launch argument or icon path holds a control character no desktop entry value can carry."""


class LinuxDesktopShortcut:
    """Create and find the ``lsdsk.desktop`` launcher under the reader's home."""

    def __init__(self, *, config_home: Path | None = None) -> None:
        """Read ``user-dirs.dirs`` from ``config_home``.

        Args:
            config_home: Where ``user-dirs.dirs`` lives; ``$XDG_CONFIG_HOME`` or
                ``~/.config`` when not given.
        """
        self._config_home = config_home

    def desktop_dir(self) -> Path:
        """The Desktop directory: ``$XDG_DESKTOP_DIR``, then ``user-dirs.dirs``, then ``~/Desktop``."""
        override = os.environ.get("XDG_DESKTOP_DIR")
        if override:
            return Path(override)
        return self._named_in_user_dirs() or Path.home() / "Desktop"

    def has_desktop(self) -> bool:
        """Whether the Desktop folder exists; ``"$HOME/"`` in ``user-dirs.dirs`` turns it off."""
        desktop = self.desktop_dir()
        return probe(desktop.is_dir) and desktop != Path.home()

    def exists(self) -> bool:
        """Whether the Desktop already carries the shortcut."""
        return probe((self.desktop_dir() / _FILENAME).is_file)

    def no_desktop_reason(self) -> str:
        """Why there is no desktop: a missing folder, or a setting that names home itself.

        ``"$HOME/"`` in ``user-dirs.dirs`` is how the spec turns the desktop off,
        and home exists, so naming home as a missing folder told the reader
        something false and pointed at the wrong fix.
        """
        desktop = self.desktop_dir()
        if desktop != Path.home():
            return f"no desktop folder at {desktop}"
        if os.environ.get("XDG_DESKTOP_DIR"):
            return "XDG_DESKTOP_DIR names the home folder itself, which turns the desktop off"
        return f"{self._user_dirs_file()} turns the desktop off: its XDG_DESKTOP_DIR is the home folder itself"

    def _user_dirs_file(self) -> Path:
        """The ``user-dirs.dirs`` this account's desktop environment writes."""
        config_home = self._config_home or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return config_home / "user-dirs.dirs"

    def _named_in_user_dirs(self) -> Path | None:
        """The desktop folder ``user-dirs.dirs`` names, or None where it names none it could mean."""
        try:
            # replace: a path that will not decode names no folder that exists, which is answer enough.
            text = read_text_bounded(self._user_dirs_file(), what="the XDG user-dirs file", errors="replace")
        except ConfigurationError:
            return None
        for line in text.splitlines():
            found = _DESKTOP_LINE.fullmatch(line.strip())
            if found is not None:
                return _expand(found["value"])
        return None

    def create(self, *, launch: Sequence[str], icon: Path | None) -> ShortcutWritten:
        """Write the entry to the Desktop and the application menu, both executable.

        A menu copy that cannot be written is named in the result's ``skipped``
        and does not fail the call, because the Desktop entry is already there.

        Args:
            launch: The command the entry runs, as an argv.
            icon: An icon file to reference, or ``None`` for the default icon.

        Returns:
            The Desktop entry, and the menu copy in ``skipped`` when it failed.

        Raises:
            FileNotFoundError: When the Desktop folder does not exist; it is
                never created here, so nothing is written at all.
            OSError: When the Desktop entry itself could not be written.
        """
        body = self._entry(launch, icon)
        desktop = self.desktop_dir() / _FILENAME
        _write_executable(desktop, body)
        missed = self._add_to_the_menu(body)
        return ShortcutWritten(path=desktop, skipped=() if missed is None else (missed,))

    def _add_to_the_menu(self, body: str) -> str | None:
        """Write ``body`` into the application menu too; the sentence saying why it would not go, or None."""
        menu = self._applications_dir() / _FILENAME
        try:
            menu.parent.mkdir(parents=True, exist_ok=True)
            _write_executable(menu, body)
        except OSError as exc:
            return f"the shortcut is on the Desktop, but it could not be added to the application menu at {menu}: {exc}"
        return None

    @staticmethod
    def _applications_dir() -> Path:
        """The per-user application-menu directory, honouring ``XDG_DATA_HOME``."""
        data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(data_home) if data_home else Path.home() / ".local" / "share"
        return base / "applications"

    @staticmethod
    def _entry(launch: Sequence[str], icon: Path | None) -> str:
        """The ``.desktop`` file body for ``launch``, with ``icon`` when given.

        Raises:
            UnwritableDesktopEntryError: An argument or the icon path holds a
                control character, which no value in the file can carry.
        """
        lines = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={_NAME}",
            f"Comment={SHORTCUT_COMMENT}",
            f"Exec={_exec_value(launch)}",
            "Terminal=true",
            "Categories=System;Monitor;",
        ]
        if icon is not None:
            lines.insert(4, f"Icon={_string_value(str(icon))}")
        return "\n".join(lines) + "\n"


def _exec_value(launch: Sequence[str]) -> str:
    """``launch`` as the spec's ``Exec`` value: every argument double-quoted, then string-escaped.

    Each argument is quoted whole, so no reserved character (a space, ``'``,
    ``#``, ``;`` and the rest) is ever outside a quote, and inside the quotes a
    backslash escapes the four characters that need it. A literal ``%`` is
    written ``%%``, so nothing in a path reads as a field code. The result is
    then a ``string`` value like any other, whose own rule doubles a backslash -
    which is why a literal backslash ends up as four in the file. A shell's
    single quotes (``shlex.join``) are not quoting at all here.
    """
    return _string_value(" ".join(_exec_argument(argument) for argument in launch))


def _exec_argument(argument: str) -> str:
    """One ``Exec`` argument, ``%`` doubled and double-quoted with its reserved characters escaped."""
    escaped = "".join(f"\\{character}" if character in _QUOTED_ESCAPES else character for character in argument)
    return '"' + escaped.replace("%", "%%") + '"'


def _string_value(value: str) -> str:
    """``value`` as a Desktop Entry ``string``: a backslash doubled, a control character refused.

    Raises:
        UnwritableDesktopEntryError: ``value`` holds a control character. A
            newline would end the key and start one the reader never wrote, and
            refusing is the honest answer for the rest: a path is not text a
            launcher should be quietly rewriting.
    """
    if _CONTROL_CHARACTER.search(value) is not None:
        raise UnwritableDesktopEntryError(
            errno.EINVAL, f"{value!r} holds a control character, which a desktop entry cannot carry"
        )
    return value.replace("\\", "\\\\")


def _expand(value: str) -> Path | None:
    """A ``user-dirs.dirs`` value as a path: ``$HOME`` expanded, anything else the spec forbids dropped."""
    if value == _HOME_PREFIX or value.startswith(_HOME_PREFIX + "/"):
        return Path.home() / value[len(_HOME_PREFIX) :].lstrip("/")
    return Path(value) if value.startswith("/") else None


def _write_executable(path: Path, body: str) -> None:
    """Replace ``path`` with ``body``, executable, through the shared atomic writer.

    The rename never follows a symlink planted at the destination, so a link
    there is replaced rather than having its target overwritten and made
    executable, and a failed write leaves the previous entry as it was.
    """
    replace_atomically(path, body, mode=_EXECUTABLE_MODE)


__all__ = ["LinuxDesktopShortcut", "UnwritableDesktopEntryError"]
