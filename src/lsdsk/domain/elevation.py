"""What became of asking for Administrator or root before the interactive view opened.

Some figures can only be read elevated - SMART on every platform, a SATA
drive's link speed on Windows - and a running process cannot be given the
privilege: Windows starts a NEW, elevated process through its UAC prompt, and on
Linux and macOS sudo starts one as root. So the interactive view asks once,
before it reads anything, and this value carries the answer to wherever it is
explained.

It is a value rather than a flag because "not elevated" has several causes that
a reader fixes differently - they declined the prompt, the relaunch failed, or
this run was told not to ask - and a banner that cannot tell them apart can only
say something vague. It also carries WHICH prompt it was, because the banner
that explains a refused sudo password must not talk about Administrator.

Contents:
    * :class:`ElevationState` - which of those happened.
    * :class:`ElevationPrompt` - which prompt was, or would have been, shown.
    * :class:`Elevation` - the state, with the prompt and the exit code or reason it carries.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from .base import DomainModel


class ElevationState(StrEnum):
    """What asking for Administrator or root came to.

    Attributes:
        NOT_ASKED: Nothing was asked: the run was already elevated, renders a
            snapshot, is on a platform that never relaunches, or runs where
            elevating could not reach the disks either.
        TURNED_OFF: The run was told not to ask (``--no-elevate`` or
            ``display.auto_elevate = false``).
        RELAUNCHED: An elevated copy ran; its exit code is this run's.
        DECLINED: The person refused the prompt, or sudo did not grant root.
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


class ElevationPrompt(StrEnum):
    """Which prompt a relaunch asks through.

    Attributes:
        UAC: Windows' Administrator prompt; the copy opens in its own window.
        SUDO: sudo's password prompt on Linux and macOS; the copy runs in this
            terminal.

    Example:
        >>> ElevationPrompt.SUDO.value
        'sudo'
    """

    UAC = "uac"
    SUDO = "sudo"


class Elevation(DomainModel, frozen=True):
    """The outcome of asking for elevation, with what each outcome carries.

    Attributes:
        state: What happened.
        prompt: The prompt that was shown, or would have been; required for
            every state but NOT_ASKED, since each of those is explained to the
            reader in that prompt's own words.
        exit_code: The elevated copy's exit code; only a relaunch has one.
        detail: Why a relaunch failed, in the words the system gave.

    Example:
        >>> Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=1).exit_code
        1
        >>> Elevation(state=ElevationState.NOT_ASKED).detail
        ''
    """

    state: ElevationState
    prompt: ElevationPrompt | None = None
    exit_code: int | None = None
    detail: str = ""

    @model_validator(mode="after")
    def _an_explained_outcome_names_its_prompt(self) -> Self:
        """Refuse an outcome a banner would have to explain without knowing the prompt."""
        if self.prompt is None and self.state is not ElevationState.NOT_ASKED:
            msg = f"an elevation that is {self.state.value} must name its prompt"
            raise ValueError(msg)
        return self


__all__ = ["Elevation", "ElevationPrompt", "ElevationState"]
