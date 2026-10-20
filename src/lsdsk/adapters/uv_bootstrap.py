"""Detect ``uvx`` and, on the reader's explicit consent, install uv for them.

This is the single module in lsdsk that starts a subprocess and reaches the
network, and it does so on one path only: a reader who accepted the desktop
shortcut, found to have no ``uvx``, and who then answered yes to an install
prompt. Everywhere else the tool shells out to nothing and opens no socket, and
``tests/test_no_subprocess_or_network.py`` confines this exception to this file.

The network request is made by the official installer lsdsk spawns (``curl`` on
POSIX, PowerShell on Windows), not by Python, so the only forbidden import here
is ``subprocess``. The installer is version-pinned, so a reader always runs a
known script rather than whatever is newest.

System Role:
    Adapter-layer process/network boundary, allow-listed and consent-gated.

Contents:
    * :func:`uvx_path` - where ``uvx`` resolves, or ``None``.
    * :func:`install_uv` - run the pinned official installer, then re-resolve ``uvx``.
"""

from __future__ import annotations

import shutil
import subprocess  # the one allow-listed shell-out; see the module docstring and the guard test
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: The uv release the official installer is pinned to. Pinned so a reader runs a
#: known installer rather than the newest one unseen; bump it deliberately as uv
#: releases, the way a dependency floor is bumped.
UV_INSTALLER_VERSION: Final = "0.13.0"

_POSIX_INSTALLER: Final = f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.sh"
_WINDOWS_INSTALLER: Final = f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.ps1"


def uvx_path(*, which: Callable[[str], str | None] = shutil.which) -> Path | None:
    """Where ``uvx`` resolves on this machine, or ``None`` when it is not installed.

    Args:
        which: The lookup, injectable so a test need not alter the real ``PATH``.

    Returns:
        The absolute path of ``uvx``, or ``None``.
    """
    found = which("uvx")
    return Path(found) if found else None


def _installer_command(platform: str) -> list[str]:
    """The pinned official-installer invocation for ``platform``.

    The network fetch is the installer's own (``curl``/``irm``); Python opens no
    socket. The URL is a module constant, so nothing a reader supplies reaches
    the shell.
    """
    if platform == "win32":
        return ["powershell", "-ExecutionPolicy", "ByPass", "-c", f"irm {_WINDOWS_INSTALLER} | iex"]
    return ["sh", "-c", f"curl -LsSf {_POSIX_INSTALLER} | sh"]


def _subprocess_run(command: Sequence[str]) -> int:
    """Run ``command`` to completion and return its exit code."""
    completed = subprocess.run(list(command), check=False)  # noqa: S603 - fixed argv, pinned-constant URL, no user input
    return completed.returncode


def install_uv(
    *,
    run: Callable[[Sequence[str]], int] = _subprocess_run,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Run the pinned official uv installer, then report whether ``uvx`` is now resolvable.

    The caller gates this on the reader's consent; it is never reached without a
    yes. The installer puts ``uvx`` on ``PATH``, so the return value - the path
    re-resolved after the run - is the signal the caller needs to decide whether
    the shortcut can now be created.

    Args:
        run: Executes a command and returns its exit code; injectable for tests.
        platform: The platform whose installer is chosen; defaults to this one.
        which: The ``uvx`` lookup, injectable for tests.

    Returns:
        The path ``uvx`` resolves to after the install, or ``None`` when the
        install left it unresolvable.
    """
    run(_installer_command(platform))
    return uvx_path(which=which)


__all__ = ["UV_INSTALLER_VERSION", "install_uv", "uvx_path"]
