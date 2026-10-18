"""The interactive view asks for elevation on Linux and macOS as it does on Windows.

Windows asks for Administrator through a UAC prompt; Linux and macOS ask for root
through sudo, both by relaunching lsdsk through pwshpy's ``ps.elevate``. What
differs is the prompt, so the outcome carries which one it was and every line
that explains the outcome names that prompt rather than assuming Windows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from lsdsk.adapters.hw import elevation as dispatch
from lsdsk.adapters.render import report
from lsdsk.domain.elevation import Elevation, ElevationPrompt, ElevationState
from lsdsk.domain.models import Inventory

if TYPE_CHECKING:
    from collections.abc import Sequence


class Recorder:
    """Stands in for the relaunch and the announcement, remembering each call."""

    def __init__(self, outcome: Elevation) -> None:
        """Answer every relaunch with ``outcome``."""
        self.outcome = outcome
        self.relaunched: list[tuple[str, ...]] = []
        self.said: list[str] = []

    def relaunch(self, argv: Sequence[str], cwd: str) -> Elevation:
        """Record the request and answer with the configured outcome."""
        del cwd
        self.relaunched.append(tuple(argv))
        return self.outcome

    def announce(self, line: str) -> None:
        """Record what would have been printed."""
        self.said.append(line)


def _seams(recorder: Recorder, prompt: ElevationPrompt | None, *, readable: bool = True) -> dispatch.ElevationSeams:
    return dispatch.ElevationSeams(
        prompt=prompt,
        is_elevated=lambda: False,
        devices_readable=lambda: readable,
        relaunch=recorder.relaunch,
        announce=recorder.announce,
        argv=("--tree-density", "full"),
        cwd="/work",
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("platform", "prompt"),
    [
        pytest.param("win32", ElevationPrompt.UAC, id="windows asks through UAC"),
        pytest.param("linux", ElevationPrompt.SUDO, id="linux asks through sudo"),
        pytest.param("darwin", ElevationPrompt.SUDO, id="macos asks through sudo"),
        pytest.param("freebsd14", None, id="any other platform never asks"),
    ],
)
def test_each_platform_asks_through_its_own_prompt(platform: str, prompt: ElevationPrompt | None) -> None:
    assert dispatch.prompt_for(platform) is prompt


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("prompt", "named"),
    [
        pytest.param(ElevationPrompt.UAC, "Administrator", id="uac"),
        pytest.param(ElevationPrompt.SUDO, "sudo", id="sudo"),
    ],
)
def test_a_non_elevated_run_asks_and_says_which_prompt_is_coming(prompt: ElevationPrompt, named: str) -> None:
    accepted = Elevation(state=ElevationState.RELAUNCHED, prompt=prompt, exit_code=0)
    recorder = Recorder(accepted)

    outcome = dispatch.elevate_for_the_view(None, wanted=True, seams=_seams(recorder, prompt))

    assert outcome == accepted
    assert recorder.relaunched == [("--tree-density", "full")]
    (line,) = recorder.said
    assert named in line


@pytest.mark.os_agnostic
def test_the_sudo_announcement_says_it_restarts_in_this_terminal_not_a_new_window() -> None:
    """Sudo runs the copy in the same terminal; a UAC copy opens its own window."""
    sudo = dispatch.announcement_for(ElevationPrompt.SUDO)
    uac = dispatch.announcement_for(ElevationPrompt.UAC)

    assert "this terminal" in sudo
    assert "new window" not in sudo
    assert "new window" in uac


@pytest.mark.os_agnostic
def test_a_platform_that_never_asks_does_not_ask() -> None:
    recorder = Recorder(Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=0))

    outcome = dispatch.elevate_for_the_view(None, wanted=True, seams=_seams(recorder, None))

    assert outcome == Elevation(state=ElevationState.NOT_ASKED)
    assert recorder.relaunched == []
    assert recorder.said == []


@pytest.mark.os_agnostic
def test_a_machine_whose_disks_have_no_device_nodes_is_not_asked() -> None:
    """In a container root cannot open the disks either, so a password would buy nothing."""
    recorder = Recorder(Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=0))

    outcome = dispatch.elevate_for_the_view(
        None, wanted=True, seams=_seams(recorder, ElevationPrompt.SUDO, readable=False)
    )

    assert outcome == Elevation(state=ElevationState.NOT_ASKED)
    assert recorder.relaunched == []


@pytest.mark.os_agnostic
def test_a_run_told_not_to_ask_names_the_prompt_it_did_not_show() -> None:
    recorder = Recorder(Elevation(state=ElevationState.NOT_ASKED))

    outcome = dispatch.elevate_for_the_view(None, wanted=False, seams=_seams(recorder, ElevationPrompt.SUDO))

    assert outcome == Elevation(state=ElevationState.TURNED_OFF, prompt=ElevationPrompt.SUDO)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("state", [ElevationState.TURNED_OFF, ElevationState.DECLINED, ElevationState.FAILED])
def test_an_outcome_a_banner_explains_cannot_be_built_without_its_prompt(state: ElevationState) -> None:
    with pytest.raises(ValidationError, match="prompt"):
        Elevation(state=state)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("elevation", "words"),
    [
        pytest.param(
            Elevation(state=ElevationState.DECLINED, prompt=ElevationPrompt.UAC),
            ("declined", "Administrator"),
            id="uac declined",
        ),
        pytest.param(
            Elevation(state=ElevationState.DECLINED, prompt=ElevationPrompt.SUDO),
            ("sudo did not grant root",),
            id="sudo declined",
        ),
        pytest.param(
            Elevation(state=ElevationState.TURNED_OFF, prompt=ElevationPrompt.SUDO),
            ("does not ask for root", "--no-elevate"),
            id="sudo turned off",
        ),
        pytest.param(
            Elevation(state=ElevationState.FAILED, prompt=ElevationPrompt.SUDO, detail="sudo is not installed"),
            ("root", "sudo is not installed"),
            id="sudo failed",
        ),
    ],
)
def test_the_banner_names_the_prompt_that_was_refused(elevation: Elevation, words: tuple[str, ...]) -> None:
    line = report.elevation_note(elevation, Inventory(hostname="h", privileged=False))

    assert line.startswith("NOT ELEVATED: ")
    for word in words:
        assert word in line
    if elevation.prompt is ElevationPrompt.SUDO:
        assert "Administrator" not in line


class FakePwshpy:
    """Stands in for the pwshpy module, answering ``elevate`` with one exit code."""

    class PwshPyError(Exception):
        """pwshpy's root error."""

    class ElevationDeclinedError(PwshPyError):
        """pwshpy's refused-prompt error."""

    def __init__(self, exit_code: int) -> None:
        """Answer every ``elevate`` with ``exit_code``."""
        self.exit_code = exit_code
        self.ps = self

    def elevate(
        self,
        argv: Sequence[str] | None = None,
        *,
        executable: str | None = None,
        cwd: str | None = None,
        wait: bool = True,
    ) -> int | None:
        """Answer as configured."""
        del argv, executable, cwd, wait
        return self.exit_code


@pytest.mark.os_agnostic
def test_a_copy_killed_by_a_signal_hands_back_the_shell_s_exit_status() -> None:
    """Measured with real sudo: a Ctrl+C inside the root copy comes back as -2, which must leave as 130."""
    outcome = dispatch.relaunch_through_pwshpy(
        (), "/work", prompt=ElevationPrompt.SUDO, load=lambda: FakePwshpy(exit_code=-2)
    )

    assert outcome == Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=130)
