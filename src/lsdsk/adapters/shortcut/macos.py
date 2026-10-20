"""The macOS desktop shortcut: an executable ``.command`` that runs lsdsk in Terminal.

A ``.command`` file opens Terminal and runs its contents on double-click, which
is what lsdsk needs: it reads SMART and link data through passthrough commands
that want root, so the interactive view self-elevates with ``sudo`` - and sudo
needs a terminal to ask in.

System Role:
    Adapter-layer file-writing boundary; satisfies
    :class:`~lsdsk.application.ports.DesktopShortcuts` on macOS.
"""

from __future__ import annotations

import shlex
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_FILENAME = "lsdsk.command"
_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


class MacosDesktopShortcut:
    """Create and find the ``lsdsk.command`` launcher on the reader's Desktop."""

    def desktop_dir(self) -> Path:
        """The Desktop directory (macOS has no XDG override)."""
        return Path.home() / "Desktop"

    def exists(self) -> bool:
        """Whether the Desktop already carries the launcher."""
        return (self.desktop_dir() / _FILENAME).is_file()

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        """Write the ``.command`` and make it executable.

        Args:
            launch: The command the shortcut runs, as an argv.
            icon: Accepted for the port, but a ``.command`` carries no icon, so
                it is unused here (the ``.app`` form that could is a later step).

        Returns:
            The path of the ``.command`` file.
        """
        del icon
        path = self.desktop_dir() / _FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\nexec {shlex.join(launch)}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | _EXECUTABLE_BITS)
        return path


__all__ = ["MacosDesktopShortcut"]
