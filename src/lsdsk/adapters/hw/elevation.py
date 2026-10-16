"""Decide whether the interactive view asks for Administrator before it reads.

Only where asking can change what the view shows: a live run (a snapshot is
rendered from what it already holds), on Windows (the one platform where a
missing privilege costs the SATA link as well as SMART, and where a process is
relaunched rather than raised), by a process that is not elevated already, and
only when the run was not told otherwise. Everywhere else the answer is that
nothing was asked, and the view opens as it always did.

System Role:
    Adapter layer. Chooses whether the platform relaunch runs, and is the only
    place that knows a relaunch has a platform at all.

The relaunch itself is pwshpy's ``ps.elevate``, a Windows-only dependency, so
this module reaches it through a typed facade over the module rather than an
import: on every other platform pwshpy is not installed at all.

Contents:
    * :data:`RELAUNCH_MARKER` - the option the elevated copy is started with.
    * :class:`ElevationSeams` - the platform, the check, the relaunch, the line.
    * :func:`elevate_for_the_view` - ask where it helps, report what happened.
    * :func:`relaunch_through_pwshpy` - the Windows relaunch, as an outcome.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

from ...domain.elevation import Elevation, ElevationState

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: Printed before the prompt appears, because a prompt nobody expected is one a
#: careful person refuses, and the window it opens is not where they typed.
#: Started on the elevated copy so it never asks again, whatever its token says.
RELAUNCH_MARKER = "--elevated-relaunch"

ANNOUNCEMENT = (
    "lsdsk is asking Windows for Administrator rights: without them it cannot read SMART or a SATA drive's "
    "link speed. If you allow it, lsdsk opens in a new window and this one waits for it."
)


class ElevationSeams(NamedTuple):
    """Everything :func:`elevate_for_the_view` consults, as one named group.

    Bundled because they are substituted together, and threaded one by one they
    would take the function past the width this project holds signatures to.

    Attributes:
        is_windows: Whether this is the platform that relaunches.
        is_elevated: Whether this process holds Administrator rights already.
        relaunch: Runs the elevated copy with these arguments in this directory.
        announce: Tells the person a prompt is coming, and why.
        argv: This run's arguments after the program name.
        cwd: The directory the copy should start in.
    """

    is_windows: bool
    is_elevated: Callable[[], bool]
    relaunch: Callable[[Sequence[str], str], Elevation]
    announce: Callable[[str], None]
    argv: tuple[str, ...]
    cwd: str


def elevate_for_the_view(replay: Path | None, *, wanted: bool, seams: ElevationSeams | None = None) -> Elevation:
    """Ask for Administrator where it can change the view, and report what happened.

    Args:
        replay: The snapshot being rendered instead of this machine, if any.
        wanted: Whether this run may ask; false under ``--no-elevate``,
            ``display.auto_elevate = false``, or in the elevated copy itself.
        seams: The collaborators; this machine's real ones when not given.

    Returns:
        NOT_ASKED where asking could not help, TURNED_OFF where the run may not
        ask, otherwise whatever the relaunch reported.
    """
    called = seams if seams is not None else _the_real_seams()
    if replay is not None or not called.is_windows or called.is_elevated():
        return Elevation(state=ElevationState.NOT_ASKED)
    if not wanted:
        return Elevation(state=ElevationState.TURNED_OFF)
    called.announce(ANNOUNCEMENT)
    return called.relaunch(called.argv, called.cwd)


def _the_real_seams() -> ElevationSeams:
    """This machine's platform, privilege and relaunch, the Windows ones loaded lazily."""
    is_windows = sys.platform == "win32"
    return ElevationSeams(
        is_windows=is_windows,
        is_elevated=_windows_is_elevated if is_windows else _never,
        relaunch=relaunch_through_pwshpy,
        announce=_to_stderr,
        argv=tuple(sys.argv[1:]),
        cwd=str(Path.cwd()),
    )


def _windows_is_elevated() -> bool:
    from .windows.reader import is_elevated  # noqa: PLC0415 - Windows-only path, kept off every other platform's import

    return is_elevated()


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

    Declared so it type-checks where pwshpy is not installed, which is every
    platform but Windows.

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
    argv: Sequence[str], cwd: str, *, load: Callable[[], PwshpyModule] | None = None
) -> Elevation:
    """Run the elevated copy through pwshpy and turn its answer into an outcome.

    The copy is THIS interpreter running ``-m lsdsk``, passed to pwshpy
    explicitly: under a console script built by uv ``sys.argv[0]`` has no
    ``.exe`` (measured on Windows), so the launcher path is not something
    Windows can start, while the interpreter always is.

    Args:
        argv: This run's arguments after the program name, passed on unchanged.
        cwd: The directory the copy starts in.
        load: Returns the pwshpy module; the installed one when not given.

    Returns:
        RELAUNCHED with the copy's exit code, DECLINED when the person refused,
        FAILED with pwshpy's message for anything else.
    """
    pwshpy = load() if load is not None else _installed_pwshpy()
    request = ["-m", "lsdsk", RELAUNCH_MARKER, *argv]
    try:
        code = pwshpy.ps.elevate(request, executable=sys.executable, cwd=cwd, wait=True)
    except pwshpy.ElevationDeclinedError:
        return Elevation(state=ElevationState.DECLINED)
    except pwshpy.PwshPyError as exc:
        return Elevation(state=ElevationState.FAILED, detail=str(exc))
    return Elevation(state=ElevationState.RELAUNCHED, exit_code=code or 0)


def _installed_pwshpy() -> PwshpyModule:
    """Import pwshpy by name: it is installed on Windows only, where this runs."""
    return cast("PwshpyModule", importlib.import_module("pwshpy"))


def _never() -> bool:
    return False


def _to_stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)  # noqa: T201 - the one line before a prompt, printed before any view exists


__all__ = [
    "ANNOUNCEMENT",
    "RELAUNCH_MARKER",
    "ElevationSeams",
    "PwshpyModule",
    "elevate_for_the_view",
    "relaunch_through_pwshpy",
]
