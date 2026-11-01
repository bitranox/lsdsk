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

# subprocess is the one allow-listed shell-out here; see the module docstring and the guard test.
import subprocess  # nosec B404
import sys
from enum import Enum
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
        missing_program: The program the installer command starts (``sh``,
            ``powershell``) when no absolute ``PATH`` directory holds it, so
            the installer never ran; ``None`` when it was started. Carried as
            its own value because the exit code alone cannot say that nothing
            ran: a shell reports a missing command with the same 127.
    """

    exit_code: int
    uvx: Path | None
    missing_program: str | None = None


class _ThisMachine(Enum):
    """The ``pathext`` default: whatever suffixes this machine's executables carry."""

    PATHEXT = "this machine's PATHEXT"


_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"
"""Windows' own ``PATHEXT`` when the environment carries none."""


def _pathext_for(platform: str) -> str | None:
    """The suffixes an executable carries on ``platform``: Windows' ``PATHEXT``, else ``None``.

    The installer passes the platform it installs for rather than this
    machine's, so its Windows rule is the same one on every runner.
    """
    if platform == "win32":
        return os.environ.get("PATHEXT") or _DEFAULT_PATHEXT
    return None


def _names_to_try(name: str, pathext: str | None) -> tuple[str, ...]:
    """The file names ``name`` may have on disk: itself, or itself plus each ``PATHEXT`` suffix.

    Examples:
        >>> _names_to_try("uvx", None)
        ('uvx',)
        >>> _names_to_try("uvx", ".COM;.EXE")
        ('uvx.COM', 'uvx.EXE')
        >>> _names_to_try("uvx.exe", ".COM;.EXE")
        ('uvx.exe',)
    """
    if pathext is None:
        return (name,)
    suffixes = [suffix for suffix in pathext.split(";") if suffix]
    if any(name.lower().endswith(suffix.lower()) for suffix in suffixes):
        return (name,)
    return tuple(name + suffix for suffix in suffixes)


def executable_on_path(
    name: str, *, search: str | None = None, pathext: str | _ThisMachine | None = _ThisMachine.PATHEXT
) -> Path | None:
    """The executable ``name`` in the first ABSOLUTE ``PATH`` directory holding it, or ``None``.

    What this answers becomes the target of a shortcut that, on Windows, runs as
    administrator on every double-click. ``shutil.which(name)`` searches the
    current directory first on Windows, and resolves a relative ``PATH`` entry
    (``.``, an empty entry, ``bin``) against the current directory everywhere, so
    an executable planted in the folder lsdsk was started from would become that
    target. Only an absolute directory names the same file wherever lsdsk runs,
    so each one is looked in directly, for the name and, on Windows, the name
    plus each ``PATHEXT`` suffix. ``which`` is not asked: handed a name that
    carries a directory, Python 3.11 checks it exactly as written and finds no
    ``uvx.exe`` for ``uvx``, and handed the directory as ``path`` it searches the
    current directory on Windows again.

    Args:
        name: The executable's bare name, such as ``uvx``.
        search: The ``PATH`` value to search; the process's own when ``None``.
        pathext: The ``;``-separated suffixes an executable may carry, ``None``
            for none; this machine's own (``PATHEXT`` on Windows) by default.

    Returns:
        The absolute path of the first match, or ``None``.

    Examples:
        >>> executable_on_path("uvx", search="") is None
        True
    """
    entries = os.environ.get("PATH", "") if search is None else search
    suffixes = _pathext_for(sys.platform) if pathext is _ThisMachine.PATHEXT else pathext
    names = _names_to_try(name, suffixes)
    for entry in _absolute_entries(entries):
        found = _executable_in(Path(entry), names)
        if found is not None:
            return found
    return None


def _absolute_entries(search: str) -> list[str]:
    """The entries of the ``PATH`` value ``search`` that name an absolute directory, in order.

    An empty entry, ``.`` or ``bin`` names a different folder wherever the
    process runs, so it is dropped; the launcher lookup and the installer's own
    ``PATH`` both keep only what this keeps.

    Examples:
        >>> import os
        >>> _absolute_entries(os.pathsep.join(["", ".", "bin"]))
        []
    """
    return [entry for entry in search.split(os.pathsep) if entry and Path(entry).is_absolute()]


def _executable_in(directory: Path, names: Sequence[str]) -> Path | None:
    """The first of ``names`` that is an executable file in ``directory``, or ``None``.

    A directory this account cannot enter answers ``None`` rather than raising:
    before Python 3.14 ``Path.is_file`` re-raises a refused ``stat``.
    """
    for candidate in names:
        path = directory / candidate
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return path
        except OSError:
            continue
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


#: The exit code of an installer whose program was not found, the one a shell
#: gives a command it cannot find. It only keeps ``exit_code`` non-zero;
#: :attr:`UvInstallResult.missing_program` is what says the installer never ran.
_PROGRAM_NOT_FOUND = 127


def _installer_program(platform: str) -> str:
    """The bare name of the program the installer command for ``platform`` starts."""
    return "powershell" if platform == "win32" else "sh"


def _installer_command(program: Path, platform: str) -> list[str]:
    """The pinned official-installer invocation for ``platform``, started as ``program``.

    ``program`` is the absolute path the installer's program was found at (see
    :func:`install_uv`), never a bare name: handed ``sh`` or ``powershell``,
    the operating system looks it up itself, through a relative ``PATH`` entry
    on POSIX and in the working directory first on Windows, so a file planted
    in the folder lsdsk ran in would be started in its place.

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
        return [str(program), "-ExecutionPolicy", "ByPass", "-c", f"irm {UV_INSTALLER_URL_WINDOWS} | iex"]
    return [str(program), "-c", _FETCH_THEN_RUN, _SCRIPT_NAME, UV_INSTALLER_URL_POSIX]


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

    The child's ``PATH`` holds only the absolute entries of this process's
    own, because the POSIX script starts ``curl`` and a second ``sh`` by name
    and a relative entry would let a file in the working directory answer for
    either. Only ``PATH`` is rewritten: the rest of the environment is passed
    on, since a replaced one drops ``SystemRoot``, without which a Windows
    child does not start.

    Args:
        command: The argv to run.

    Returns:
        The command's exit code.
    """
    environment = {**os.environ, "PATH": os.pathsep.join(_absolute_entries(os.environ.get("PATH", "")))}
    # Fixed argv built from pinned-constant installer URLs, no user input reaches it;
    # subprocess here is the one allow-listed shell-out (see the module docstring).
    completed = subprocess.run(list(command), check=False, stdout=_STANDARD_ERROR, env=environment)  # noqa: S603  # nosec B603
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

    The installer's own program is looked up the way the launcher is
    (:func:`executable_on_path`, in this process's ``PATH``), so only an
    absolute directory can supply it. Where none holds it nothing is started,
    and the result is a failed install naming that program.

    Args:
        run: Executes a command and returns its exit code; injectable for tests.
        platform: The platform whose installer is chosen; defaults to this one.
        search: The ``PATH`` value ``uvx`` is looked up in afterwards; the
            process's own when ``None``.

    Returns:
        The installer's exit code and the path ``uvx`` resolves to after it,
        with the program that was not found when the installer never ran.
    """
    name = _installer_program(platform)
    program = executable_on_path(name, pathext=_pathext_for(platform))
    if program is None:
        return UvInstallResult(exit_code=_PROGRAM_NOT_FOUND, uvx=uvx_path(search=search), missing_program=name)
    exit_code = run(_installer_command(program, platform))
    return UvInstallResult(exit_code=exit_code, uvx=uvx_path(search=search))


__all__ = ["UvInstallResult", "executable_on_path", "install_uv", "run_installer", "uvx_path"]
