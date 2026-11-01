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
    * :func:`executable_on_path` - an executable in an absolute ``PATH`` directory, or ``None``.
    * :func:`uvx_path` - where ``uvx`` resolves, or ``None``.
    * :func:`install_uv` - run the pinned official installer, then re-resolve ``uvx``.
    * :class:`UvInstallResult` - the installer's exit code and where ``uvx`` resolves after it.
"""

from __future__ import annotations

import os
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


def executable_on_path(name: str, *, search: str | None = None) -> Path | None:
    """The executable ``name`` in the first ABSOLUTE ``PATH`` directory holding it, or ``None``.

    What this answers becomes the target of a shortcut that, on Windows, runs as
    administrator on every double-click. ``shutil.which(name)`` searches the
    current directory first on Windows, and resolves a relative ``PATH`` entry
    (``.``, an empty entry, ``bin``) against the current directory everywhere, so
    an executable planted in the folder lsdsk was started from would become that
    target. Only an absolute directory names the same file wherever lsdsk runs,
    so each one is asked about directly: a name that carries a directory makes
    ``which`` check that one place, with no current-directory insert, while
    Windows' ``PATHEXT`` still applies.

    Args:
        name: The executable's bare name, such as ``uvx``.
        search: The ``PATH`` value to search; the process's own when ``None``.

    Returns:
        The absolute path of the first match, or ``None``.

    Examples:
        >>> executable_on_path("uvx", search="") is None
        True
    """
    entries = os.environ.get("PATH", "") if search is None else search
    for entry in entries.split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        found = shutil.which(str(directory / name))
        if found:
            return Path(found)
    return None


def uvx_path(*, search: str | None = None) -> Path | None:
    """Where ``uvx`` resolves on this machine, or ``None`` when it is not installed.

    Args:
        search: The ``PATH`` value to search; the process's own when ``None``.

    Returns:
        The absolute path of ``uvx`` in an absolute ``PATH`` directory (see
        :func:`executable_on_path`), or ``None``.
    """
    return executable_on_path("uvx", search=search)


#: Fetch the installer named by ``$1`` and run it only when the fetch succeeded,
#: leaving with ``curl``'s exit code when it did not.
_FETCH_THEN_RUN = 'script=$(curl -LsSf "$1") || exit $?; printf "%s\\n" "$script" | sh'

#: The script's ``$0``, which the shell puts in front of every error it reports
#: itself (``lsdsk-uv-installer: 1: curl: not found``). With the URL there, a
#: missing ``curl`` was reported under the download's address, which reads as
#: the URL being what was not found.
_SCRIPT_NAME = "lsdsk-uv-installer"


def _installer_command(platform: str) -> list[str]:
    """The pinned official-installer invocation for ``platform``.

    The network fetch is the installer's own (``curl``/``irm``); Python opens no
    socket. The URL is a module constant, so nothing a reader supplies reaches
    the shell; on POSIX it is handed to the script as ``$1`` rather than written
    into it, and ``$0`` names the step for the shell's own error lines.

    On POSIX the script is fetched first and run second. ``curl | sh`` reports
    the exit code of ``sh``, and a failed download hands ``sh`` an empty script,
    which exits 0: the run read as an install that worked and merely left
    ``uvx`` off PATH. Fetched into a variable, ``curl``'s own failure is the
    command's exit code.
    """
    if platform == "win32":
        return ["powershell", "-ExecutionPolicy", "ByPass", "-c", f"irm {UV_INSTALLER_URL_WINDOWS} | iex"]
    return ["sh", "-c", _FETCH_THEN_RUN, _SCRIPT_NAME, UV_INSTALLER_URL_POSIX]


#: The process's own standard-error descriptor. The installer's standard output
#: is sent there because lsdsk's standard output carries the ``--format json``
#: envelope that follows the install: inherited, the installer's progress lines
#: came first and the envelope no longer parsed. The descriptor rather than
#: ``sys.stderr``, which a test harness or a redirect replaces with an object
#: that has no descriptor at all.
_STANDARD_ERROR = 2


def run_installer(command: Sequence[str]) -> int:
    """Run ``command`` to completion and return its exit code, its output on standard error.

    A failing installer is an exit code here, never an exception (``check`` is
    off), so the caller can say which code it was.

    Args:
        command: The argv to run.

    Returns:
        The command's exit code.
    """
    # Fixed argv built from pinned-constant installer URLs, no user input reaches it;
    # subprocess here is the one allow-listed shell-out (see the module docstring).
    completed = subprocess.run(list(command), check=False, stdout=_STANDARD_ERROR)  # noqa: S603  # nosec B603
    return completed.returncode


def install_uv(
    *,
    run: Callable[[Sequence[str]], int] = run_installer,
    platform: str = sys.platform,
    search: str | None = None,
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
        search: The ``PATH`` value ``uvx`` is looked up in afterwards; the
            process's own when ``None``.

    Returns:
        The installer's exit code and the path ``uvx`` resolves to after it.
    """
    exit_code = run(_installer_command(platform))
    return UvInstallResult(exit_code=exit_code, uvx=uvx_path(search=search))


__all__ = ["UvInstallResult", "executable_on_path", "install_uv", "run_installer", "uvx_path"]
