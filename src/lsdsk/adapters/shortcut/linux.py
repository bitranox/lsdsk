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

A relative ``XDG_DESKTOP_DIR``, ``XDG_DATA_HOME`` or ``XDG_CONFIG_HOME`` is
refused rather than resolved: it names a different folder wherever lsdsk is
started, so following it writes the shortcut into the current directory, or
reads a ``user-dirs.dirs`` planted there.

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
    """A launch argument or icon path holds a character no desktop entry value can carry.

    A control character, or one UTF-8 cannot encode: the lone surrogate Python
    reads a filename byte as when it is not valid UTF-8. It carries ``EINVAL`` as
    its ``errno`` and prints the refusal alone, without the ``[Errno 22]`` an
    ``OSError`` would put in front of a sentence written for the reader.
    """

    def __str__(self) -> str:
        """The refusal as written, with no errno in front of it."""
        return self.strerror if self.strerror is not None else super().__str__()


class RelativeXdgDirectoryError(OSError):
    """An XDG directory variable the shortcut depends on is set to a relative path."""


def _xdg_directory(variable: str) -> Path | None:
    """The absolute directory ``variable`` names, or ``None`` when it is unset or empty.

    Raises:
        RelativeXdgDirectoryError: ``variable`` is set to a relative path, which
            would be resolved against whatever folder lsdsk was started in.
    """
    value = os.environ.get(variable)
    if not value:
        return None
    if not Path(value).is_absolute():
        # The message alone, as NoUserConfigPathError carries it: an errno would put "[Errno 22]" in front of it.
        raise RelativeXdgDirectoryError(
            f"{variable}={value} is not an absolute path; set {variable} to an absolute path or unset it"
        )
    return Path(value)


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
        """The Desktop directory: ``$XDG_DESKTOP_DIR``, then ``user-dirs.dirs``, then ``~/Desktop``.

        Raises:
            RelativeXdgDirectoryError: ``XDG_DESKTOP_DIR``, or the
                ``XDG_CONFIG_HOME`` holding ``user-dirs.dirs``, is relative.
        """
        override = _xdg_directory("XDG_DESKTOP_DIR")
        if override is not None:
            return override
        return self._named_in_user_dirs() or Path.home() / "Desktop"

    def has_desktop(self) -> bool:
        """Whether the Desktop folder exists; ``"$HOME/"`` in ``user-dirs.dirs`` turns it off.

        The menu directory is checked here too, so a relative ``XDG_DATA_HOME``
        is refused before a caller installs anything for a shortcut it cannot write.

        Raises:
            RelativeXdgDirectoryError: An XDG directory the shortcut depends on is relative.
        """
        self._applications_dir()
        desktop = self.desktop_dir()
        return probe(desktop.is_dir) and desktop != Path.home()

    def exists(self) -> bool:
        """Whether the Desktop already carries the shortcut.

        Raises:
            RelativeXdgDirectoryError: The Desktop is named by a relative XDG directory.
        """
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
        if _xdg_directory("XDG_DESKTOP_DIR") is not None:
            return "XDG_DESKTOP_DIR names the home folder itself, which turns the desktop off"
        return f"{self._user_dirs_file()} turns the desktop off: its XDG_DESKTOP_DIR is the home folder itself"

    def _user_dirs_file(self) -> Path:
        """The ``user-dirs.dirs`` this account's desktop environment writes."""
        config_home = self._config_home or _xdg_directory("XDG_CONFIG_HOME") or Path.home() / ".config"
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
            RelativeXdgDirectoryError: When the Desktop or the menu is named by
                a relative XDG directory; nothing is written at all.
            OSError: When the Desktop entry itself could not be written.
        """
        body = self._entry(launch, icon)
        # Both destinations are named before either is written, so a relative
        # XDG_DATA_HOME refuses the whole shortcut rather than only its menu copy.
        menu = self._applications_dir() / _FILENAME
        desktop = self.desktop_dir() / _FILENAME
        _write_executable(desktop, body)
        missed = self._add_to_the_menu(menu, body)
        return ShortcutWritten(path=desktop, skipped=() if missed is None else (missed,))

    @staticmethod
    def _add_to_the_menu(menu: Path, body: str) -> str | None:
        """Write ``body`` to ``menu`` too; the sentence saying why it would not go, or None."""
        try:
            menu.parent.mkdir(parents=True, exist_ok=True)
            _write_executable(menu, body)
        except OSError as exc:
            return (
                "the shortcut is on the Desktop, but it could not be added to the application menu "
                f"at {menu}: {_reason_about(exc, menu)}"
            )
        return None

    @staticmethod
    def _applications_dir() -> Path:
        """The per-user application-menu directory, honouring ``XDG_DATA_HOME``.

        Raises:
            RelativeXdgDirectoryError: ``XDG_DATA_HOME`` is relative.
        """
        return (_xdg_directory("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"

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

    Each argument is checked on its own before they are joined, so a refusal
    names the one argument at fault rather than the whole line.

    Each argument is quoted whole, so no reserved character (a space, ``'``,
    ``#``, ``;`` and the rest) is ever outside a quote, and inside the quotes a
    backslash escapes the four characters that need it. A literal ``%`` is
    written ``%%``, so nothing in a path reads as a field code. The result is
    then a ``string`` value like any other, whose own rule doubles a backslash -
    which is why a literal backslash ends up as four in the file. A shell's
    single quotes (``shlex.join``) are not quoting at all here.
    """
    for argument in launch:
        _require_carriable(argument)
    return _escaped(" ".join(_exec_argument(argument) for argument in launch))


def _exec_argument(argument: str) -> str:
    """One ``Exec`` argument, ``%`` doubled and double-quoted with its reserved characters escaped."""
    escaped = "".join(f"\\{character}" if character in _QUOTED_ESCAPES else character for character in argument)
    return '"' + escaped.replace("%", "%%") + '"'


def _string_value(value: str) -> str:
    """``value`` as a Desktop Entry ``string``: a backslash doubled, an uncarriable character refused.

    Raises:
        UnwritableDesktopEntryError: ``value`` holds a character no value in the
            file can carry (see :func:`_require_carriable`).
    """
    _require_carriable(value)
    return _escaped(value)


def _escaped(value: str) -> str:
    """``value`` with each backslash doubled, as a Desktop Entry ``string`` spells one."""
    return value.replace("\\", "\\\\")


def _require_carriable(value: str) -> None:
    """Refuse ``value`` - one launch argument or the icon path - when the file cannot carry it.

    A newline would end the key and start one the reader never wrote; the file
    is UTF-8 by the spec, so a path byte that is not valid UTF-8 has no spelling
    in it; and refusing is the honest answer for the rest: a path is not text a
    launcher should be quietly rewriting. An undecodable value is quoted by its
    bytes, so the reader sees the byte itself rather than Python's surrogate.

    Raises:
        UnwritableDesktopEntryError: ``value`` holds a control character, or a
            character UTF-8 cannot encode.
    """
    if _CONTROL_CHARACTER.search(value) is not None:
        raise UnwritableDesktopEntryError(
            errno.EINVAL, f"{value!r} holds a control character, which a desktop entry cannot carry"
        )
    if not _encodes_as_utf8(value):
        raise UnwritableDesktopEntryError(
            errno.EINVAL, f"{os.fsencode(value)!r} is not valid UTF-8, which a desktop entry cannot carry"
        )


def _encodes_as_utf8(value: str) -> bool:
    """Whether ``value`` has a UTF-8 spelling; a lone surrogate from an undecodable filename byte has none."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _reason_about(error: OSError, path: Path) -> str:
    """Why ``error`` refused ``path``, for a sentence that has already named ``path``.

    The atomic writer names the destination in the refusal it raises, so the
    refusal's own text would repeat the path the sentence just gave. Where the
    refusal names that same file and no second one, its reason alone is said;
    any other refusal - a folder above it that could not be made - is quoted
    whole, because the path it names is not the one already given.
    """
    names_only_path = error.filename == str(path) and error.filename2 is None
    return error.strerror if names_only_path and error.strerror is not None else str(error)


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


__all__ = ["LinuxDesktopShortcut", "RelativeXdgDirectoryError", "UnwritableDesktopEntryError"]
