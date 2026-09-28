"""A standard error that refuses a write costs the diagnostic, never the report or the verdict.

A departed stderr reader was already answered that way. A stderr that refuses
for another reason - a log on a full disk, ``2>/dev/full`` - raises an
``OSError`` that is NOT a broken pipe, and five sites re-raised it: the echo of
a warning, the logging writer's write and its flush, the guard around a
diagnostic write and the final flush. Measured
before this: ``lsdsk --profile nosuch findings --replay ... --format json
2>/dev/full`` left 120, CPython's shutdown-flush code that no document lists,
with 0 bytes on stdout, against 1 and the whole envelope with stderr to a file.

stderr carries diagnostics about the run and never the run's own output, so the
rule is the one a missing stderr and a departed stderr reader already follow:
the diagnostic is lost, and the run's own output and exit code stand.

The runs are REAL processes with ``/dev/full`` as descriptor 2, because the
failure is the kernel's answer to a write, and one of the five sites is the
exit flush an in-process runner never reaches. Each carries a control with
stderr readable, which proves the run has a diagnostic to lose - a run that
wrote nothing to stderr would pass the refusing arm vacuously. ``/dev/full`` is
Linux's; macOS has no such device.
"""

from __future__ import annotations

import errno
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from lsdsk.adapters.cli import safe_console
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
LARGE = FIXTURES / "linux-sas-hba.json"
FULL_DEVICE = "/dev/full"


class _Run(NamedTuple):
    """What one spawned run left behind."""

    code: int
    stdout: bytes
    stderr: str


def _launch(argv: list[str], *, refuse_stderr: bool) -> _Run:
    """Run the real CLI in a child process, its stderr either read or a full device.

    Args:
        argv: The arguments after the program name.
        refuse_stderr: Give the child ``/dev/full`` as descriptor 2.

    Returns:
        The exit code, what reached stdout, and what reached stderr when it was read.
    """
    with Path(FULL_DEVICE).open("wb") as full:
        completed = subprocess.run(  # noqa: S603 - the interpreter running this suite, with fixed arguments
            [sys.executable, "-m", "lsdsk", *argv],
            stdout=subprocess.PIPE,
            stderr=full if refuse_stderr else subprocess.PIPE,
            check=False,
            timeout=180,
        )
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
    return _Run(completed.returncode, completed.stdout, stderr)


def _history_isolated(tmp_path: Path) -> list[str]:
    return ["--no-record", "--history-file", str(tmp_path / "history.json")]


@pytest.mark.os_linux
def test_a_warning_the_full_disk_refused_costs_the_warning_and_not_the_report(tmp_path: Path) -> None:
    argv = [
        *_history_isolated(tmp_path),
        "--profile",
        "nosuch",
        "findings",
        "--replay",
        str(LARGE),
        "--format",
        "json",
    ]

    control = _launch(argv, refuse_stderr=False)
    run = _launch(argv, refuse_stderr=True)

    assert "Warning: profile nosuch" in control.stderr, "the control warned nothing, so the refusing arm proves nothing"
    assert control.stdout, "the control wrote no report, so there is nothing to lose"
    assert run.code == control.code, f"exit {run.code} against {control.code}"
    assert run.stdout == control.stdout, f"the report shrank from {len(control.stdout)} to {len(run.stdout)} bytes"


@pytest.mark.os_linux
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        pytest.param(["--replay", "{missing}", "controllers"], ExitCode.USAGE_ERROR, id="a usage error"),
        pytest.param(["fail"], ExitCode.SOFTWARE_ERROR, id="a crash and its traceback"),
        pytest.param(["config", "--format", "json"], ExitCode.SUCCESS, id="a log line"),
    ],
)
def test_a_diagnostic_the_full_disk_refused_leaves_the_run_s_own_code(
    argv: list[str], code: ExitCode, tmp_path: Path
) -> None:
    argv = [arg.format(missing=tmp_path / "missing.json") for arg in argv]

    control = _launch(argv, refuse_stderr=False)
    run = _launch(argv, refuse_stderr=True)

    assert control.stderr.strip(), "the control said nothing on stderr, so the refusing arm proves nothing"
    assert control.code == code, f"the control left {control.code}"
    assert run.code == code, f"exit {run.code}"
    assert run.stdout == control.stdout, "what stdout carried depended on whether stderr could be written"


class _RefusingStream(io.StringIO):
    """A stream whose every write and flush fails the way a full disk does."""

    def write(self, text: str) -> int:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def flush(self) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda: safe_console.echo("a warning", err=True), id="echo"),
        pytest.param(lambda: safe_console.safe_stream(err=True).write("a log line"), id="the logging writer"),
        pytest.param(lambda: safe_console.safe_stream(err=True).flush(), id="the logging writer's flush"),
        pytest.param(
            lambda: safe_console.write_unless_the_reader_left(lambda: safe_console.echo("a traceback", err=True)),
            id="the diagnostic guard",
        ),
    ],
)
def test_every_stderr_writer_loses_a_refused_diagnostic_quietly(
    write: Callable[[], object], monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", _RefusingStream())

    write()

    assert stdout.getvalue() == "", "a diagnostic meant for stderr reached stdout"


def test_a_final_flush_the_full_disk_refused_on_stderr_leaves_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", _RefusingStream())

    assert safe_console.flush_streams_or_leave(int(ExitCode.GENERAL_ERROR)) == ExitCode.GENERAL_ERROR
