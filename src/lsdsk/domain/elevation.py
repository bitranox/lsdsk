"""What became of asking for Administrator before the interactive view opened.

On Windows a figure such as a SATA drive's link speed can only be read with
Administrator rights, and a running process cannot be given them: Windows starts
a NEW, elevated process instead. So the interactive view asks once, before it
reads anything, and this value carries the answer to wherever it is explained.

It is a value rather than a flag because "not elevated" has several causes that
a reader fixes differently - they declined the prompt, the relaunch failed, or
this run was told not to ask - and a banner that cannot tell them apart can only
say something vague.

Contents:
    * :class:`ElevationState` - which of those happened.
    * :class:`Elevation` - the state, with the exit code or reason it carries.
"""

from __future__ import annotations

from enum import StrEnum

from .base import DomainModel


class ElevationState(StrEnum):
    """What asking for Administrator came to.

    Attributes:
        NOT_ASKED: Nothing was asked: the run was already elevated, renders a
            snapshot, or is on a platform that never relaunches.
        TURNED_OFF: The run was told not to ask (``--no-elevate`` or
            ``display.auto_elevate = false``).
        RELAUNCHED: An elevated copy ran in its own window; its exit code is
            this run's.
        DECLINED: The person refused the prompt.
        FAILED: The relaunch could not start, for the reason carried.

    Example:
        >>> ElevationState.DECLINED.value
        'declined'
    """

    NOT_ASKED = "not-asked"
    TURNED_OFF = "turned-off"
    RELAUNCHED = "relaunched"
    DECLINED = "declined"
    FAILED = "failed"


class Elevation(DomainModel, frozen=True):
    """The outcome of asking for Administrator, with what each outcome carries.

    Attributes:
        state: What happened.
        exit_code: The elevated copy's exit code; only a relaunch has one.
        detail: Why a relaunch failed, in the words the system gave.

    Example:
        >>> Elevation(state=ElevationState.RELAUNCHED, exit_code=1).exit_code
        1
        >>> Elevation(state=ElevationState.NOT_ASKED).detail
        ''
    """

    state: ElevationState
    exit_code: int | None = None
    detail: str = ""


__all__ = ["Elevation", "ElevationState"]
