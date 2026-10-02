"""The lock beside the counter store: what it opens, and how long it waits.

The lock file sits in the store's own directory, which a ``--history-file``
can put anywhere - a shared or group-writable directory included. Opened with
a plain ``O_CREAT``, a symlink planted at the lock's name was followed and its
target created with the store's mode, as whoever ran ``record``: measured, a
link to ``victim/x`` left ``victim/x`` behind and the run exited 0.
"""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.commands.history import RecordAttempt, RecordOutcome, read_history, record_reading
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.history import HistorySettings
from lsdsk.adapters.history import store as store_module
from lsdsk.adapters.history.store import history_lock
from lsdsk.adapters.hw.snapshot import load as load_inventory

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
SNAPSHOT = FIXTURES / "linux-sas-hba.json"
INVENTORY = load_inventory(SNAPSHOT)


def _lock_of(store: Path) -> Path:
    """The lock file ``history_lock`` takes for ``store``."""
    return store.with_name(f".{store.name}.lock")


def _plant_a_link(tmp_path: Path) -> tuple[Path, Path]:
    """A store in a shared directory whose lock name is a link to a file elsewhere."""
    shared, victim = tmp_path / "shared", tmp_path / "victim" / "x"
    shared.mkdir()
    victim.parent.mkdir()
    store = shared / "history.json"
    _lock_of(store).symlink_to(victim)
    return store, victim


@pytest.mark.os_posix
def test_the_lock_refuses_a_symlink_planted_at_its_name(tmp_path: Path) -> None:
    store, victim = _plant_a_link(tmp_path)

    with pytest.raises(OSError, match=r"lock"), history_lock(store):
        pass

    assert not victim.exists(), "the lock followed a planted link and created its target"


@pytest.mark.os_posix
def test_the_lock_refuses_a_lock_name_that_is_not_a_regular_file(tmp_path: Path) -> None:
    """A FIFO at the lock's name would otherwise be opened and locked as if it were the lock."""
    store = tmp_path / "history.json"
    os.mkfifo(_lock_of(store))

    with pytest.raises(OSError, match=r"not a regular file"), history_lock(store, wait=0.1):
        pass


@pytest.mark.os_posix
def test_a_planted_lock_link_is_a_write_that_failed_not_a_recorded_run(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    store, victim = _plant_a_link(tmp_path)

    result = cli_runner.invoke(
        cli, ["--history-file", str(store), "record", "--replay", str(SNAPSHOT)], obj=production_factory
    )

    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a traceback: {result.exception!r}"
    )
    assert result.exit_code == ExitCode.IO_ERROR, f"a refused lock left exit {result.exit_code}: {result.output!r}"
    assert not victim.exists(), "the run created the planted link's target"
    assert not store.exists(), "the run wrote a store it held no lock for"


def test_a_plain_lock_is_still_taken(tmp_path: Path) -> None:
    """The control: the guard must not refuse the lock file it creates itself."""
    store = tmp_path / "history.json"

    with history_lock(store):
        pass
    with history_lock(store):  # and again, now that the file exists
        pass

    assert _lock_of(store).is_file()


def _hold_the_lock(store: Path) -> tuple[threading.Event, threading.Thread]:
    """Hold the store's lock on a thread, as another run would, until the event is set."""
    held, release = threading.Event(), threading.Event()

    def other_run() -> None:
        with history_lock(store):
            held.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=other_run, daemon=True)
    holder.start()
    assert held.wait(timeout=10), "the control: the other run never took the lock"
    return release, holder


def _within(seconds: float, action: Callable[[], object]) -> list[object]:
    """Run ``action`` on a thread and fail if it is still running after ``seconds``.

    The bound is the test's own rather than the code's, so a wait that never
    gives up fails here by name instead of hanging the suite.
    """
    outcome: list[object] = []

    def call() -> None:
        try:
            outcome.append(action())
        except OSError as error:
            outcome.append(error)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(timeout=seconds)
    assert not worker.is_alive(), f"still waiting after {seconds} s: the bounded wait never gave up"
    return outcome


def _take(store: Path) -> None:
    with history_lock(store, wait=0.1):
        pass


def test_a_lock_another_run_holds_is_given_up_once_the_wait_is_over(tmp_path: Path) -> None:
    store = tmp_path / "history.json"
    release, holder = _hold_the_lock(store)
    try:
        outcome = _within(5, lambda: _take(store))
    finally:
        release.set()
        holder.join(timeout=10)

    assert len(outcome) == 1, f"the held lock was taken anyway: {outcome!r}"
    refusal = outcome[0]
    assert isinstance(refusal, OSError), f"the held lock was taken anyway: {refusal!r}"
    assert refusal.errno == errno.EAGAIN, f"gave up with {refusal!r}"


def test_a_record_that_cannot_take_the_lock_is_a_write_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = HistorySettings(path=tmp_path / "history.json")
    read = read_history(INVENTORY, settings)
    # record_reading waits the shipped 30 seconds, which a unit test cannot
    # spend; history_lock reads the constant when it is called, so this is
    # the wait the real record path takes.
    monkeypatch.setattr(store_module, "LOCK_WAIT_SECONDS", 0.1)
    release, holder = _hold_the_lock(settings.path)
    try:
        outcome = _within(5, lambda: record_reading(INVENTORY, read, settings, announce=False))
    finally:
        release.set()
        holder.join(timeout=10)

    assert len(outcome) == 1, f"record_reading raised or returned nothing: {outcome!r}"
    attempt = outcome[0]
    assert isinstance(attempt, RecordAttempt), f"came back as {attempt!r}"
    assert attempt.outcome is RecordOutcome.COULD_NOT_WRITE, f"came back as {attempt!r}"
    assert not settings.path.exists(), "a run that held no lock wrote the store"
