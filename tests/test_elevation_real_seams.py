"""The seams the elevation question uses on a real machine, and what the copy's exit status becomes.

``test_elevation_dispatch.py`` drives :func:`elevate_for_the_view` with every
collaborator substituted, which is what lets it run on every platform. That
leaves the one bundle nothing there reaches: the seams a real run builds when it
is given none. Each member is asked here on its own - the arguments the copy is
handed, the directory it starts in, whether the process is elevated already,
whether the disks could be opened, and that the relaunch really goes through
pwshpy - with only true external edges substituted: ``sys.argv``, the working
directory, ``os.geteuid``, ``sys.platform`` and the pwshpy module itself, which
is handed in through the seam's own loader so no process is ever started.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw import elevation as dispatch
from lsdsk.domain.elevation import Elevation, ElevationPrompt, ElevationState

if TYPE_CHECKING:
    from collections.abc import Sequence

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


class StandInPwshpy:
    """The part of the pwshpy module lsdsk calls, answering ``elevate`` with a chosen code."""

    class PwshPyError(Exception):
        """pwshpy's root error."""

    class ElevationDeclinedError(PwshPyError):
        """pwshpy's refused-prompt error."""

    def __init__(self, answer: int | None) -> None:
        """Answer every ``elevate`` with ``answer``, which pwshpy may give as None."""
        self.answer = answer
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
        """Record the request and answer it."""
        self.calls.append({"argv": list(argv or ()), "executable": executable, "cwd": cwd, "wait": wait})
        return self.answer


def test_the_copy_is_handed_this_runs_arguments_without_the_program_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The program name is not an argument: handed on, the copy would read it as a subcommand."""
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/lsdsk", "--tree-density", "full", "tui"])

    assert dispatch.the_real_seams().argv == ("--tree-density", "full", "tui")


def test_the_copy_starts_in_the_directory_this_run_was_started_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative ``--replay`` or ``--history-file`` names a file there, so the copy has to start there."""
    monkeypatch.chdir(tmp_path)

    assert Path(dispatch.the_real_seams().cwd).resolve() == tmp_path.resolve()


@pytest.mark.os_posix
@pytest.mark.parametrize(
    ("euid", "elevated"),
    [pytest.param(0, True, id="root"), pytest.param(1000, False, id="an ordinary user")],
)
def test_a_posix_process_is_elevated_exactly_when_it_runs_as_root(
    monkeypatch: pytest.MonkeyPatch, euid: int, elevated: bool
) -> None:
    """The effective uid is the operating system's own answer, so it is the edge substituted here."""
    monkeypatch.setattr(os, "geteuid", lambda: euid)

    assert dispatch.process_is_elevated() is elevated
    assert dispatch.the_real_seams().is_elevated() is elevated


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_off_linux_the_disks_are_taken_to_be_openable(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """Only a Linux container is known to list disks root cannot open; elsewhere asking can help."""
    monkeypatch.setattr(sys, "platform", platform)

    assert dispatch.the_real_seams().devices_readable() is True


@pytest.mark.parametrize(
    ("platform", "prompt"),
    [pytest.param("win32", ElevationPrompt.UAC, id="windows"), pytest.param("linux", ElevationPrompt.SUDO, id="linux")],
)
def test_the_real_relaunch_goes_through_pwshpy_with_this_runs_arguments_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, prompt: ElevationPrompt
) -> None:
    """The relaunch the real seams carry reaches pwshpy's ``elevate``, and reports its prompt."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "argv", ["lsdsk", "--no-record"])
    monkeypatch.chdir(tmp_path)
    pwshpy = StandInPwshpy(0)
    seams = dispatch.the_real_seams(load=lambda: pwshpy)

    outcome = seams.relaunch(seams.argv, seams.cwd)

    assert outcome == Elevation(state=ElevationState.RELAUNCHED, prompt=prompt, exit_code=0)
    assert len(pwshpy.calls) == 1, "the relaunch never reached pwshpy"
    call = pwshpy.calls[0]
    assert call["argv"] == ["-P", "-m", "lsdsk", dispatch.RELAUNCH_MARKER, "--no-record"]
    assert Path(str(call["cwd"])).resolve() == tmp_path.resolve()


def test_a_platform_with_no_prompt_relaunches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the arm above: with no prompt to ask through, pwshpy is never reached."""
    monkeypatch.setattr(sys, "platform", "freebsd14")
    pwshpy = StandInPwshpy(0)
    seams = dispatch.the_real_seams(load=lambda: pwshpy)

    assert seams.prompt is None
    assert seams.relaunch(seams.argv, seams.cwd) == Elevation(state=ElevationState.NOT_ASKED)
    assert pwshpy.calls == []


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        pytest.param(0, 0, id="success"),
        pytest.param(None, 0, id="no code is success"),
        pytest.param(2, 2, id="a findings code passes through"),
        pytest.param(-2, 130, id="killed by SIGINT is 128 plus 2"),
    ],
)
def test_the_copys_answer_becomes_an_exit_status_this_run_can_leave_with(answer: int | None, status: int) -> None:
    """The copy's code passes through, None (a clean wait) is 0, and minus N (a signal death) is 128 plus N."""
    outcome = dispatch.relaunch_through_pwshpy(
        ("--no-record",), "/work", prompt=ElevationPrompt.SUDO, load=lambda: StandInPwshpy(answer)
    )

    assert outcome == Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=status)


@pytest.mark.parametrize(
    ("replay", "elevated"),
    [
        pytest.param("capture.json", False, id="a snapshot"),
        pytest.param(None, True, id="already elevated"),
    ],
)
def test_a_run_that_cannot_gain_is_not_asked_even_with_asking_turned_off(
    tmp_path: Path, replay: str | None, elevated: bool
) -> None:
    """Asking turned off is a reason given only where asking COULD have helped.

    A snapshot gains nothing from privilege and an elevated run has it already,
    so neither is told "this run does not ask": that banner would send a reader
    after a setting that changes nothing.
    """
    asked: list[tuple[str, ...]] = []
    said: list[str] = []

    def relaunch(argv: Sequence[str], cwd: str) -> Elevation:
        del cwd
        asked.append(tuple(argv))
        return Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=0)

    seams = dispatch.ElevationSeams(
        prompt=ElevationPrompt.SUDO,
        is_elevated=lambda: elevated,
        devices_readable=lambda: True,
        relaunch=relaunch,
        announce=said.append,
        argv=(),
        cwd="/work",
    )
    snapshot = tmp_path / replay if replay is not None else None

    outcome = dispatch.elevate_for_the_view(snapshot, wanted=False, seams=seams)

    assert outcome == Elevation(state=ElevationState.NOT_ASKED)
    assert asked == []
    assert said == [], "nobody is told a prompt is coming that never comes"


class _AtATerminal(io.StringIO):
    """A standard stream that says it is a terminal, which is all the view's own check asks."""

    def isatty(self) -> bool:
        """Answer as a terminal does."""
        return True


def _sitting_at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every standard stream the view needs a terminal, at the operating system's edge.

    The streams themselves are replaced, not this project's question about them,
    and click is run without CliRunner, which would swap them back.
    """
    monkeypatch.setattr(sys, "stdin", _AtATerminal())
    monkeypatch.setattr(sys, "stdout", _AtATerminal())
    monkeypatch.setattr(sys, "__stderr__", _AtATerminal())


def test_the_tui_command_hands_its_replay_to_the_elevation_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """``lsdsk tui --replay X`` renders a snapshot, so the question it asks must name that snapshot.

    Handed nothing instead, the real question would ask for a password to render
    a file that privilege cannot change. The port answers RELAUNCHED, so the run
    leaves before any view is built, and the recorder holds what it was asked.
    """
    from dataclasses import replace

    from lsdsk.adapters.cli import cli
    from lsdsk.composition import build_testing

    _sitting_at_a_terminal(monkeypatch)
    asked: list[tuple[Path | None, bool]] = []

    def elevate(replay: Path | None, *, wanted: bool) -> Elevation:
        asked.append((replay, wanted))
        return Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=0)

    services = replace(build_testing(), elevate=elevate)

    with pytest.raises(SystemExit) as left:
        cli.main(args=["--no-record", "tui", "--replay", str(CAPTURE)], obj=lambda: services, standalone_mode=False)

    assert left.value.code == 0
    assert asked == [(CAPTURE, True)]
