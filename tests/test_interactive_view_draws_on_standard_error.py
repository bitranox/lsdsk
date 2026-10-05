"""The interactive view opens only where the stream it DRAWS on is a terminal too.

Textual's POSIX driver draws on ``sys.__stderr__``, not on stdout. The router
asked only stdin and stdout whether they were terminals, so ``lsdsk 2>err.log``
typed at a terminal opened a view nobody could see: measured in tmux, the
screen stayed on the shell's own text, 161,934 bytes of escape sequences went
into ``err.log``, and the run ended only when somebody pressed ``q`` blind.
``2>&-`` left no stream to draw on at all.

So a bare ``lsdsk`` prints the page there, as it does into any pipe, and
``lsdsk tui`` refuses at ``22`` the way it refuses a redirected stdin or stdout.

Each run is a REAL process on a pseudo-terminal, because the question is what
the operating system says about three descriptors, and a view that should not
have opened shows up as a run that never ends - which only a process can
express. The control puts stderr on the terminal too and proves the harness
sees the view when it does open, by its switch to the alternate screen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from lsdsk.adapters.cli.commands import scan
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

if sys.platform != "win32":
    import pty

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

#: What Textual writes first when it takes a terminal over: the switch to the
#: alternate screen. The printed page never writes it.
ALTERNATE_SCREEN = b"\x1b[?1049h"

#: Text only a painted frame of the view carries: the capture's hostname, which
#: its header draws. Looked for AFTER the switch to the alternate screen, so the
#: printed page, which names it too, cannot satisfy it.
PAINTED = b"linux-sas-hba"

#: How long a run that should end on its own gets before it is called hung.
HANG_SECONDS = 60

#: Closes descriptor 2 and becomes the real interpreter with the remaining
#: arguments - ``2>&-`` without a ``preexec_fn``, which runs Python between fork
#: and exec in a process whose other threads did not come along.
CLOSE_STDERR_THEN_EXEC = "import os, sys; os.close(2); os.execv(sys.executable, [sys.executable, *sys.argv[1:]])"


class _Run(NamedTuple):
    """What one run at a pseudo-terminal left behind."""

    code: int | None
    terminal: bytes
    stderr: bytes


class _Drain(threading.Thread):
    """Read everything the child writes to the terminal, so its writes never block."""

    def __init__(self, master: int) -> None:
        super().__init__(daemon=True)
        self._master = master
        self.received = bytearray()

    def run(self) -> None:
        while True:
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.received.extend(chunk)

    def wait_for(self, marker: bytes, *, seconds: float, after: bytes = b"") -> bool:
        """Whether `marker` arrives before `seconds` pass, somewhere after the first `after`."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            start = self.received.find(after)
            if start >= 0 and marker in self.received[start:]:
                return True
            time.sleep(0.1)
        return False


def _at_a_terminal(argv: list[str], *, stderr: str, tmp_path: Path, press_q: bool = False) -> _Run:
    """Run lsdsk with stdin and stdout on a pseudo-terminal and stderr as asked.

    Args:
        argv: The command line after the program name.
        stderr: ``"terminal"``, ``"file"`` or ``"closed"``.
        tmp_path: This test's directory, for the history file and stderr's file.
        press_q: Press ``q`` once the view has taken the terminal over, which
            is how the control ends a view that did open.

    Returns:
        The exit code, or None for a run that had not ended after
        :data:`HANG_SECONDS`, and what reached the terminal and stderr's file.
    """
    master, slave = pty.openpty()
    log = tmp_path / "stderr.log"
    closing = ["-c", CLOSE_STDERR_THEN_EXEC] if stderr == "closed" else []
    command = [
        sys.executable,
        *closing,
        "-m",
        "lsdsk",
        "--no-record",
        "--history-file",
        str(tmp_path / "history.json"),
        *argv,
    ]
    with log.open("wb") as file:
        process = subprocess.Popen(  # noqa: S603 - the interpreter running this suite, with fixed arguments
            command,
            stdin=slave,
            stdout=slave,
            stderr={"terminal": slave, "file": file}.get(stderr),
            env={**os.environ, "TERM": "xterm-256color", "COLUMNS": "180", "LINES": "50"},
        )
    os.close(slave)
    drain = _Drain(master)
    drain.start()
    try:
        # Pressed once the view has PAINTED, an event, rather than a fixed second
        # after it took the terminal, a guess about how long a runner needs. The
        # press is then known to reach a mounted screen whose bindings are live.
        # (Measured, an earlier press is held in the terminal's input buffer and
        # still handled; the pause was never what made this work.) A view that
        # never paints is not pressed at all, and the run ends as a hang the test
        # reports after HANG_SECONDS.
        if press_q and drain.wait_for(PAINTED, after=ALTERNATE_SCREEN, seconds=HANG_SECONDS):
            os.write(master, b"q")
        try:
            code: int | None = process.wait(timeout=HANG_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
            code = None
    finally:
        drain.join(timeout=10)
        os.close(master)
    return _Run(code, bytes(drain.received), log.read_bytes())


@pytest.mark.os_posix
def test_the_harness_sees_the_view_when_every_stream_is_a_terminal(tmp_path: Path) -> None:
    run = _at_a_terminal(["--replay", str(FIXTURE)], stderr="terminal", tmp_path=tmp_path, press_q=True)

    assert ALTERNATE_SCREEN in run.terminal, "the view never took the terminal over, so the arms below prove nothing"
    assert run.code == ExitCode.GENERAL_ERROR, f"the view left {run.code}"


@pytest.mark.os_posix
@pytest.mark.parametrize("stderr", ["file", "closed"])
def test_a_bare_run_whose_stderr_is_not_a_terminal_prints_the_page(stderr: str, tmp_path: Path) -> None:
    run = _at_a_terminal(["--replay", str(FIXTURE)], stderr=stderr, tmp_path=tmp_path)

    assert run.code is not None, f"the run with stderr {stderr} never ended: a view opened that nobody can see"
    assert ALTERNATE_SCREEN not in run.terminal + run.stderr, "the interactive view opened"
    assert b"linux-sas-hba" in run.terminal, "the printed page did not reach the terminal"
    assert run.code == ExitCode.GENERAL_ERROR, f"the page left {run.code}, not the findings' code"


@pytest.mark.os_posix
@pytest.mark.parametrize("stderr", ["file", "closed"])
def test_tui_refuses_where_its_stderr_is_not_a_terminal(stderr: str, tmp_path: Path) -> None:
    run = _at_a_terminal(["tui", "--replay", str(FIXTURE)], stderr=stderr, tmp_path=tmp_path)

    assert run.code is not None, f"`lsdsk tui` with stderr {stderr} never ended: a view opened that nobody can see"
    assert ALTERNATE_SCREEN not in run.terminal + run.stderr, "the interactive view opened"
    assert run.code == ExitCode.INVALID_ARGUMENT, f"exit {run.code}"
    if stderr == "file":
        assert b"standard error" in run.stderr, f"the refusal does not say which stream: {run.stderr[-400:]!r}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("platform", "needed"),
    [
        ("linux", ["standard input", "standard output", "standard error"]),
        ("darwin", ["standard input", "standard output", "standard error"]),
        # Textual's Windows driver draws on stdout, so stderr is no requirement
        # there, and a refusal naming it would state another platform's rule.
        ("win32", ["standard input", "standard output"]),
    ],
)
def test_the_streams_the_view_needs_follow_the_platform_it_draws_on(
    monkeypatch: pytest.MonkeyPatch, platform: str, needed: list[str]
) -> None:
    monkeypatch.setattr(sys, "platform", platform)

    assert scan.names_of_the_streams_the_view_needs() == needed


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("platform", "requirement"),
    [
        ("linux", "needs standard input, standard output and standard error all to be terminals"),
        # Two streams are "both", and a sentence saying "all" of two reads as if
        # a third had been left out of the list.
        ("win32", "needs standard input and standard output both to be terminals"),
    ],
)
def test_the_tui_refusal_counts_the_streams_it_names(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    requirement: str,
) -> None:
    """The refusal's quantifier agrees with how many streams this platform asks about."""
    from lsdsk.adapters.cli import cli

    class RefuseToOpen:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("`lsdsk tui` opened the interactive view with no terminal to open it on")

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("lsdsk.adapters.tui.LsdskApp", RefuseToOpen)
    monkeypatch.setattr(sys, "platform", platform)

    result = cli_runner.invoke(cli, ["tui", "--replay", str(FIXTURE)], obj=production_factory)

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, f"exited {result.exit_code}: {result.output}"
    said = " ".join(result.stderr.split())
    assert requirement in said, f"the refusal does not say {requirement!r}:\n{result.stderr}"
