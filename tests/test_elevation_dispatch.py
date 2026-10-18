"""The interactive view asks for elevation only where asking can change what it shows.

``elevate_for_the_view`` is driven with its collaborators injected through its
own seam bundle: the prompt, the elevation and device checks, the relaunch and
the line it prints. Each arm records whether the relaunch was reached, because
the costly mistake here is a prompt where none can help - a snapshot being
rendered, a run that is elevated already, or a platform with no prompt - and the
opposite mistake is a run that should have asked and silently did not. The arms
here run through the Windows prompt; ``test_elevation_on_every_platform.py``
holds what differs per platform.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw import elevation as dispatch
from lsdsk.domain.elevation import Elevation, ElevationPrompt, ElevationState

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class Recorder:
    """Stands in for the relaunch and the announcement, remembering each call."""

    def __init__(self, outcome: Elevation) -> None:
        """Answer every relaunch with ``outcome``."""
        self.outcome = outcome
        self.relaunched: list[tuple[tuple[str, ...], str]] = []
        self.said: list[str] = []

    def relaunch(self, argv: Sequence[str], cwd: str) -> Elevation:
        """Record the request and answer with the configured outcome."""
        self.relaunched.append((tuple(argv), cwd))
        return self.outcome

    def announce(self, line: str) -> None:
        """Record what would have been printed."""
        self.said.append(line)


UAC = ElevationPrompt.UAC


def _seams(
    recorder: Recorder, *, prompt: ElevationPrompt | None = UAC, elevated: bool = False
) -> dispatch.ElevationSeams:
    return dispatch.ElevationSeams(
        prompt=prompt,
        is_elevated=lambda: elevated,
        devices_readable=lambda: True,
        relaunch=recorder.relaunch,
        announce=recorder.announce,
        argv=("--tree-density", "full"),
        cwd="C:\\work",
    )


ACCEPTED = Elevation(state=ElevationState.RELAUNCHED, prompt=UAC, exit_code=1)


@pytest.mark.os_agnostic
def test_a_non_elevated_windows_run_asks_and_hands_back_the_answer() -> None:
    recorder = Recorder(ACCEPTED)

    outcome = dispatch.elevate_for_the_view(None, wanted=True, seams=_seams(recorder))

    assert outcome == ACCEPTED
    assert recorder.relaunched == [(("--tree-density", "full"), "C:\\work")]
    assert len(recorder.said) == 1, "the person must be told why a prompt is about to appear"
    assert "Administrator" in recorder.said[0]


@pytest.mark.os_agnostic
def test_a_declined_prompt_comes_back_as_declined() -> None:
    recorder = Recorder(Elevation(state=ElevationState.DECLINED, prompt=UAC))

    outcome = dispatch.elevate_for_the_view(None, wanted=True, seams=_seams(recorder))

    assert outcome.state is ElevationState.DECLINED


@pytest.mark.os_agnostic
def test_a_run_told_not_to_ask_does_not_ask_and_says_so() -> None:
    recorder = Recorder(ACCEPTED)

    outcome = dispatch.elevate_for_the_view(None, wanted=False, seams=_seams(recorder))

    assert outcome == Elevation(state=ElevationState.TURNED_OFF, prompt=UAC)
    assert recorder.relaunched == []
    assert recorder.said == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("replay", "prompt", "elevated"),
    [
        pytest.param("capture.json", UAC, False, id="a snapshot needs no privilege"),
        pytest.param(None, None, False, id="a platform with no prompt never asks"),
        pytest.param(None, UAC, True, id="already elevated"),
    ],
)
def test_nothing_is_asked_where_asking_cannot_change_the_view(
    tmp_path: Path, replay: str | None, prompt: ElevationPrompt | None, elevated: bool
) -> None:
    recorder = Recorder(ACCEPTED)
    snapshot = tmp_path / replay if replay is not None else None

    outcome = dispatch.elevate_for_the_view(
        snapshot, wanted=True, seams=_seams(recorder, prompt=prompt, elevated=elevated)
    )

    assert outcome == Elevation(state=ElevationState.NOT_ASKED)
    assert recorder.relaunched == []
    assert recorder.said == []


class FakePwshpy:
    """Stands in for the pwshpy module: its ``ps.elevate`` and the two error types lsdsk reads."""

    class PwshPyError(Exception):
        """pwshpy's root error."""

    class ElevationDeclinedError(PwshPyError):
        """pwshpy's refused-prompt error, a subclass of its root as in pwshpy."""

    def __init__(self, *, raises: type[Exception] | None = None, exit_code: int = 2) -> None:
        """Answer ``elevate`` with ``exit_code``, or raise ``raises``."""
        self.raises = raises
        self.exit_code = exit_code
        self.calls: list[dict[str, object]] = []
        self.ps = self

    def elevate(
        self,
        argv: Sequence[str] | None = None,
        *,
        executable: str | None = None,
        cwd: str | None = None,
        wait: bool = True,
    ) -> int | None:
        """Record the request; answer as configured."""
        self.calls.append({"argv": list(argv or ()), "executable": executable, "cwd": cwd, "wait": wait})
        if self.raises is not None:
            raise self.raises("refused")
        return self.exit_code


def _through_pwshpy(fake: FakePwshpy) -> Elevation:
    return dispatch.relaunch_through_pwshpy(("--tree-density", "full"), "C:\\work", prompt=UAC, load=lambda: fake)


@pytest.mark.os_agnostic
def test_the_relaunch_asks_pwshpy_for_this_interpreter_running_lsdsk_with_the_marker() -> None:
    """``-P -m lsdsk`` on this interpreter, never the launcher path: a uv launcher's argv0 has no ``.exe``.

    ``-P`` because ``-m`` otherwise puts the working directory first on ``sys.path``,
    and the copy runs as root or Administrator in the directory the person typed in:
    a ``rich.py`` or ``lsdsk/`` lying there would be imported with that privilege.
    """
    fake = FakePwshpy(exit_code=2)

    outcome = _through_pwshpy(fake)

    assert outcome == Elevation(state=ElevationState.RELAUNCHED, prompt=UAC, exit_code=2)
    assert fake.calls == [
        {
            "argv": ["-P", "-m", "lsdsk", dispatch.RELAUNCH_MARKER, "--tree-density", "full"],
            "executable": sys.executable,
            "cwd": "C:\\work",
            "wait": True,
        }
    ]


@pytest.mark.os_agnostic
def test_a_refused_prompt_from_pwshpy_is_declined() -> None:
    outcome = _through_pwshpy(FakePwshpy(raises=FakePwshpy.ElevationDeclinedError))

    assert outcome == Elevation(state=ElevationState.DECLINED, prompt=UAC)


@pytest.mark.os_agnostic
def test_any_other_pwshpy_error_is_a_failure_carrying_its_message() -> None:
    outcome = _through_pwshpy(FakePwshpy(raises=FakePwshpy.PwshPyError))

    assert outcome.state is ElevationState.FAILED
    assert "refused" in outcome.detail


@pytest.mark.os_agnostic
def test_the_installed_pwshpy_has_the_shape_the_facade_declares() -> None:
    """The double above is only worth what its likeness to the real module is."""
    import importlib

    pwshpy = importlib.import_module("pwshpy")

    assert issubclass(pwshpy.ElevationDeclinedError, pwshpy.PwshPyError)
    assert callable(pwshpy.ps.elevate)
