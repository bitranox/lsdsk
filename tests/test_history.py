"""Trend rules: turning a stored counter into a rate, or refusing to.

The counters a drive publishes are lifetime totals held in its own non-volatile
attribute table, so they survive reboots and say nothing about when the damage
happened.  A drive with 462640 CRC errors that has not moved in a year and one
gaining a thousand an hour report the same number.  These tests pin the rules
that tell them apart, and pin just as hard the cases where the honest answer is
that the samples cannot say.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.history import (
    CounterKind,
    DiskSeries,
    History,
    Sample,
    TrendVerdict,
    identity_of,
    record,
    sample_from,
    trend_for,
    untracked_disks,
)
from lsdsk.domain.models import Disk, Health
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"

# The wall clock plays no part in a rate; these exist only to fill the field.
T0 = "2026-08-05T01:23:00+00:00"
T1 = "2026-08-05T17:03:00+00:00"


def load(host: str) -> dict[str, object]:
    """Read one hardware capture."""
    with (FIXTURE_DIR / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, object] = json.load(handle)
    return payload


def series_of(*samples: Sample) -> DiskSeries:
    """Wrap samples in a series, oldest first."""
    return DiskSeries(identity="naa.test", model="Test Drive", samples=samples)


def crc_sample(hours: int, errors: int, when: str = T0) -> Sample:
    """A sample carrying only power-on hours and a CRC count."""
    return Sample(power_on_hours=hours, captured_at=when, crc_errors=errors)


# --------------------------------------------------------------------------
# The verdicts
# --------------------------------------------------------------------------


def test_one_sample_cannot_produce_a_rate() -> None:
    trend = trend_for(series_of(crc_sample(1000, 50)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.FIRST_SAMPLE
    assert trend.per_hour is None
    assert trend.latest == 50


def test_an_empty_series_cannot_produce_a_rate() -> None:
    trend = trend_for(series_of(), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.FIRST_SAMPLE
    assert trend.latest is None


def test_a_rising_counter_reports_errors_per_power_on_hour() -> None:
    trend = trend_for(series_of(crc_sample(1000, 100), crc_sample(1010, 300)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.RISING
    assert trend.delta == 200
    assert trend.span_hours == 10
    assert trend.per_hour == pytest.approx(20.0)


def test_a_span_under_an_hour_refuses_to_rate() -> None:
    """Two samples in the same power-on hour divide by nothing."""
    trend = trend_for(series_of(crc_sample(1000, 100), crc_sample(1000, 900)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.TOO_CLOSE
    assert trend.per_hour is None


def test_a_counter_that_fell_is_a_reset_never_a_negative_rate() -> None:
    trend = trend_for(series_of(crc_sample(1000, 900), crc_sample(1100, 5)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.RESET
    assert trend.per_hour is None


def test_power_on_hours_that_fell_is_a_reset() -> None:
    """A drive swapped behind an unchanged identity, or a firmware reset."""
    trend = trend_for(series_of(crc_sample(9000, 10), crc_sample(80, 12)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.RESET


# --------------------------------------------------------------------------
# Quiet is self-calibrating, and that is the whole point
# --------------------------------------------------------------------------


def test_quiet_is_claimed_when_the_drives_own_history_predicted_errors() -> None:
    """462640 errors over 31486 hours predicts ~235 in a 16 hour span. None came."""
    trend = trend_for(series_of(crc_sample(31470, 462640), crc_sample(31486, 462640)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.QUIET
    assert trend.delta == 0
    assert trend.expected_from_lifetime is not None
    assert trend.expected_from_lifetime > DEFAULT_THRESHOLDS.quiet_expected_min


def test_a_slow_trickle_over_a_short_span_admits_it_cannot_tell() -> None:
    """430 errors over 10513 hours predicts 0.6 in 15 hours; silence proves nothing."""
    trend = trend_for(series_of(crc_sample(10498, 430), crc_sample(10513, 430)), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.TOO_CLOSE
    assert trend.expected_from_lifetime is not None
    assert trend.expected_from_lifetime < DEFAULT_THRESHOLDS.quiet_expected_min


def test_a_quiet_span_is_measured_from_where_the_counter_last_moved() -> None:
    """Frequent sampling must not shrink the evidence to the last interval.

    Sampling hourly would otherwise cap every quiet span at one hour, and no
    silence could ever amount to evidence no matter how long it lasted.
    """
    samples = [crc_sample(1000, 500), *(crc_sample(1000 + n, 900) for n in range(1, 6))]
    trend = trend_for(series_of(*samples), CounterKind.CRC_ERRORS)
    assert trend.span_hours == 4  # hour 1001 through 1005, not the last hour alone


def test_a_long_quiet_run_beats_the_threshold_that_the_last_interval_alone_would_miss() -> None:
    samples = [crc_sample(90, 20), *(crc_sample(100 + n * 50, 100) for n in range(6))]
    trend = trend_for(series_of(*samples), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.QUIET
    assert trend.span_hours == 250


def test_quiet_needs_a_lifetime_rate_so_a_zero_hour_drive_cannot_claim_it() -> None:
    trend = trend_for(series_of(crc_sample(0, 0), crc_sample(0, 0)), CounterKind.CRC_ERRORS)
    assert trend.verdict is not TrendVerdict.QUIET


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_wwn_is_preferred_as_identity() -> None:
    disk = Disk(node="sda", path="/dev/sda", model="X", serial="SER1", wwn="naa.33bdd4dc46f4c149")
    assert identity_of(disk) == "naa.33bdd4dc46f4c149"


def test_serial_is_the_fallback_where_there_is_no_wwn() -> None:
    """Windows never populates wwn, so serial carries identity there."""
    disk = Disk(node="PhysicalDrive0", path="\\\\.\\PHYSICALDRIVE0", model="X", serial="EAS39CR")
    assert identity_of(disk) == "EAS39CR"


def test_a_disk_with_neither_is_not_tracked() -> None:
    """The device node reorders across reboots, so it is not an identity."""
    assert identity_of(Disk(node="sda", path="/dev/sda", model="X")) is None


def test_a_disk_without_power_on_hours_cannot_be_placed_on_the_time_axis() -> None:
    disk = Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(crc_errors=5))
    assert sample_from(disk, T0) is None


def test_a_sample_carries_the_counters_and_not_the_temperature() -> None:
    disk = Disk(
        node="sda",
        path="/dev/sda",
        model="X",
        wwn="naa.1",
        health=Health(power_on_hours=100, crc_errors=5, temperature_c=41),
    )
    sample = sample_from(disk, T0)
    assert sample is not None
    assert sample.crc_errors == 5
    assert sample.power_on_hours == 100
    assert not hasattr(sample, "temperature_c")


# --------------------------------------------------------------------------
# The real hardware pair
# --------------------------------------------------------------------------


def paired_series() -> dict[str, DiskSeries]:
    """Build one series per drive from the two real pbs captures."""
    early, late = build_from(load("linux-sas-hba")), build_from(load("linux-sas-hba-later"))
    by_identity: dict[str, list[Sample]] = {}
    for inventory, when in ((early, T0), (late, T1)):
        for disk in inventory.disks:
            identity = identity_of(disk)
            sample = sample_from(disk, when)
            if identity is not None and sample is not None:
                by_identity.setdefault(identity, []).append(sample)
    return {
        identity: DiskSeries(identity=identity, model="", samples=tuple(samples))
        for identity, samples in by_identity.items()
    }


def test_the_two_captures_pair_up_by_wwn() -> None:
    """Every drive is found in both, which is what makes wwn a usable identity.

    No drive happened to move node between these two captures, so a count alone
    would pass just as happily against ``sda``, and the test would be blind to
    the very substitution it exists to rule out. So the keys are asserted to be
    nothing a device node could ever be.
    """
    paired = paired_series()
    complete = [s for s in paired.values() if len(s.samples) == 2]
    assert len(complete) >= 18

    nodes = {disk.node for disk in build_from(load("linux-sas-hba")).disks}
    assert nodes  # the comparison below is worthless against an empty set
    assert not (nodes & {series.identity for series in complete})


@pytest.mark.parametrize(
    ("identity", "expected", "low", "high"),
    [
        # 2196127 errors, gaining ~1100 an hour: the fault is live.
        ("naa.33bdd4dc46f4c149", TrendVerdict.RISING, 500.0, 2000.0),
        # 99361 errors, gaining about one an hour.
        ("naa.405e7d1d9b1fdb6b", TrendVerdict.RISING, 0.2, 5.0),
    ],
)
def test_a_live_fault_is_reported_as_rising(identity: str, expected: TrendVerdict, low: float, high: float) -> None:
    trend = trend_for(paired_series()[identity], CounterKind.CRC_ERRORS)
    assert trend.verdict is expected
    assert trend.per_hour is not None
    assert low <= trend.per_hour <= high


def test_a_dead_fault_is_reported_as_quiet_despite_a_huge_total() -> None:
    """Nearly half a million CRC errors, and not one of them recent."""
    trend = trend_for(paired_series()["naa.62a2aab8f1319f8d"], CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.QUIET
    assert trend.latest is not None
    assert trend.latest > 400000


def test_the_verdict_does_not_depend_on_the_wall_clock() -> None:
    """Both samples stamped at the same instant still yield the right rate.

    Power-on hours are the time base precisely so a clock step, a timezone or a
    machine that was switched off cannot move the answer.
    """
    early, late = build_from(load("linux-sas-hba")), build_from(load("linux-sas-hba-later"))
    samples: list[Sample] = []
    for inventory in (early, late):
        for disk in inventory.disks:
            if identity_of(disk) == "naa.33bdd4dc46f4c149":
                sample = sample_from(disk, T0)  # the SAME stamp for both
                assert sample is not None
                samples.append(sample)
    trend = trend_for(series_of(*samples), CounterKind.CRC_ERRORS)
    assert trend.verdict is TrendVerdict.RISING
    assert trend.per_hour is not None
    assert trend.per_hour > 500.0


# --------------------------------------------------------------------------
# History container
# --------------------------------------------------------------------------


def test_history_finds_a_series_by_identity() -> None:
    series = DiskSeries(identity="naa.1", model="X", samples=(crc_sample(10, 0),))
    history = History(hostname="box", series=(series,))
    assert history.for_identity("naa.1") is series
    assert history.for_identity("naa.missing") is None


def test_every_counter_kind_can_be_read_off_a_sample() -> None:
    """A kind with no accessor would silently trend as absent forever."""
    sample = Sample(
        power_on_hours=10,
        captured_at=T0,
        crc_errors=1,
        reallocated_sectors=2,
        pending_sectors=3,
        uncorrectable_sectors=4,
        media_errors=5,
        percent_used=6,
        bytes_written=7,
        unsafe_shutdowns=8,
        error_log_entries=9,
        power_cycles=11,
    )
    assert all(sample.counter(kind) is not None for kind in CounterKind)


# --------------------------------------------------------------------------
# An identity two drives share is not an identity
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_two_drives_sharing_a_serial_are_not_spliced_into_one_series() -> None:
    """Folding the second onto the first turns one instant into a rate.

    Serials collide in practice, a virtual machine hands out shared synthetic
    ones, and on Windows the serial IS the identity, so this is reachable.
    Measured before the fix: two drives at 0 and 8000 CRC errors produced one
    series holding both samples, a confident "about 200 an hour", and a warning
    escalated to critical that no hardware supported.
    """
    first = Disk(
        node="sdx",
        path="/dev/sdx",
        model="Drive A",
        serial="0123456789ABCDEF",
        health=Health(power_on_hours=100, crc_errors=0),
    )
    second = Disk(
        node="sdy",
        path="/dev/sdy",
        model="Drive B",
        serial="0123456789ABCDEF",
        health=Health(power_on_hours=140, crc_errors=8000),
    )
    assert identity_of(first) == identity_of(second), "the fixture no longer collides, so this proves nothing"

    stored = record(History(hostname="box"), [first, second], "t")

    assert stored.series == (), "a shared identity was recorded anyway"
    reasons = untracked_disks([first, second])
    assert len(reasons) == 2, "a drive dropped from the history was not explained"
    assert all("shares an identity" in reason for reason in reasons)


@pytest.mark.os_agnostic
def test_a_unique_serial_is_still_recorded() -> None:
    """The control: the guard must not stop recording drives that differ."""
    first = Disk(
        node="sdx",
        path="/dev/sdx",
        model="Drive A",
        serial="AAAA",
        health=Health(power_on_hours=100, crc_errors=0),
    )
    second = Disk(
        node="sdy",
        path="/dev/sdy",
        model="Drive B",
        serial="BBBB",
        health=Health(power_on_hours=140, crc_errors=8000),
    )
    stored = record(History(hostname="box"), [first, second], "t")
    assert len(stored.series) == 2, "unique drives stopped being recorded"
    assert untracked_disks([first, second]) == ()
