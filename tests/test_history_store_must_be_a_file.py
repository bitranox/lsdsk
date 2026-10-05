"""A history store is a regular file, and anything else is refused before it is opened.

The bounded reader accepts a FIFO on purpose, because ``--replay`` is fed from a
pipe. The history store is never written that way - the writer refuses every
destination that is not a regular file - but the reader took the same path, and
opening a FIFO for reading blocks until something writes to it. Measured: a FIFO
given as ``--history-file`` hung every command, ``health`` and ``record`` alike,
with no message and no end.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.history.store import load_history, save_history
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import History

HAS_FIFO = hasattr(os, "mkfifo")
REPO = Path(__file__).parent.parent
SNAPSHOT = REPO / "tests" / "fixtures" / "hw" / "linux-sas-hba.json"


def _release_a_blocked_reader(fifo: Path) -> None:
    """Give a reader stuck opening ``fifo`` its end of stream, so a failing arm does not leave it blocked."""
    with contextlib.suppress(OSError):
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))


@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_a_fifo_given_as_the_store_is_refused_rather_than_waited_on(tmp_path: Path) -> None:
    fifo = tmp_path / "history.json"
    os.mkfifo(fifo)
    outcome: list[BaseException | History] = []

    def load() -> None:
        try:
            outcome.append(load_history(fifo, hostname="box"))
        except Exception as error:  # recorded and asserted on below
            outcome.append(error)

    # Bounded by this test's own join rather than by the code under test, so a
    # reader that blocks fails here by name instead of hanging the suite.
    reader = threading.Thread(target=load, daemon=True)
    reader.start()
    reader.join(timeout=5)
    try:
        assert not reader.is_alive(), "loading a FIFO as the store blocked waiting for a writer"
    finally:
        _release_a_blocked_reader(fifo)
        reader.join(timeout=5)

    assert len(outcome) == 1 and isinstance(outcome[0], ConfigurationError), f"came back as {outcome!r}"
    assert "not a regular file" in str(outcome[0]), f"refused for another reason: {outcome[0]}"


@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_record_with_a_fifo_as_the_store_ends_as_a_store_it_cannot_use(tmp_path: Path) -> None:
    """End to end in a real process, because the hang is the defect and a thread cannot be killed."""
    fifo = tmp_path / "history.json"
    os.mkfifo(fifo)
    argv = [sys.executable, "-m", "lsdsk", "--history-file", str(fifo), "record", "--replay", str(SNAPSHOT)]

    try:
        result = subprocess.run(  # noqa: S603 - argv is built here, no shell
            argv, capture_output=True, cwd=str(REPO), check=False, timeout=60
        )
    except subprocess.TimeoutExpired:
        pytest.fail("record never finished with a FIFO as the store")

    assert result.returncode == ExitCode.CONFIG_ERROR, f"left {result.returncode}: {result.stderr!r}"
    assert b"not a regular file" in result.stderr, f"refused for another reason: {result.stderr!r}"


@pytest.mark.os_posix
def test_a_store_reached_through_a_symlink_is_still_read(tmp_path: Path) -> None:
    """The control: the rule is about what the path IS, so a link to a real store still counts."""
    real = tmp_path / "real.json"
    save_history(History(hostname="box"), real)
    link = tmp_path / "history.json"
    link.symlink_to(real)

    assert load_history(link, hostname="box").hostname == "box"


def test_a_missing_store_is_still_an_empty_history(tmp_path: Path) -> None:
    """The control: the refusal must not catch the store that has not been written yet."""
    assert load_history(tmp_path / "history.json", hostname="box").series == ()
