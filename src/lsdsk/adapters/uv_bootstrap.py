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
    * :class:`UvInstallResult` - the installer's exit code and where ``uvx`` resolves after it.
"""

from __future__ import annotations

import shutil

# subprocess is the one allow-listed shell-out here; see the module docstring and the guard test.
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from lsdsk.domain.shortcut import UV_INSTALLER_URL_POSIX, UV_INSTALLER_URL_WINDOWS

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class UvInstallResult(NamedTuple):
    """What an installer run came to.

    Attributes:
        exit_code: The installer's own exit code; anything but 0 is a failed
            install, whatever ``uvx`` resolves to afterwards.
        uvx: Where ``uvx`` resolves after the run, or ``None``.
    """

    exit_code: int
    uvx: Path | None


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
        return ["powershell", "-ExecutionPolicy", "ByPass", "-c", f"irm {UV_INSTALLER_URL_WINDOWS} | iex"]
    return ["sh", "-c", f"curl -LsSf {UV_INSTALLER_URL_POSIX} | sh"]


def _subprocess_run(command: Sequence[str]) -> int:
    """Run ``command`` to completion and return its exit code."""
    # Fixed argv built from pinned-constant installer URLs, no user input reaches it;
    # subprocess here is the one allow-listed shell-out (see the module docstring).
    completed = subprocess.run(list(command), check=False)  # noqa: S603  # nosec B603
    return completed.returncode


def install_uv(
    *,
    run: Callable[[Sequence[str]], int] = _subprocess_run,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
) -> UvInstallResult:
    """Run the pinned official uv installer, then report its exit code and whether ``uvx`` resolves.

    The caller gates this on the reader's consent; it is never reached without a
    yes. Both halves are returned because they answer different questions: the
    exit code says whether the installer worked, and the re-resolved path says
    whether THIS shell can see what it installed - a successful install whose
    ``uvx`` a new shell will pick up is not a failed one.

    Args:
        run: Executes a command and returns its exit code; injectable for tests.
        platform: The platform whose installer is chosen; defaults to this one.
        which: The ``uvx`` lookup, injectable for tests.

    Returns:
        The installer's exit code and the path ``uvx`` resolves to after it.
    """
    exit_code = run(_installer_command(platform))
    return UvInstallResult(exit_code=exit_code, uvx=uvx_path(which=which))


__all__ = ["UvInstallResult", "install_uv", "uvx_path"]
