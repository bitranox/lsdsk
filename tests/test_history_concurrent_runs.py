"""Two runs recording into one counter store must not lose each other's readings.

A timer's ``record`` and an interactive ``lsdsk`` overlap easily, and each one
used to read the store, decide, and write back what it had read plus its own
reading. The run that wrote second replaced the first one's sample with the
copy of the store it had read before that sample existed.
"""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from lsdsk.adapters.cli.commands.history import read_history, record_reading
from lsdsk.adapters.config.history import HistorySettings
from lsdsk.adapters.history.store import history_lock, load_history, save_history
from lsdsk.adapters.hw.snapshot import load as load_inventory
from lsdsk.domain.history import record

if TYPE_CHECKING:
    from types import FrameType

    import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
EARLIER = load_inventory(FIXTURES / "linux-sas-hba.json")
LATER = load_inventory(FIXTURES / "linux-sas-hba-later.json")
EARLIER_HOURS = {
    hours for disk in EARLIER.disks if disk.health is not None and (hours := disk.health.power_on_hours) is not None
}


class _RefusedOnce:
    """A thread profile that notes the moment a thread waits inside ``history_lock``.

    ``history_lock`` sleeps only between attempts, so a call to ``time.sleep``
    made from its own frame is reached only after an attempt was refused: the
    recorder has met the held store, rather than merely not got round to it yet.
    Installed with :func:`threading.setprofile`, which reaches only the threads
    started after it, so the holder is never watched.
    """

    def __init__(self) -> None:
        self.seen = threading.Event()
        self._lock_code = inspect.unwrap(history_lock).__code__

    def __call__(self, frame: FrameType, event: str, arg: object) -> None:
        if event == "c_call" and frame.f_code is self._lock_code and arg is time.sleep:
            self.seen.set()


def _hours(store: Path) -> set[int]:
    history = load_history(store, hostname=EARLIER.hostname)
    return {sample.power_on_hours for series in history.series for sample in series.samples}


def test_a_reading_another_run_stored_after_this_one_read_the_store_survives(tmp_path: Path) -> None:
    settings = HistorySettings(path=tmp_path / "history.json")
    stale = read_history(EARLIER, settings)  # this run reads the store while it is still empty

    record_reading(LATER, read_history(LATER, settings), settings, announce=False)  # another run records
    later_hours = _hours(settings.path)
    assert later_hours, "the control: the other run stored nothing, so nothing could be lost"

    record_reading(EARLIER, stale, settings, announce=False)

    assert later_hours <= _hours(settings.path), "the other run's reading was overwritten by a stale copy"


def test_a_run_waits_for_the_store_another_run_is_writing(tmp_path: Path) -> None:
    settings = HistorySettings(path=tmp_path / "history.json")
    read = read_history(EARLIER, settings)
    held = threading.Event()
    release = threading.Event()

    def other_run() -> None:
        with history_lock(settings.path):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=other_run, daemon=True)
    holder.start()
    assert held.wait(timeout=10), "the control: the other run never took the lock"

    finished = threading.Event()
    recorder = threading.Thread(
        target=lambda: (record_reading(EARLIER, read, settings, announce=False), finished.set()), daemon=True
    )
    refused = _RefusedOnce()
    threading.setprofile(refused)
    try:
        recorder.start()
    finally:
        threading.setprofile(None)
    # Waiting on what the recorder did rather than on the clock: a fixed sleep
    # passed vacuously whenever the thread had not reached the lock yet, which on
    # a slow runner is exactly when nothing had been tested. Bounded, and the
    # bound fails the test rather than hanging it.
    deadline = time.monotonic() + 10
    while not (refused.seen.is_set() or finished.is_set()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not finished.is_set(), "this run wrote while another held the store"
    assert refused.seen.is_set(), "this run never met the held store within 10 seconds"

    release.set()
    recorder.join(timeout=10)
    assert finished.is_set(), "this run never finished after the other let go"
    assert _hours(settings.path), "it finished without storing its reading"


def test_a_run_that_finds_the_store_created_while_it_waited_does_not_announce_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Whether this run is the machine's first is decided holding the lock, not before.

    Two runs starting against an empty store each used to look for the file
    before trying for the lock, so both saw nothing there and both printed the
    once-per-machine announcement when their turn to write came round. Here
    the other run holds the real lock, this run reaches it and waits, the other
    run creates the store and lets go: this run then finds a store it did not
    create and must stay quiet about it.
    """
    settings = HistorySettings(path=tmp_path / "history.json")
    read = read_history(EARLIER, settings)
    other = read_history(LATER, settings)
    held = threading.Event()
    write_now = threading.Event()

    def other_run() -> None:
        with history_lock(settings.path):
            held.set()
            write_now.wait(timeout=10)
            save_history(record(other.history, LATER.disks, "t-other"), settings.path)

    holder = threading.Thread(target=other_run, daemon=True)
    holder.start()
    assert held.wait(timeout=10), "the control: the other run never took the lock"

    finished = threading.Event()
    recorder = threading.Thread(target=lambda: (record_reading(EARLIER, read, settings), finished.set()), daemon=True)
    refused = _RefusedOnce()
    threading.setprofile(refused)
    try:
        recorder.start()
    finally:
        threading.setprofile(None)
    deadline = time.monotonic() + 10
    while not (refused.seen.is_set() or finished.is_set()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert refused.seen.is_set(), "this run never met the held store within 10 seconds"

    write_now.set()
    holder.join(timeout=10)
    recorder.join(timeout=10)
    assert finished.is_set(), "this run never finished after the other let go"
    assert EARLIER_HOURS, "the control: the capture carries no power-on hours to record"
    assert _hours(settings.path) >= EARLIER_HOURS, "the control: this run stored nothing, so nothing to announce"

    assert "Recording disk error counters" not in capsys.readouterr().err
