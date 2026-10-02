"""Two runs recording into one counter store must not lose each other's readings.

A timer's ``record`` and an interactive ``lsdsk`` overlap easily, and each one
used to read the store, decide, and write back what it had read plus its own
reading. The run that wrote second replaced the first one's sample with the
copy of the store it had read before that sample existed.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from lsdsk.adapters.cli.commands.history import read_history, record_reading
from lsdsk.adapters.config.history import HistorySettings
from lsdsk.adapters.history.store import history_lock, load_history
from lsdsk.adapters.hw.snapshot import load as load_inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
EARLIER = load_inventory(FIXTURES / "linux-sas-hba.json")
LATER = load_inventory(FIXTURES / "linux-sas-hba-later.json")


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
    recorder.start()
    time.sleep(0.3)
    assert not finished.is_set(), "this run wrote while another held the store"

    release.set()
    recorder.join(timeout=10)
    assert finished.is_set(), "this run never finished after the other let go"
    assert _hours(settings.path), "it finished without storing its reading"
