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
from pathlib import Path
from typing import TYPE_CHECKING

from lsdsk.adapters.atomicfile import replace_atomically

if TYPE_CHECKING:
    from collections.abc import Sequence

_FILENAME = "lsdsk.command"
#: Executable for everyone, which a ``.command`` needs to run on double-click.
_EXECUTABLE_MODE = 0o755


class MacosDesktopShortcut:
    """Create and find the ``lsdsk.command`` launcher on the reader's Desktop."""

    def desktop_dir(self) -> Path:
        """The Desktop directory (macOS has no XDG override)."""
        return Path.home() / "Desktop"

    def has_desktop(self) -> bool:
        """Whether the Desktop folder exists; it is never created here."""
        return self.desktop_dir().is_dir()

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

        Raises:
            FileNotFoundError: When the Desktop folder does not exist; it is
                never created here.
        """
        del icon
        path = self.desktop_dir() / _FILENAME
        # Atomic and never through a symlink at the destination: see linux._write_executable.
        replace_atomically(path, f"#!/bin/sh\nexec {shlex.join(launch)}\n", mode=_EXECUTABLE_MODE)
        return path


__all__ = ["MacosDesktopShortcut"]
