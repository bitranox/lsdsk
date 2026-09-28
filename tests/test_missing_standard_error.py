"""A process started without a standard error writes nothing it meant for stderr to stdout.

With descriptor 2 closed - ``2>&-``, a supervisor that wires only stdout - the
interpreter sets ``sys.stderr`` to None, and three printers each fell back to
STDOUT: the logging console (handed that None, which the writer read as "follow
stdout"), rich-click's usage error and the log line of a crash. So ``lsdsk config
--format json 2>&-`` left 0 with ``[INFO]: Displaying configuration`` appended to
its JSON envelope, and a usage error printed its whole help block onto the
stream a caller was parsing.

A missing stderr is answered the way a departed stderr reader already is: the
diagnostic is lost and the run's own output and verdict stand.

The runs are REAL processes, because the None is the interpreter's own answer to
a closed descriptor at start-up, which an in-process runner never produces. Each
carries a control with stderr open, which proves the run has a diagnostic to lose
- a run that printed nothing to stderr would pass the closed arm vacuously.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, NamedTuple

import pytest

from lsdsk.adapters.cli import safe_console
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from pathlib import Path


class _Run(NamedTuple):
    """What one spawned run left behind."""

    code: int
    stdout: bytes
    stderr: str


def _launch(argv: list[str], *, close_stderr: bool) -> _Run:
    """Run the real CLI in a child process, with or without a standard error.

    The child inherits the per-test configuration and state the conftest gives
    every test, so nothing it reads or writes is the developer's own.

    Args:
        argv: The arguments after the program name.
        close_stderr: Start the child with descriptor 2 closed.

    Returns:
        The exit code and what reached stdout and stderr.
    """
    completed = subprocess.run(  # noqa: S603 - the interpreter running this suite, with fixed arguments
        [sys.executable, "-m", "lsdsk", *argv],
        capture_output=True,
        check=False,
        timeout=120,
        preexec_fn=(lambda: os.close(2)) if close_stderr else None,
    )
    return _Run(completed.returncode, completed.stdout, completed.stderr.decode("utf-8", errors="replace"))


@pytest.mark.os_posix
def test_json_output_stays_only_json_when_there_is_no_standard_error(tmp_path: Path) -> None:
    argv = ["config", "--format", "json"]

    control = _launch(argv, close_stderr=False)
    run = _launch(argv, close_stderr=True)

    assert "Displaying configuration" in control.stderr, "the control logged nothing, so the closed arm proves nothing"
    assert run.code == ExitCode.SUCCESS, f"exit {run.code}"
    assert run.stdout == control.stdout, "a diagnostic meant for stderr reached stdout"
    envelope = json.loads(run.stdout)
    assert envelope["ok"] is True


@pytest.mark.os_posix
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        pytest.param(["--replay", "{missing}", "controllers"], ExitCode.USAGE_ERROR, id="a usage error"),
        pytest.param(["fail"], ExitCode.SOFTWARE_ERROR, id="a crash and its log line"),
    ],
)
def test_a_diagnostic_with_no_standard_error_is_lost_rather_than_printed_to_stdout(
    argv: list[str], code: ExitCode, tmp_path: Path
) -> None:
    argv = [arg.format(missing=tmp_path / "missing.json") for arg in argv]

    control = _launch(argv, close_stderr=False)
    run = _launch(argv, close_stderr=True)

    assert control.stderr.strip(), "the control said nothing on stderr, so the closed arm proves nothing"
    assert control.stdout == b"", "the control already writes to stdout, so it cannot tell a leak apart"
    assert run.code == code, f"exit {run.code}"
    assert run.stdout == b"", f"stderr's text reached stdout: {run.stdout[:200]!r}"


def test_a_writer_following_standard_error_never_falls_back_to_standard_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", None)

    writer = safe_console.safe_stream(err=True)
    writer.write("a diagnostic\n")
    writer.flush()

    assert stdout.getvalue() == ""


def test_a_writer_following_standard_error_follows_the_stream_it_is_at_each_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    writer = safe_console.safe_stream(err=True)

    monkeypatch.setattr(sys, "stderr", second)
    writer.write("late")

    assert (first.getvalue(), second.getvalue()) == ("", "late")


def test_a_missing_standard_error_is_stood_in_for_and_put_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", None)

    safe_console.stand_in_for_missing_standard_streams()
    stood_in = sys.stderr
    written = stood_in.write("lost") if stood_in is not None else None
    safe_console.restore_original_streams()

    assert written == len("lost")
    assert stood_in is not None
    assert not stood_in.isatty()
    assert sys.stderr is None


def test_a_process_with_a_standard_error_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = io.StringIO()
    monkeypatch.setattr(sys, "stderr", mine)

    safe_console.stand_in_for_missing_standard_streams()
    kept = sys.stderr
    safe_console.restore_original_streams()

    assert kept is mine
    assert sys.stderr is mine
