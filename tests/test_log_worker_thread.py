"""A stdout failure met on the logging worker thread still reaches the exit code.

lib_log_rich writes on a QUEUE WORKER thread. With its console on stdout or on
both streams, a departed stdout reader made the guarded writer raise
``SystemExit(141)`` there - and ``SystemExit`` raised off the main thread ends
that thread and nothing else. The worker catches ``Exception`` only, so it died
silently: the 141 never reached the exit code, every later log line was
dropped (the stderr half of ``both`` included), and the shutdown drain waited
out its whole stop timeout for a worker that no longer existed. Measured
before this: ``LOG_CONSOLE_STREAM=stdout lsdsk fail`` with stdout's reader gone
took 5.6 s against 0.6 s, the 5 s being the library's default stop timeout.

So off the main thread a stdout failure is RECORDED rather than raised, and
the final flush answers from the record: the reader leaving is 141, stdout
refusing for another reason is 74 with its one sentence, and either ranks
against the run's own code exactly as it would have on the main thread.
"""

from __future__ import annotations

import errno
import io
import os
import subprocess
import sys
import threading
from functools import partial
from typing import IO, TYPE_CHECKING, NamedTuple

import pytest

from lsdsk.adapters.cli import safe_console
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

#: Long enough that a drain waiting on a dead worker cannot end before the
#: harness gives up, so a hang is a timeout rather than a figure to compare.
STOP_TIMEOUT_SECONDS = "600"
HANG_SECONDS = 60


class _Run(NamedTuple):
    """What one spawned run left behind."""

    code: int | None
    stdout: bytes
    stderr: str


def _launch(argv: list[str], *, log_stream: str, stdout_leaves: bool) -> _Run:
    """Run the real CLI with the logging console on `log_stream`.

    Args:
        argv: The arguments after the program name.
        log_stream: What ``LOG_CONSOLE_STREAM`` names.
        stdout_leaves: Close stdout's reader before the child writes anything.

    Returns:
        The exit code, or None for a run still going after :data:`HANG_SECONDS`,
        and what reached each stream that was read.
    """
    env = {
        **os.environ,
        "TERM": "dumb",
        "LOG_CONSOLE_STREAM": log_stream,
        "LOG_QUEUE_STOP_TIMEOUT": STOP_TIMEOUT_SECONDS,
    }
    process = subprocess.Popen(  # noqa: S603 - the interpreter running this suite, with fixed arguments
        [sys.executable, "-m", "lsdsk", *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert process.stdout is not None
    if stdout_leaves:
        process.stdout.close()
    try:
        out, err = process.communicate(timeout=HANG_SECONDS)
        code: int | None = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate(timeout=30)
        code = None
    return _Run(code, out or b"", (err or b"").decode("utf-8", errors="replace"))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("log_stream", ["stdout", "both"])
def test_a_departed_stdout_reader_does_not_kill_the_logging_worker(log_stream: str) -> None:
    control = _launch(["--no-record", "fail"], log_stream=log_stream, stdout_leaves=False)
    run = _launch(["--no-record", "fail"], log_stream=log_stream, stdout_leaves=True)

    assert b"intentional failure" in control.stdout, "the log line never went to stdout, so nothing here can break"
    assert run.code is not None, "the shutdown drain waited on a logging worker that had died"
    assert run.code == ExitCode.SOFTWARE_ERROR, f"exit {run.code}"
    if log_stream == "both":
        assert "intentional failure" in run.stderr, "the stderr half of the log was lost with the stdout half"


class _DepartedReader(io.StringIO):
    """A stdout whose reader has gone: every write and flush is a broken pipe."""

    def write(self, text: str) -> int:
        raise BrokenPipeError(errno.EPIPE, os.strerror(errno.EPIPE))

    def flush(self) -> None:
        raise BrokenPipeError(errno.EPIPE, os.strerror(errno.EPIPE))


class _FullDisk(io.StringIO):
    """A stdout on a full disk."""

    def write(self, text: str) -> int:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def flush(self) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


def _write_on_a_worker(writer_factory: Callable[[], IO[str]]) -> list[BaseException]:
    """Write one line through `writer_factory()` on a thread, as the logging worker does.

    Returns:
        Whatever escaped the write on that thread.
    """
    escaped: list[BaseException] = []

    def work() -> None:
        try:
            writer = writer_factory()
            writer.write("a log line\n")
            writer.flush()
        # BaseException on purpose: SystemExit is exactly what escaped here.
        except BaseException as exc:
            escaped.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(timeout=30)
    return escaped


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("stdout", "code", "said"),
    [
        pytest.param(_DepartedReader, ExitCode.BROKEN_PIPE, False, id="the reader left"),
        pytest.param(_FullDisk, ExitCode.IO_ERROR, True, id="the disk is full"),
    ],
)
@pytest.mark.parametrize("writer", [safe_console.safe_stream, safe_console.safe_stream_to_both])
def test_a_stdout_failure_on_a_worker_thread_reaches_the_exit_code(
    stdout: type[io.StringIO],
    code: ExitCode,
    said: bool,
    writer: Callable[[], IO[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout())
    monkeypatch.setattr(sys, "stderr", stderr)

    escaped = _write_on_a_worker(writer)
    try:
        answered = safe_console.flush_streams_or_leave(int(ExitCode.SUCCESS))
    finally:
        safe_console.restore_original_streams()

    assert not escaped, f"the write raised on the worker thread, which ends only that thread: {escaped!r}"
    assert answered == code, f"the run left {answered}"
    assert ("standard output" in stderr.getvalue()) is said, stderr.getvalue()
    if writer is safe_console.safe_stream_to_both:
        assert "a log line" in stderr.getvalue(), "the stderr half was lost with the stdout half"


@pytest.mark.os_agnostic
def test_a_worker_thread_failure_does_not_outlive_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", _DepartedReader())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    _write_on_a_worker(safe_console.safe_stream)
    safe_console.restore_original_streams()

    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert safe_console.flush_streams_or_leave(int(ExitCode.SUCCESS)) == ExitCode.SUCCESS


def test_the_run_is_still_the_main_thread_s_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """On the main thread a departed stdout reader still ends the run at the write."""
    monkeypatch.setattr(sys, "stdout", _DepartedReader())

    with pytest.raises(SystemExit) as leaving:
        safe_console.safe_stream().write("output")
    safe_console.restore_original_streams()

    assert leaving.value.code == ExitCode.BROKEN_PIPE


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("stdout", "code", "said"),
    [
        pytest.param(_DepartedReader, ExitCode.BROKEN_PIPE, False, id="the reader left"),
        pytest.param(_FullDisk, ExitCode.IO_ERROR, True, id="the disk is full"),
    ],
)
@pytest.mark.parametrize(
    "writer",
    [
        pytest.param(partial(safe_console.safe_stream, records_failures=True), id="stdout"),
        pytest.param(partial(safe_console.safe_stream_to_both, records_failures=True), id="both"),
    ],
)
def test_the_logging_console_records_a_stdout_failure_on_the_main_thread_too(
    stdout: type[io.StringIO],
    code: ExitCode,
    said: bool,
    writer: Callable[[], IO[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The logging console's writer never raises, because its caller swallows what it raises.

    lib_log_rich writes to its console inside ``except Exception``, so on the
    main thread - the demo, or a run with the queue off - a raised refusal was
    lost and the run left 0. The end-to-end arm needs ``/dev/full``; this holds
    the same rule on every platform.
    """
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout())
    monkeypatch.setattr(sys, "stderr", stderr)

    logging_writer = writer()
    logging_writer.write("a log line\n")
    logging_writer.flush()
    answered = safe_console.flush_streams_or_leave(int(ExitCode.SUCCESS))

    assert answered == code, f"the run left {answered}"
    assert ("standard output" in stderr.getvalue()) is said, stderr.getvalue()
