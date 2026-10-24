"""Decide whether the interactive view asks for Administrator or root before it reads.

Only where asking can change what the view shows: a live run (a snapshot is
rendered from what it already holds), on a platform with a prompt to ask
through - UAC on Windows, sudo on Linux and macOS - by a process that is not
elevated already, on a machine whose disks root could open (in a container
``/sys/block`` lists the host's disks while ``/dev`` holds none of their nodes,
so a password would buy nothing), and only when the run was not told otherwise.
Everywhere else the answer is that nothing was asked, and the view opens as it
always did.

System Role:
    Adapter layer. Chooses whether the platform relaunch runs, and is the only
    place that knows a relaunch has a platform at all.

The relaunch itself is pwshpy's ``ps.elevate``: a UAC prompt on Windows, ``sudo``
on Linux and macOS. This module reaches it through a typed facade over the
module rather than an import, so the one place lsdsk starts a process stays
named in one file.

Contents:
    * :data:`RELAUNCH_MARKER` - the option the elevated copy is started with.
    * :class:`ElevationSeams` - the prompt, the checks, the relaunch, the line.
    * :func:`prompt_for` - which prompt a platform asks through, if any.
    * :func:`announcement_for` - the line printed before that prompt.
    * :func:`elevate_for_the_view` - ask where it helps, report what happened.
    * :func:`the_real_seams` - this machine's collaborators for that question.
    * :func:`process_is_elevated` - whether this process is Administrator or root already.
    * :func:`announce_on_stderr` - print that line, set off from what came before.
    * :func:`relaunch_through_pwshpy` - the relaunch, as an outcome.
"""

from __future__ import annotations

import importlib
import os
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

from ...domain.elevation import Elevation, ElevationPrompt, ElevationState

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: Started on the elevated copy so it never asks again, whatever its token says.
RELAUNCH_MARKER = "--elevated-relaunch"

#: Printed before the prompt appears, because a prompt nobody expected is one a
#: careful person refuses. Each says where the elevated copy will appear: UAC
#: opens it in a new window, while sudo runs it in this same terminal.
_ANNOUNCEMENTS = {
    ElevationPrompt.UAC: (
        "lsdsk is asking Windows for Administrator rights: without them it cannot read SMART or a SATA drive's "
        "link speed. If you allow it, lsdsk opens in a new window and this one waits for it."
    ),
    ElevationPrompt.SUDO: (
        "lsdsk is asking for root through sudo: without it it cannot read SMART or which PCIe ports are real "
        "slots. If sudo grants it, lsdsk restarts as root in this terminal."
    ),
}

#: The platforms that relaunch, keyed by ``sys.platform``, and the prompt each asks through.
_PROMPTS = {
    "win32": ElevationPrompt.UAC,
    "linux": ElevationPrompt.SUDO,
    "darwin": ElevationPrompt.SUDO,
}

#: A POSIX shell reports a process killed by signal N as exit status 128 + N.
_SIGNALLED_EXIT_BASE = 128


class ElevationSeams(NamedTuple):
    """Everything :func:`elevate_for_the_view` consults, as one named group.

    Bundled because they are substituted together, and threaded one by one they
    would take the function past the width this project holds signatures to.

    Attributes:
        prompt: The prompt this platform asks through; None where it never asks.
        is_elevated: Whether this process holds Administrator or root already.
        devices_readable: Whether an elevated copy could open the disks at all.
        relaunch: Runs the elevated copy with these arguments in this directory.
        announce: Tells the person a prompt is coming, and why.
        argv: This run's arguments after the program name.
        cwd: The directory the copy should start in.
    """

    prompt: ElevationPrompt | None
    is_elevated: Callable[[], bool]
    devices_readable: Callable[[], bool]
    relaunch: Callable[[Sequence[str], str], Elevation]
    announce: Callable[[str], None]
    argv: tuple[str, ...]
    cwd: str


def prompt_for(platform: str) -> ElevationPrompt | None:
    """Which prompt a platform asks through, or None where lsdsk never asks.

    Args:
        platform: A ``sys.platform`` value.

    Returns:
        UAC on Windows, sudo on Linux and macOS, otherwise None.

    Example:
        >>> prompt_for("linux")
        <ElevationPrompt.SUDO: 'sudo'>
        >>> prompt_for("freebsd14") is None
        True
    """
    return _PROMPTS.get(platform)


def announcement_for(prompt: ElevationPrompt) -> str:
    """The line printed before ``prompt`` appears, saying why and where the copy opens.

    Args:
        prompt: The prompt about to be shown.

    Returns:
        One sentence pair for standard error.

    Example:
        >>> "this terminal" in announcement_for(ElevationPrompt.SUDO)
        True
    """
    return _ANNOUNCEMENTS[prompt]


def elevate_for_the_view(replay: Path | None, *, wanted: bool, seams: ElevationSeams | None = None) -> Elevation:
    """Ask for Administrator or root where it can change the view, and report what happened.

    Args:
        replay: The snapshot being rendered instead of this machine, if any.
        wanted: Whether this run may ask; false under ``--no-elevate``,
            ``display.auto_elevate = false``, or in the elevated copy itself.
        seams: The collaborators; this machine's real ones when not given.

    Returns:
        NOT_ASKED where asking could not help, TURNED_OFF (naming the prompt it
        did not show) where the run may not ask, otherwise whatever the
        relaunch reported.
    """
    called = seams if seams is not None else the_real_seams()
    prompt = called.prompt
    if replay is not None or prompt is None or called.is_elevated() or not called.devices_readable():
        return Elevation(state=ElevationState.NOT_ASKED)
    if not wanted:
        return Elevation(state=ElevationState.TURNED_OFF, prompt=prompt)
    called.announce(announcement_for(prompt))
    return called.relaunch(called.argv, called.cwd)


def the_real_seams(*, load: Callable[[], PwshpyModule] | None = None) -> ElevationSeams:
    """This machine's prompt, privilege, disks and relaunch, the platform ones loaded lazily.

    Args:
        load: Returns the pwshpy module the relaunch goes through; the installed
            one when not given. A test passes a stand-in so the real seams can be
            driven end to end without starting a process.

    Returns:
        The seams :func:`elevate_for_the_view` uses when it is given none.
    """
    prompt = prompt_for(sys.platform)
    return ElevationSeams(
        prompt=prompt,
        is_elevated=process_is_elevated,
        devices_readable=_devices_readable_here,
        relaunch=partial(relaunch_through_pwshpy, prompt=prompt, load=load)
        if prompt is not None
        else _relaunch_nowhere,
        announce=announce_on_stderr,
        argv=tuple(sys.argv[1:]),
        cwd=str(Path.cwd()),
    )


def process_is_elevated() -> bool:
    """Whether this process holds Administrator (Windows' own token check) or root (euid 0).

    Returns:
        True when it does; the answer the elevation question and the desktop
        shortcut offer both read.
    """
    if sys.platform == "win32":
        from .windows.reader import is_elevated  # noqa: PLC0415 - Windows-only path, kept off every other platform's import

        return is_elevated()
    return os.geteuid() == 0


def _devices_readable_here() -> bool:
    """Whether root could open the disks; only Linux containers are known to say no."""
    if sys.platform != "linux":
        return True
    from .linux.reader import disk_nodes_reachable  # noqa: PLC0415 - Linux-only path, kept off every other platform's import

    return disk_nodes_reachable()


def _relaunch_nowhere(argv: Sequence[str], cwd: str) -> Elevation:
    """The relaunch of a platform with no prompt, which :func:`elevate_for_the_view` never reaches."""
    del argv, cwd
    return Elevation(state=ElevationState.NOT_ASKED)


class _Elevator(Protocol):
    """The one member of pwshpy's ``ps`` facade this module calls."""

    def elevate(
        self,
        argv: Sequence[str] | None = None,
        *,
        executable: str | None = None,
        cwd: str | None = None,
        wait: bool = True,
    ) -> int | None:
        """Relaunch elevated and return the copy's exit code."""
        ...


class PwshpyModule(Protocol):
    """The part of the pwshpy module lsdsk relies on.

    Declared so the three names lsdsk relies on are typed here, where the
    module itself is reached by name rather than imported.

    Attributes:
        ps: The facade carrying ``elevate``.
        PwshPyError: The root of pwshpy's errors.
        ElevationDeclinedError: Raised when the person refuses the prompt.
    """

    @property
    def ps(self) -> _Elevator: ...  # noqa: D102 - a Protocol member, documented above

    @property
    def PwshPyError(self) -> type[Exception]: ...  # noqa: D102, N802 - pwshpy's own class name

    @property
    def ElevationDeclinedError(self) -> type[Exception]: ...  # noqa: D102, N802 - pwshpy's own class name


def relaunch_through_pwshpy(
    argv: Sequence[str], cwd: str, *, prompt: ElevationPrompt, load: Callable[[], PwshpyModule] | None = None
) -> Elevation:
    """Run the elevated copy through pwshpy and turn its answer into an outcome.

    The copy is THIS interpreter running ``-m lsdsk``, passed to pwshpy
    explicitly: under a console script built by uv ``sys.argv[0]`` has no
    ``.exe`` (measured on Windows), so the launcher path is not something
    Windows can start, while the interpreter always is.

    A copy killed by a signal comes back from pwshpy as ``-N`` (measured with
    real sudo: a Ctrl+C inside the root copy returned -2), which is not an exit
    status a process can leave with, so it is handed back the way a shell
    reports it, ``128 + N``.

    Args:
        argv: This run's arguments after the program name, passed on unchanged.
        cwd: The directory the copy starts in.
        prompt: The prompt pwshpy asks through on this platform.
        load: Returns the pwshpy module; the installed one when not given.

    Returns:
        RELAUNCHED with the copy's exit code, DECLINED when the person refused
        or sudo did not grant root, FAILED with pwshpy's message for anything else.
    """
    pwshpy = load() if load is not None else _installed_pwshpy()
    # -P: `-m` would otherwise put `cwd` first on sys.path, and the copy runs elevated
    # there, so a stray `rich.py` or `lsdsk/` in that directory would be imported as root
    # or Administrator. Not -I, which also drops the user site where `pip --user` installs.
    request = ["-P", "-m", "lsdsk", RELAUNCH_MARKER, *argv]
    try:
        code = pwshpy.ps.elevate(request, executable=sys.executable, cwd=cwd, wait=True)
    except pwshpy.ElevationDeclinedError:
        return Elevation(state=ElevationState.DECLINED, prompt=prompt)
    except pwshpy.PwshPyError as exc:
        return Elevation(state=ElevationState.FAILED, prompt=prompt, detail=str(exc))
    return Elevation(state=ElevationState.RELAUNCHED, prompt=prompt, exit_code=_exit_status(code or 0))


def _exit_status(code: int) -> int:
    """The copy's exit code as a status this process can leave with: a signal death becomes 128 + N."""
    return _SIGNALLED_EXIT_BASE - code if code < 0 else code


def _installed_pwshpy() -> PwshpyModule:
    """Import pwshpy by name, the one place lsdsk reaches it."""
    return cast("PwshpyModule", importlib.import_module("pwshpy"))


def announce_on_stderr(line: str) -> None:
    """Print the line before the prompt on standard error, after a blank line.

    The blank line keeps it from running on from whatever question came first
    (the desktop-shortcut offer), so the reader sees where one ends and sudo's
    or UAC's begins.

    Args:
        line: The announcement, from :func:`announcement_for`.
    """
    print(f"\n{line}", file=sys.stderr, flush=True)  # noqa: T201 - the one line before a prompt, printed before any view exists


__all__ = [
    "RELAUNCH_MARKER",
    "ElevationSeams",
    "PwshpyModule",
    "announce_on_stderr",
    "announcement_for",
    "elevate_for_the_view",
    "process_is_elevated",
    "prompt_for",
    "relaunch_through_pwshpy",
    "the_real_seams",
]
