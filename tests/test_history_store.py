"""The history store: where samples live, and what keeps the file trustworthy.

A capture can always be retaken from the hardware. History cannot: it is the one
thing this tool produces that is unrecoverable if the file is lost or truncated,
because the drive keeps the total and never the past. So the write is atomic,
the file is owner-only (it carries every drive's serial), and the file cannot
grow without bound.
"""

from __future__ import annotations

import itertools
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from workcount import work_to_run

from lsdsk.adapters.history.store import (
    HISTORY_FILE_MODE,
    HISTORY_SCHEMA_VERSION,
    MAX_SAMPLES_PER_DRIVE,
    default_history_path,
    load_history,
    save_history,
)
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import CounterKind, DiskSeries, History, Sample, record, thin, trend_for
from lsdsk.domain.models import Disk, Health
from lsdsk.domain.text import MAX_DEVICE_TEXT

T0 = "2026-08-05T01:23:00+00:00"


def sample(hours: int, errors: int = 0) -> Sample:
    """A minimal sample."""
    return Sample(power_on_hours=hours, captured_at=T0, crc_errors=errors)


def history_of(*samples: Sample) -> History:
    """A one-drive history."""
    return History(hostname="box", series=(DiskSeries(identity="naa.1", model="X", samples=samples),))


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A path for a store that does not exist yet."""
    return tmp_path / "state" / "history.json"


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_a_missing_store_reads_as_empty_not_as_an_error(store: Path) -> None:
    """The first run on a machine has no file, and that is not a failure."""
    loaded = load_history(store, hostname="box")
    assert loaded.series == ()
    assert loaded.hostname == "box"


#: ``chmod 000`` refuses everyone except root, who reads through it, so the
#: counterpart below would report the unreadable store as readable rather than
#: fail. Asked with ``hasattr`` because Windows has no ``geteuid``.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.os_posix
@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root reads through a mode that refuses everyone else")
def test_a_store_that_cannot_be_read_is_refused_rather_than_reported_absent(tmp_path: Path) -> None:
    """The counterpart: a store this process may not look at is not an absent one.

    They are opposite answers to the caller. An absent store is the ordinary
    first run and every rule degrades to "first sample" correctly; one that
    exists and cannot be read means every verdict the tool is about to give is
    computed from nothing, and saying "no counter history recorded yet on this
    machine" tells the reader the opposite of what happened. It is reachable
    without anybody doing anything unusual: the store's directory is created
    with whatever the writer's umask says, so a root timer under umask 077
    leaves it 0700 and every later unprivileged run reads it as empty.
    """
    closed = tmp_path / "closed"
    closed.mkdir()
    store = closed / "history.json"
    save_history(history_of(sample(100, 5)), store)
    closed.chmod(0o000)

    try:
        with pytest.raises(ConfigurationError, match="Could not read a history store at"):
            load_history(store, hostname="box")
    finally:
        closed.chmod(0o700)


def test_a_saved_history_reads_back_identically(store: Path) -> None:
    original = history_of(sample(100, 5), sample(200, 9))
    save_history(original, store)
    assert load_history(store, hostname="box") == original


def test_saving_creates_the_directory(store: Path) -> None:
    save_history(history_of(sample(1)), store)
    assert store.is_file()


@pytest.mark.os_posix
def test_the_store_is_readable_only_by_its_owner(store: Path) -> None:
    """It carries every drive's serial number, as the snapshot file does.

    POSIX-only: Windows has no mode bits for os.chmod to set, and the store
    is kept private there by living under a per-user LOCALAPPDATA instead.
    """
    save_history(history_of(sample(1)), store)
    assert stat.S_IMODE(store.stat().st_mode) == HISTORY_FILE_MODE


@pytest.mark.os_posix
def test_the_store_stays_owner_only_under_a_permissive_umask(store: Path) -> None:
    """The condition that actually threatens the mode.

    Two things keep it narrow: mkstemp creates at 0600, and the mode is then set
    explicitly. Either alone passes the plain check above, so that check cannot
    see one of them being removed. A wide open umask is what a rewrite to a
    plain open would fail on, and it is the real-world case that matters, since
    a privileged run under umask 022 would otherwise publish every serial.
    """
    previous = os.umask(0)
    try:
        save_history(history_of(sample(1)), store)
    finally:
        os.umask(previous)
    mode = stat.S_IMODE(store.stat().st_mode)
    assert mode == HISTORY_FILE_MODE
    assert not mode & (stat.S_IRGRP | stat.S_IROTH)


def test_the_stored_file_names_its_schema(store: Path) -> None:
    save_history(history_of(sample(1)), store)
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert payload["schema"] == HISTORY_SCHEMA_VERSION


# --------------------------------------------------------------------------
# What keeps the file trustworthy
# --------------------------------------------------------------------------


def test_a_failed_write_leaves_the_previous_history_intact(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """History is the one artefact that cannot be re-read from the hardware.

    A plain write that dies midway truncates the file and the accumulated past
    is gone for good, so the replacement must be atomic.
    """
    save_history(history_of(sample(100, 5)), store)
    good = store.read_text(encoding="utf-8")

    def explode(*_args: object, **_kwargs: object) -> None:
        message = "disk full"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", explode)
    with pytest.raises(OSError, match="disk full"):
        save_history(history_of(sample(100, 5), sample(999, 77)), store)

    assert store.read_text(encoding="utf-8") == good
    assert load_history(store, hostname="box").series[0].samples[-1].power_on_hours == 100


def test_no_temporary_file_is_left_behind(store: Path) -> None:
    save_history(history_of(sample(1)), store)
    assert [p.name for p in store.parent.iterdir()] == [store.name]


def test_a_corrupt_store_is_reported_rather_than_crashing(store: Path) -> None:
    store.parent.mkdir(parents=True)
    store.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="history"):
        load_history(store, hostname="box")


def test_a_store_from_another_machine_is_refused_not_merged(store: Path) -> None:
    """Serial numbers can collide between machines, so the two must not mix."""
    save_history(History(hostname="other-box"), store)
    with pytest.raises(ConfigurationError, match="other-box"):
        load_history(store, hostname="box")


def test_a_store_from_a_future_schema_is_refused(store: Path) -> None:
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"schema": HISTORY_SCHEMA_VERSION + 1, "hostname": "box"}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="schema"):
        load_history(store, hostname="box")


def _store_with_text(store: Path, field: str, text: str) -> None:
    """Write a one-drive, one-sample store whose `field` holds `text`."""
    document: dict[str, Any] = {
        "schema": HISTORY_SCHEMA_VERSION,
        "hostname": "box",
        "series": [{"identity": "naa.1", "model": "X", "samples": [{"power_on_hours": 1, "captured_at": T0}]}],
    }
    if field == "hostname":
        document["hostname"] = text
    elif field == "captured_at":
        document["series"][0]["samples"][0]["captured_at"] = text
    else:
        document["series"][0][field] = text
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["hostname", "identity", "model", "captured_at"])
def test_a_stored_text_past_the_device_text_bound_is_refused(store: Path, field: str) -> None:
    """The store is a file the caller points at, so each of its strings is input.

    A capture bounds every piece of device text at `MAX_DEVICE_TEXT` because one
    field is cleaned a character at a time, which costs a multiple of its length
    in memory. The store's four strings carry the same kind of value through the
    same cleaning and had no bound at all: a 40 MB `identity` inside the file
    ceiling cost about 445 MB to replay a `trend`. Refused as any other malformed
    store is, with the machine's own hostname passed in so the hostname arm is
    refused by the bound and not by the other-machine check.
    """
    text = "a" * (MAX_DEVICE_TEXT + 1)
    _store_with_text(store, field, text)
    hostname = text if field == "hostname" else "box"
    with pytest.raises(ConfigurationError, match="not a history store") as refused:
        load_history(store, hostname=hostname)
    assert f"at most {MAX_DEVICE_TEXT}" in str(refused.value), f"the refusal gives no reason: {refused.value}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["hostname", "identity", "model", "captured_at"])
def test_a_stored_text_at_the_device_text_bound_still_loads(store: Path, field: str) -> None:
    """The control: the bound is a length, and a value exactly at it is kept."""
    text = "a" * MAX_DEVICE_TEXT
    _store_with_text(store, field, text)
    hostname = text if field == "hostname" else "box"
    loaded = load_history(store, hostname=hostname)
    stored = {
        "hostname": loaded.hostname,
        "identity": loaded.series[0].identity,
        "model": loaded.series[0].model,
        "captured_at": loaded.series[0].samples[0].captured_at,
    }
    assert stored[field] == text, f"{field} at the bound did not load unchanged"


# --------------------------------------------------------------------------
# Bounded growth
# --------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, MAX_SAMPLES_PER_DRIVE - 1, MAX_SAMPLES_PER_DRIVE, 5000])
def test_thinning_never_exceeds_the_cap(count: int) -> None:
    samples = tuple(sample(hour) for hour in range(count))
    kept = thin(samples, MAX_SAMPLES_PER_DRIVE)
    assert len(kept) <= MAX_SAMPLES_PER_DRIVE


# Long enough that every cap below is genuinely exceeded. At 400 samples the
# 512 cap short-circuited on ``len(samples) <= cap`` before the selection ran at
# all, so a third of this parametrisation asserted nothing about the rule it
# names. The control in the test body is what keeps that from coming back.
_MISBEHAVING_LENGTH = 2000


@pytest.mark.parametrize(
    ("name", "hours"),
    [
        ("a clock that runs backwards", list(range(_MISBEHAVING_LENGTH, 0, -1))),
        ("a clock that oscillates", [(index % 2) * 1000 for index in range(_MISBEHAVING_LENGTH)]),
        ("a reset halfway through", [*range(_MISBEHAVING_LENGTH // 2)] * 2),
        # Must END above the baseline or it takes the flat-clock fallback and never
        # reaches the bucketing. This is the shape that exercises the negative index.
        (
            "samples older than the baseline",
            [0, *(-500 - step for step in range(_MISBEHAVING_LENGTH // 2)), *range(1, _MISBEHAVING_LENGTH // 2)],
        ),
        ("a clock that never moves", [7] * _MISBEHAVING_LENGTH),
        (
            "an idle drive then a burst",
            [0] * (_MISBEHAVING_LENGTH // 2) + list(range(1, _MISBEHAVING_LENGTH // 2 + 1)),
        ),
        # Built explicitly rather than by repeating a pattern: truncating a
        # repeat to the target length left this ending BELOW its baseline, which
        # sent it to the flat-clock fallback at the 512 cap and quietly stopped
        # it exercising the bucketing at the cap that matters.
        (
            "one enormous jump",
            [0, *(10**9 if step % 2 else step for step in range(1, _MISBEHAVING_LENGTH - 1)), 10**9 + 5],
        ),
    ],
)
@pytest.mark.parametrize("cap", [3, 5, MAX_SAMPLES_PER_DRIVE])
def test_thinning_holds_the_cap_on_a_clock_that_misbehaves(name: str, hours: list[int], cap: int) -> None:
    """The cap has to survive the clock, not only a tidy ascending range.

    A drive's clock going backwards is a supported event, reported as
    TrendVerdict.RESET. Selecting by hour made that reachable: a sample older
    than the baseline landed in a negative bucket bounded by nothing, and
    keeping a sample whenever its bucket merely DIFFERED from the last let an
    oscillating clock fire that test repeatedly. A cap of 5 returned all 7
    inputs. The old ascending-range test could not see any of it.
    """
    samples = tuple(sample(hour) for hour in hours)
    assert len(samples) > cap, f"{name}: the series does not exceed the cap, so nothing is selected"

    kept = thin(samples, cap)

    assert len(kept) <= cap, f"{name}: kept {len(kept)} of {len(samples)} against a cap of {cap}"
    assert thin(kept, cap) == kept, f"{name}: thinning its own output changed it again"


def test_thinning_keeps_the_baseline_and_the_newest() -> None:
    """The first sample is the most valuable one: it is the only true baseline."""
    samples = tuple(sample(hour) for hour in range(5000))
    kept = thin(samples, MAX_SAMPLES_PER_DRIVE)
    assert kept[0] is samples[0]
    assert kept[-1] is samples[-1]


def test_thinning_leaves_a_short_series_untouched() -> None:
    samples = tuple(sample(hour) for hour in range(10))
    assert thin(samples, MAX_SAMPLES_PER_DRIVE) == samples


def test_thinning_keeps_the_order() -> None:
    kept = thin(tuple(sample(hour) for hour in range(5000)), MAX_SAMPLES_PER_DRIVE)
    hours = [s.power_on_hours for s in kept]
    assert hours == sorted(hours)
    assert len(set(hours)) == len(hours)


@pytest.mark.parametrize("cap", [3, 5, MAX_SAMPLES_PER_DRIVE])
def test_thinning_survives_an_hour_count_no_float_can_hold(cap: int) -> None:
    """A history store is input, not measurement, so its numbers are not trusted.

    The file is one the user points ``--history-file`` at, and nothing bounds
    how large a ``power_on_hours`` it may contain. Selecting by hour originally
    divided by a float width, which raised OverflowError from inside the domain
    on a value too large to convert; the user saw a traceback rather than this
    tool's own refusal. Python's integers do not overflow, so the arithmetic
    stays integral.
    """
    hours = [0, *(10**400 + step for step in range(_MISBEHAVING_LENGTH // 2)), *range(1, _MISBEHAVING_LENGTH // 2)]
    samples = tuple(sample(hour) for hour in hours)
    assert len(samples) > cap, "the control: the series must exceed the cap to be selected at all"

    kept = thin(samples, cap)

    assert len(kept) <= cap


def test_a_store_written_many_times_stays_bounded(store: Path) -> None:
    """The end-to-end guarantee, not just the helper's."""
    history = History(hostname="box")
    for hour in range(MAX_SAMPLES_PER_DRIVE * 3):
        disks = (Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=hour)),)
        history = record(history, disks, T0, cap=MAX_SAMPLES_PER_DRIVE)
    save_history(history, store)
    assert len(load_history(store, hostname="box").series[0].samples) <= MAX_SAMPLES_PER_DRIVE


def test_a_store_written_many_times_keeps_the_middle_it_promises() -> None:
    """Staying under the cap is not the guarantee; keeping a usable spread is.

    Thinning runs again on its own output after every reading, so a rule that
    picks by array position re-decimates the same samples on each pass. The old
    rule did: over four years of hourly readings it left the baseline plus the
    most recent weeks, with one hole covering 96% of the span, while never
    exceeding the cap. That hole changes what the tool concludes, because
    ``_quiet_run_start`` measures how long a counter has been silent by walking
    back through exactly those samples.

    A cap check alone cannot see this, which is why it survived.
    """
    history = History(hostname="box")
    hours = MAX_SAMPLES_PER_DRIVE * 8
    for hour in range(1, hours + 1):
        disks = (Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=hour)),)
        history = record(history, disks, T0, cap=MAX_SAMPLES_PER_DRIVE)

    kept = [sample.power_on_hours for sample in history.series[0].samples]
    assert len(kept) <= MAX_SAMPLES_PER_DRIVE
    assert kept[0] == 1, "the baseline is the only true lifetime reference"
    assert kept[-1] == hours

    widest = max(later - earlier for earlier, later in itertools.pairwise(kept))
    # A tenth of the span is generous: the position-based rule left a single gap
    # covering 96% of it, and one pass over the same readings leaves under 1%.
    assert widest < hours / 10, f"largest gap {widest}h of {hours}h, so the middle was decimated away"


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def test_recording_appends_a_sample_per_tracked_drive() -> None:
    disks = (
        Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=10, crc_errors=1)),
        Disk(node="sdb", path="/dev/sdb", model="Y", wwn="naa.2", health=Health(power_on_hours=20)),
    )
    history = record(History(hostname="box"), disks, T0)
    assert len(history.series) == 2
    assert history.for_identity("naa.1") is not None
    # Each drive's own clock advances, because a reading that repeats a drive's
    # hour replaces that row rather than adding one: see test_history.py's
    # test_a_second_reading_in_the_same_power_on_hour_replaces_the_row_it_would_repeat.
    moved = (
        Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=11, crc_errors=1)),
        Disk(node="sdb", path="/dev/sdb", model="Y", wwn="naa.2", health=Health(power_on_hours=21)),
    )
    later = record(history, moved, T0)
    assert len(later.series) == 2
    assert len(later.series[0].samples) == 2


def test_a_drive_with_no_identity_is_not_recorded() -> None:
    disks = (Disk(node="sda", path="/dev/sda", model="X", health=Health(power_on_hours=10)),)
    assert record(History(hostname="box"), disks, T0).series == ()


def test_a_drive_with_no_health_is_not_recorded() -> None:
    """An unprivileged run reads no counters, so it has nothing to store."""
    disks = (Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1"),)
    assert record(History(hostname="box"), disks, T0).series == ()


def test_recording_keeps_the_model_current() -> None:
    first = (Disk(node="sda", path="/dev/sda", model="Old", wwn="naa.1", health=Health(power_on_hours=1)),)
    second = (Disk(node="sda", path="/dev/sda", model="New", wwn="naa.1", health=Health(power_on_hours=2)),)
    history = record(record(History(hostname="box"), first, T0), second, T0)
    series = history.for_identity("naa.1")
    assert series is not None
    assert series.model == "New"


# --------------------------------------------------------------------------
# Where the store lives
# --------------------------------------------------------------------------


def test_the_default_path_follows_xdg_state_on_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdgstate"))
    assert default_history_path() == tmp_path / "xdgstate" / "lsdsk" / "history.json"


def test_the_default_path_falls_back_to_local_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() is not covered by the sys.platform patch above and reads
    # USERPROFILE on Windows, so without this the simulated Linux run still
    # resolved the real Windows home.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_history_path() == tmp_path / ".local" / "state" / "lsdsk" / "history.json"


def test_the_default_path_is_not_the_config_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """State is not configuration; a config sync must never carry history."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # As in the test above: Path.home() reads USERPROFILE on Windows, so without
    # it this resolved the runner's real home and passed without testing anything.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    resolved = default_history_path()
    assert resolved.is_relative_to(tmp_path), f"{resolved} is not under the home this test set"
    assert ".config" not in resolved.parts


def test_the_default_path_is_per_user_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    resolved = default_history_path()
    assert resolved.name == "history.json"
    assert "lsdsk" in resolved.parts


# --------------------------------------------------------------------------
# A number in a state file is input, not measurement
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["power_on_hours", "crc_errors"])
def test_a_magnitude_no_float_can_hold_is_refused_not_crashed(tmp_path: Path, field: str) -> None:
    """A rate is a float, so every stored figure has to survive becoming one.

    ``--history-file`` points at a file the user chose, so its numbers are input.
    A value past the float ceiling used to raise ``OverflowError`` from inside
    the domain and take ``trend``, ``health`` and ``findings`` down with it, so
    a machine with a failing drive reported nothing at all. It must read as
    "that is not a history store" instead.
    """
    sample = {"power_on_hours": 100, "captured_at": "2024-01-01T00:00:00Z", "crc_errors": 5}
    sample[field] = 10**400
    store = tmp_path / "history.json"
    store.write_text(
        json.dumps(
            {
                "schema": 1,
                "hostname": "box",
                "series": [{"identity": "naa.1", "model": "X", "samples": [sample]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="not a history store"):
        load_history(store, hostname="box")


@pytest.mark.os_agnostic
def test_a_realistic_magnitude_is_still_accepted(tmp_path: Path) -> None:
    """The control: the bound must not reject what the hardware can produce.

    An NVMe counter is a 128-bit field and one of them is scaled by the data
    unit size, so roughly 1e44 is reachable from real hardware.
    """
    store = tmp_path / "history.json"
    store.write_text(
        json.dumps(
            {
                "schema": 1,
                "hostname": "box",
                "series": [
                    {
                        "identity": "naa.1",
                        "model": "X",
                        "samples": [
                            {"power_on_hours": 2**128, "captured_at": "2024-01-01T00:00:00Z", "crc_errors": 10**44}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert load_history(store, hostname="box").series, "the bound rejected a value real hardware can report"


@pytest.mark.os_agnostic
def test_a_corrupt_history_store_is_explained_in_this_tool_s_own_words(tmp_path: Path) -> None:
    """The store refuses the way the snapshot loader does, having the same problem.

    Both are JSON-backed and pydantic-validated, and both can meet a file
    somebody hand-edited or a write that was cut short. The snapshot loader goes
    out of its way to keep pydantic's packaging away from a caller; this one
    interpolated `str(ValidationError)`, so the pinned version's URL, the
    internal model name and a slice of the caller's own file reached stderr from
    every command that reads history.
    """
    store = tmp_path / "history.json"
    store.write_text(
        json.dumps({"schema_version": 1, "hostname": 42, "series": "not a list"}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as refused:
        load_history(store, hostname="probe")

    said = str(refused.value)
    assert "errors.pydantic.dev" not in said, f"a pydantic URL reached the caller:\n{said}"
    assert "input_value" not in said, f"pydantic's own field names reached the caller:\n{said}"
    assert "HistoryFile" not in said, f"an internal model name reached the caller:\n{said}"
    # And it still says WHAT is wrong, field by field, or the refusal is useless.
    assert "hostname" in said and "series" in said, f"the refusal names no field:\n{said}"
    assert "valid string" in said, f"the refusal gives no reason:\n{said}"


# --------------------------------------------------------------------------
# The cap holds on the way IN, not only on the way out
# --------------------------------------------------------------------------


def _stored_with(store: Path, count: int) -> DiskSeries:
    """Write a one-drive store of `count` hourly samples, past any cap, and load it back.

    Written through `save_history` rather than `record`, which is the point: the
    store is a file a user points at, so nothing guarantees `record` made it.
    """
    samples = tuple(sample(1_000 + hour, errors=hour // 100) for hour in range(count))
    save_history(history_of(*samples), store)
    return load_history(store, hostname="box").series[0]


@pytest.mark.os_agnostic
def test_a_series_past_the_cap_is_thinned_as_it_is_loaded(store: Path) -> None:
    """A store `record` did not write is held to the cap `record` would have kept.

    Measured before the load thinned: two series of 50,000 samples made
    `report` take 6.6 s and 2,566,016 calls to `Sample.counter`.
    """
    count = MAX_SAMPLES_PER_DRIVE * 4
    written = tuple(sample(1_000 + hour, errors=hour // 100) for hour in range(count))

    loaded = _stored_with(store, count).samples

    assert loaded == thin(written, MAX_SAMPLES_PER_DRIVE), "the load kept something other than what record keeps"
    assert loaded[0] == written[0] and loaded[-1] == written[-1], "the baseline or the newest reading was lost"


@pytest.mark.os_agnostic
def test_judging_a_loaded_store_costs_the_same_however_far_past_the_cap_it_grew(tmp_path: Path) -> None:
    """Counted rather than timed: the judgement reads the capped series, so doubling the file changes nothing.

    Before the load thinned, the work doubled with the file, so the two arms
    differed by a factor of two rather than not at all.
    """

    def judged(count: int) -> int:
        series = _stored_with(tmp_path / f"{count}.json", count)
        _, work = work_to_run(lambda: [trend_for(series, kind) for kind in CounterKind])
        return work

    single = judged(MAX_SAMPLES_PER_DRIVE * 4)
    doubled = judged(MAX_SAMPLES_PER_DRIVE * 8)

    assert single > MAX_SAMPLES_PER_DRIVE, f"the smaller arm did {single} units of work, so the counter never ran"
    assert doubled < single * 1.1, f"doubling a store past the cap took {doubled} units of work against {single}"


@pytest.mark.os_agnostic
def test_a_store_naming_one_drive_twice_is_read_as_one_series_and_written_back_as_one(store: Path) -> None:
    """Judged by the first series and rewritten from the last, the first one's samples were lost.

    ``History.for_identity`` returns the first series a drive has, while
    ``record`` folds a new reading into the last and writes that one back under
    both slots. A store gets two series for one drive by being hand-edited or
    merged, which nothing prevents, since it is a file a caller points
    ``--history-file`` at. Merged on the way in, every sample of both survives.
    """
    duplicated = History(
        hostname="box",
        series=(
            DiskSeries(identity="naa.1", model="X", samples=(Sample(power_on_hours=1, captured_at="a", crc_errors=1),)),
            DiskSeries(identity="naa.2", model="Y", samples=(Sample(power_on_hours=7, captured_at="a"),)),
            DiskSeries(identity="naa.1", model="X", samples=(Sample(power_on_hours=2, captured_at="b", crc_errors=3),)),
        ),
    )
    save_history(duplicated, store)

    loaded = load_history(store, hostname="box")

    assert [series.identity for series in loaded.series] == ["naa.1", "naa.2"], loaded.series
    first = loaded.for_identity("naa.1")
    assert first is not None
    assert [sample.power_on_hours for sample in first.samples] == [1, 2]

    disk = Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=5, crc_errors=3))
    save_history(record(loaded, [disk], "c"), store)
    stored = json.loads(store.read_text(encoding="utf-8"))["series"]
    assert [series["identity"] for series in stored] == ["naa.1", "naa.2"], stored
    assert [sample["power_on_hours"] for sample in stored[0]["samples"]] == [1, 2, 5], stored[0]
