"""The Linux desktop shortcut: an executable ``.desktop`` that runs lsdsk in a terminal.

lsdsk reads SMART and link data through passthrough commands that need root, so
the interactive view self-elevates with ``sudo`` - which needs a terminal to ask
in. The entry is therefore ``Terminal=true``. It is written to the Desktop, made
executable (a desktop environment refuses to launch a ``.desktop`` that is not),
and also to the application menu so it is found by name.

System Role:
    Adapter-layer file-writing boundary; satisfies
    :class:`~lsdsk.application.ports.DesktopShortcuts` on Linux.
"""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_FILENAME = "lsdsk.desktop"
_NAME = "lsdsk"
_COMMENT = "See your disks and controllers, and what is wrong with how they are connected"
_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


class LinuxDesktopShortcut:
    """Create and find the ``lsdsk.desktop`` launcher under the reader's home."""

    def desktop_dir(self) -> Path:
        """The Desktop directory, honouring ``XDG_DESKTOP_DIR`` when it is set."""
        override = os.environ.get("XDG_DESKTOP_DIR")
        return Path(override) if override else Path.home() / "Desktop"

    def exists(self) -> bool:
        """Whether the Desktop already carries the shortcut."""
        return (self.desktop_dir() / _FILENAME).is_file()

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        """Write the entry to the Desktop and the application menu, both executable.

        Args:
            launch: The command the entry runs, as an argv.
            icon: An icon file to reference, or ``None`` for the default icon.

        Returns:
            The path of the Desktop entry.
        """
        body = self._entry(launch, icon)
        desktop = self.desktop_dir() / _FILENAME
        menu = self._applications_dir() / _FILENAME
        for path in (desktop, menu):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            path.chmod(path.stat().st_mode | _EXECUTABLE_BITS)
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


__all__ = ["LinuxDesktopShortcut"]
