"""Standard output that cannot be written is reported once, and leaves a documented code.

A full disk is the portable case: ``lsdsk ... > /dev/full`` fails every write
with ``ENOSPC``, which is an ``OSError`` that is NOT a broken pipe. Before this,
the failure escaped as a Python traceback followed by "Exception ignored while
flushing sys.stdout" and the process left 120 - CPython's shutdown-flush code,
which is no :class:`~lsdsk.adapters.cli.exit_codes.ExitCode` member and appears
in no document. The failed bytes were still in the buffer, so the interpreter
tried them again on the way down, outside every handler.

Each run is a REAL process: the defect lives in the interpreter's own exit
flush, which a ``CliRunner`` never reaches, so an in-process test passes against
the broken code. The hardware read is the one thing substituted, at the edge
the other snapshot tests substitute it at, so ``snapshot -o -`` writes a
committed capture rather than whatever machine runs the suite.
"""

from __future__ import annotations

import errno
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import IO, TYPE_CHECKING, NamedTuple

import pytest

from lsdsk.adapters.cli import safe_console
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
SMALL = FIXTURES / "linux-minimal.json"
LARGE = FIXTURES / "linux-sas-hba.json"

#: Python's block buffer off a terminal. Output under it reaches the OS only in
#: the final flush; output over it fails inside the command's own write. The two
#: are separate code paths, so each parameter below states which one it takes and
#: the control arm proves it.
BLOCK_BUFFER = 8192

#: Runs lsdsk's real entry point with the hardware read answered from a file.
#: The marker file records whether the machine was read at all.
_LAUNCHER = """
import json
import sys
from pathlib import Path

from lsdsk.adapters.cli.main import main
from lsdsk.adapters.hw import snapshot
from lsdsk.composition import build_production

capture, marker, *argv = sys.argv[1:]


def _read():
    Path(marker).write_text("read", encoding="utf-8")
    return json.loads(Path(capture).read_text(encoding="utf-8"))


snapshot.read_current_machine = _read
raise SystemExit(main(argv, services_factory=build_production))
"""


class _Run(NamedTuple):
    """What one spawned run left behind."""

    code: int
    stdout: bytes
    stderr: str
    read_the_machine: bool


def _launch(argv: list[str], *, stdout: IO[bytes] | int, tmp_path: Path, close_stdout: bool = False) -> _Run:
    """Run lsdsk in a child process with `stdout` as its standard output.

    Args:
        argv: The command line, without the program name.
        stdout: Where the child's standard output goes.
        tmp_path: This test's directory, for the marker file.
        close_stdout: Start the child with descriptor 1 closed, which is what
            ``>&-`` does and what a detached launch leaves.

    Returns:
        The exit code, what stdout carried when it was a pipe, stderr, and
        whether the machine was read.
    """
    marker = tmp_path / "read-the-machine"
    completed = subprocess.run(  # noqa: S603 - argv is built here, no shell
        [sys.executable, "-c", _LAUNCHER, str(SMALL), str(marker), *argv],
        stdout=stdout,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent.parent),
        env={**os.environ, "TERM": "dumb"},
        # The only way to hand a child a CLOSED descriptor 1: a redirect cannot
        # express it, and Popen reopens anything it is given.
        preexec_fn=(lambda: os.close(1)) if close_stdout else None,
        check=False,
        timeout=180,
    )
    stderr = completed.stderr.decode("utf-8", errors="replace")
    return _Run(completed.returncode, completed.stdout or b"", stderr, marker.exists())


def _history_isolated(tmp_path: Path) -> list[str]:
    return ["--no-record", "--history-file", str(tmp_path / "history.json")]


def _lines_about_standard_output(stderr: str) -> list[str]:
    # An ERROR about stdout, not any mention of it: the snapshot notice names
    # "the capture on standard output" on every successful run.
    return [line for line in stderr.splitlines() if line.startswith("Error:") and "standard output" in line]


class _Case(NamedTuple):
    argv: list[str]
    past_the_buffer: bool


_CASES = {
    "version, buffered": _Case(["--version"], past_the_buffer=False),
    # rich-click's help printer never flushes, so this one reaches the OS only in
    # main()'s own final flush - the path every other case here skips, because
    # click.echo flushes after each write.
    "help, flushed only on the way out": _Case(["--help"], past_the_buffer=False),
    "findings, buffered": _Case(["--replay", str(SMALL), "findings"], past_the_buffer=False),
    "report, through rich's writer": _Case(["--replay", str(LARGE), "report"], past_the_buffer=True),
    "findings json, through safe_console.echo": _Case(
        ["--replay", str(LARGE), "findings", "--format", "json"], past_the_buffer=True
    ),
    "snapshot capture": _Case(["snapshot", "-o", "-"], past_the_buffer=True),
}


def _argv(case: _Case, tmp_path: Path) -> list[str]:
    if case.argv[0] in {"--version", "--help", "snapshot"}:
        return case.argv
    return [*_history_isolated(tmp_path), *case.argv]


@pytest.mark.os_linux
@pytest.mark.parametrize("case", list(_CASES.values()), ids=list(_CASES))
def test_output_that_cannot_be_written_is_said_once_and_leaves_general_error(case: _Case, tmp_path: Path) -> None:
    """One sentence on stderr naming stdout and the errno, no traceback, and exit 1.

    1 is what the exit-code table gives a ``snapshot`` whose write failed for a
    reason other than permission, and the same failure in any other command is
    the same failure.
    """
    argv = _argv(case, tmp_path)
    control = _launch(argv, stdout=subprocess.PIPE, tmp_path=tmp_path)
    assert control.stdout, "the control wrote nothing, so there was nothing to fail to write"
    assert (len(control.stdout) > BLOCK_BUFFER) is case.past_the_buffer, (
        f"{len(control.stdout)} bytes do not take the path this case claims to exercise"
    )
    assert not _lines_about_standard_output(control.stderr), control.stderr

    with Path("/dev/full").open("wb") as full:
        run = _launch(argv, stdout=full, tmp_path=tmp_path)

    assert "Traceback" not in run.stderr, run.stderr
    assert "Exception ignored" not in run.stderr, run.stderr
    said = _lines_about_standard_output(run.stderr)
    assert len(said) == 1, f"expected one sentence about standard output, got {said!r} in {run.stderr!r}"
    assert f"[Errno {errno.ENOSPC}]" in said[0], f"the sentence does not say why: {said[0]!r}"
    assert run.code == ExitCode.GENERAL_ERROR, f"exit {run.code}; stderr: {run.stderr!r}"


@pytest.mark.os_linux
def test_a_refusal_decided_before_the_write_keeps_its_own_code(tmp_path: Path) -> None:
    """A code that says the run could not start outranks the lost envelope.

    ``snapshot`` refuses a global ``--replay`` at 22 and, with ``--format json``,
    writes that refusal's envelope to stdout. The envelope is lost to a full
    disk, and the stdout line says so, but 22 is still true.
    """
    argv = ["--replay", str(SMALL), "snapshot", "-o", str(tmp_path / "x.json"), "--format", "json"]
    with Path("/dev/full").open("wb") as full:
        run = _launch(argv, stdout=full, tmp_path=tmp_path)

    assert "Traceback" not in run.stderr, run.stderr
    assert "Exception ignored" not in run.stderr, run.stderr
    assert "--replay" in run.stderr, f"the refusal itself is gone: {run.stderr!r}"
    assert len(_lines_about_standard_output(run.stderr)) == 1, run.stderr
    assert run.code == ExitCode.INVALID_ARGUMENT, f"exit {run.code}; stderr: {run.stderr!r}"


@pytest.mark.os_posix
def test_a_closed_standard_output_is_refused_before_the_machine_is_read(tmp_path: Path) -> None:
    """``snapshot -o - >&-`` has nowhere to write, and says so rather than exiting 0.

    With descriptor 1 closed the interpreter sets ``sys.stdout`` to None, and
    ``click.echo`` returns silently for None - so the capture went nowhere, the
    run exited 0 and the notice still told the reader what the capture on
    standard output held. pythonw and detached Windows launches start the same
    way.
    """
    control = _launch(["snapshot", "-o", "-"], stdout=subprocess.PIPE, tmp_path=tmp_path)
    assert control.code == ExitCode.SUCCESS, control.stderr
    assert control.read_the_machine, "the control never read the machine, so the marker proves nothing"
    (tmp_path / "read-the-machine").unlink()

    run = _launch(["snapshot", "-o", "-"], stdout=subprocess.DEVNULL, tmp_path=tmp_path, close_stdout=True)

    assert run.code == ExitCode.GENERAL_ERROR, f"exit {run.code}; stderr: {run.stderr!r}"
    lowered = run.stderr.lower()
    assert "standard output is closed" in lowered, run.stderr
    assert "nothing was written" in lowered, run.stderr
    assert "serial number" not in lowered, f"the notice about a capture that went nowhere: {run.stderr!r}"
    assert not run.read_the_machine, "the machine was read for a capture that had nowhere to go"


class _RefusingStdout(io.StringIO):
    """A standard output that refuses every write the way a full disk does.

    It has no descriptor, so nothing is redirected: these tests read the ANSWER
    each guard gives, which is what the end-to-end runs above cannot reach -
    click.echo flushes as it writes, so there a failure is always met inside the
    write and the rankings below never decide anything.
    """

    def write(self, text: str) -> int:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def flush(self) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


def _refuse_stdout(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Put a refusing stdout in place, and hand back the stderr that reports it.

    Called from the test body rather than a fixture: pytest puts its own capture
    streams back at the start of every phase, so a stream set during setup is
    gone by the time the test runs.
    """
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", _RefusingStdout())
    monkeypatch.setattr(sys, "stderr", stderr)
    return stderr


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("decided", "left_with"),
    [
        pytest.param(ExitCode.SUCCESS, ExitCode.GENERAL_ERROR, id="nothing found yields"),
        pytest.param(ExitCode.GENERAL_ERROR, ExitCode.GENERAL_ERROR, id="a finding stays 1"),
        pytest.param(ExitCode.INVALID_ARGUMENT, ExitCode.INVALID_ARGUMENT, id="a refusal stands"),
        pytest.param(ExitCode.CONFIG_ERROR, ExitCode.CONFIG_ERROR, id="a config error stands"),
        pytest.param(ExitCode.SOFTWARE_ERROR, ExitCode.SOFTWARE_ERROR, id="a crash stands"),
    ],
)
def test_the_final_flush_ranks_an_unwritten_stdout_like_a_departed_reader(
    monkeypatch: pytest.MonkeyPatch, decided: ExitCode, left_with: ExitCode
) -> None:
    """0 and 1 yield to GENERAL_ERROR; a code that says the run could not start stands."""
    refusing_stdout = _refuse_stdout(monkeypatch)
    assert safe_console.flush_streams_or_leave(int(decided)) == left_with
    said = _lines_about_standard_output(refusing_stdout.getvalue())
    assert len(said) == 1, refusing_stdout.getvalue()


@pytest.mark.os_agnostic
def test_a_diagnostic_stdout_refuses_is_said_and_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure envelope meeting a full disk costs the envelope and nothing else."""
    refusing_stdout = _refuse_stdout(monkeypatch)

    def emit() -> None:
        sys.stdout.write("{}")

    safe_console.write_unless_the_reader_left(emit, err=False)

    assert len(_lines_about_standard_output(refusing_stdout.getvalue())) == 1, refusing_stdout.getvalue()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda: safe_console.echo("x"), id="echo"),
        pytest.param(lambda: safe_console.safe_stream().write("x"), id="rich's writer, write"),
        pytest.param(lambda: safe_console.safe_stream().flush(), id="rich's writer, flush"),
    ],
)
def test_a_sink_names_a_refused_stdout_by_its_own_type(
    monkeypatch: pytest.MonkeyPatch, write: Callable[[], object]
) -> None:
    """Every stdout sink raises the typed error, so the last-resort handler can tell it from a crash."""
    _refuse_stdout(monkeypatch)
    with pytest.raises(safe_console.UnwritableStandardOutputError, match=r"No space left on device") as raised:
        write()
    assert raised.value.errno == errno.ENOSPC
