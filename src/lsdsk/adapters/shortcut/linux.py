"""The Linux desktop shortcut: an executable ``.desktop`` that runs lsdsk in a terminal.

lsdsk reads SMART and link data through passthrough commands that need root, so
the interactive view self-elevates with ``sudo`` - which needs a terminal to ask
in. The entry is therefore ``Terminal=true``. It is written to the Desktop, made
executable (a desktop environment refuses to launch a ``.desktop`` that is not),
and also to the application menu so it is found by name.

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

import os
import re
import shlex
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from lsdsk.adapters.textfile import read_text_bounded
from lsdsk.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Sequence

_FILENAME = "lsdsk.desktop"
_NAME = "lsdsk"
_COMMENT = "See your disks and controllers, and what is wrong with how they are connected"
_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH

#: The desktop line ``xdg-user-dirs-update`` writes. The spec allows only
#: ``"$HOME/<path>"`` or an absolute path, always double-quoted.
_DESKTOP_LINE = re.compile(r'XDG_DESKTOP_DIR="(?P<value>[^"]*)"')
_HOME_PREFIX = "$HOME"


class LinuxDesktopShortcut:
    """Create and find the ``lsdsk.desktop`` launcher under the reader's home."""

    def __init__(self, *, config_home: Path | None = None) -> None:
        """Read ``user-dirs.dirs`` from ``config_home``; ``$XDG_CONFIG_HOME`` or ``~/.config`` when not given."""
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
        return desktop.is_dir() and desktop != Path.home()

    def exists(self) -> bool:
        """Whether the Desktop already carries the shortcut."""
        return (self.desktop_dir() / _FILENAME).is_file()

    def _named_in_user_dirs(self) -> Path | None:
        """The desktop folder ``user-dirs.dirs`` names, or None where it names none it could mean."""
        config_home = self._config_home or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        try:
            # replace: a path that will not decode names no folder that exists, which is answer enough.
            text = read_text_bounded(config_home / "user-dirs.dirs", what="the XDG user-dirs file", errors="replace")
        except ConfigurationError:
            return None
        for line in text.splitlines():
            found = _DESKTOP_LINE.fullmatch(line.strip())
            if found is not None:
                return _expand(found["value"])
        return None

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        """Write the entry to the Desktop and the application menu, both executable.

        Args:
            launch: The command the entry runs, as an argv.
            icon: An icon file to reference, or ``None`` for the default icon.

        Returns:
            The path of the Desktop entry.

        Raises:
            FileNotFoundError: When the Desktop folder does not exist; it is
                never created here, so nothing is written at all.
        """
        body = self._entry(launch, icon)
        desktop = self.desktop_dir() / _FILENAME
        menu = self._applications_dir() / _FILENAME
        _write_executable(desktop, body)
        menu.parent.mkdir(parents=True, exist_ok=True)
        _write_executable(menu, body)
        return desktop

    @staticmethod
    def _applications_dir() -> Path:
        """The per-user application-menu directory, honouring ``XDG_DATA_HOME``."""
        data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(data_home) if data_home else Path.home() / ".local" / "share"
        return base / "applications"

    @staticmethod
    def _entry(launch: Sequence[str], icon: Path | None) -> str:
        """The ``.desktop`` file body for ``launch``, with ``icon`` when given."""
        lines = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={_NAME}",
            f"Comment={_COMMENT}",
            f"Exec={shlex.join(launch)}",
            "Terminal=true",
            "Categories=System;Utility;",
        ]
        if icon is not None:
            lines.insert(4, f"Icon={icon}")
        return "\n".join(lines) + "\n"


def _expand(value: str) -> Path | None:
    """A ``user-dirs.dirs`` value as a path: ``$HOME`` expanded, anything else the spec forbids dropped."""
    if value == _HOME_PREFIX or value.startswith(_HOME_PREFIX + "/"):
        return Path.home() / value[len(_HOME_PREFIX) :].lstrip("/")
    return Path(value) if value.startswith("/") else None


def _write_executable(path: Path, body: str) -> None:
    """Write ``body`` to ``path`` and set every execute bit a desktop environment checks."""
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | _EXECUTABLE_BITS)


__all__ = ["LinuxDesktopShortcut"]
