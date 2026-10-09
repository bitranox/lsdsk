"""A live run records only when the store may be written and recording is on.

``analyse`` folds this run's reading into the store it returns on a live JSON
run, and writes it on a live human run. Both are guarded by the same question,
``_why_not_to_record``: a store that could not be read must not be replaced by a
one-sample history, and ``--no-record`` must leave the record as it stood. Every
existing test of those rules replayed a snapshot or recorded, so a live run
that ignored them kept the suite green. The live reading comes through
``analyse``'s ``read_machine`` seam and the store is a real file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lsdsk.adapters.cli.commands.history import analyse
from lsdsk.adapters.config.history import HistorySettings
from lsdsk.adapters.history.store import save_history
from lsdsk.adapters.hw.snapshot import load
from lsdsk.domain.enums import OutputFormat
from lsdsk.domain.history import History, record

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
EARLIER = FIXTURES / "linux-sas-hba.json"
LATER = FIXTURES / "linux-sas-hba-later.json"


def live() -> Inventory:
    """The live reading: a capture taken after the one the store holds."""
    return load(LATER)


def store_holding_the_earlier_capture(tmp_path: Path) -> Path:
    """A real store file carrying the earlier capture's reading of every drive."""
    inventory = load(EARLIER)
    history = record(History(hostname=inventory.hostname), inventory.disks, "2026-01-01T00:00:00Z")
    store = tmp_path / "history.json"
    save_history(history, store)
    return store


def hours_by_drive(history: History) -> dict[str, tuple[int, ...]]:
    """Every drive's recorded power-on hours, for comparing two histories."""
    return {one.identity: tuple(s.power_on_hours for s in one.samples) for one in history.series}


def test_a_live_json_run_on_an_unreadable_store_keeps_the_refusal_and_invents_no_history(tmp_path: Path) -> None:
    """A corrupt store is still a store: the JSON run reports why, and folds nothing in."""
    store = tmp_path / "history.json"
    store.write_text("{ this is not json", encoding="utf-8")

    read = analyse(None, OutputFormat.JSON, HistorySettings(path=store), read_machine=live).history

    assert read.refusal is not None, "the refusal was lost, so the envelope cannot report it"
    assert read.writable is False
    assert read.history.series == (), f"a one-sample history was fabricated: {read.history.series}"


def test_a_live_json_run_with_recording_off_judges_against_the_store_as_it_stands(tmp_path: Path) -> None:
    """``--no-record`` means no reading is added, on the JSON path as on the human one."""
    store = store_holding_the_earlier_capture(tmp_path)
    settings = HistorySettings(path=store, enabled=False)
    before = hours_by_drive(analyse(None, OutputFormat.JSON, settings, read_machine=live).history.history)

    control = HistorySettings(path=store, enabled=True)
    folded = hours_by_drive(analyse(None, OutputFormat.JSON, control, read_machine=live).history.history)

    assert folded != before, "the control: with recording on, the later reading must be folded in"
    assert all(len(hours) == 1 for hours in before.values()), before


def test_a_live_human_run_with_recording_off_leaves_the_store_untouched(tmp_path: Path) -> None:
    """The ``--no-record`` contract on a live human run, which every other test replays."""
    store = store_holding_the_earlier_capture(tmp_path)
    original = store.read_bytes()

    read = analyse(None, OutputFormat.HUMAN, HistorySettings(path=store, enabled=False), read_machine=live).history

    assert store.read_bytes() == original, "a run told not to record wrote to the store"
    assert all(len(hours) == 1 for hours in hours_by_drive(read.history).values())


def test_a_live_human_run_with_recording_on_does_write(tmp_path: Path) -> None:
    """The control for the test above: the same run with recording on grows the store."""
    store = store_holding_the_earlier_capture(tmp_path)
    original = store.read_bytes()

    analyse(None, OutputFormat.HUMAN, HistorySettings(path=store), read_machine=live)

    assert store.read_bytes() != original, "the control did not record, so the test above proved nothing"


