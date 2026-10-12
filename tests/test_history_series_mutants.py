"""Pins the history rules a one-row series cannot tell apart from a wrong rule.

Each test reads a series long enough that the rule's wrong spelling gives a
different answer: a repeated hour inside a longer series, a duplicate hour in
the middle of one, a newest reading whose every earlier row is a different run,
and a series stored with no rows.
"""

from __future__ import annotations

from lsdsk.domain.history import (
    CounterKind,
    DiskSeries,
    History,
    Sample,
    TrendVerdict,
    has_new_readings,
    record,
    trend_for,
)
from lsdsk.domain.models import Disk, Health


def _drive(hours: int, errors: int) -> Disk:
    """A trackable drive carrying one CRC count on its own clock."""
    return Disk(
        node="sdx",
        path="/dev/sdx",
        model="Drive A",
        wwn="naa.1",
        health=Health(power_on_hours=hours, crc_errors=errors),
    )


def _series(*rows: tuple[int, int]) -> DiskSeries:
    """A series of (power-on hours, CRC count) rows, oldest first."""
    samples = tuple(Sample(power_on_hours=h, captured_at=f"t{i}", crc_errors=c) for i, (h, c) in enumerate(rows))
    return DiskSeries(identity="naa.1", model="X", samples=samples)


def test_a_repeated_hour_replaces_only_the_newest_row_of_a_longer_series() -> None:
    """Hours 1000, 1001, 1002 then 1002 again: the earlier rows stay, the newest count is replaced."""
    history = History(hostname="box")
    for index, (hours, errors) in enumerate([(1000, 10), (1001, 20), (1002, 30), (1002, 35)]):
        history = record(history, [_drive(hours, errors)], f"t{index}")

    samples = history.series[0].samples

    assert [s.power_on_hours for s in samples] == [1000, 1001, 1002], "a repeated hour collapsed the series"
    assert [s.crc_errors for s in samples] == [10, 20, 35]


def test_a_duplicate_hour_inside_a_series_still_anchors_a_span() -> None:
    """Hours 10, 10, 20: the second 10 is a real anchor, so the span runs from it, not from the first 10."""
    trend = trend_for(_series((10, 100), (10, 150), (20, 300)), CounterKind.CRC_ERRORS)

    assert trend.verdict is TrendVerdict.RISING
    assert trend.delta == 150, "the span was anchored on the older of two rows sharing an hour"
    assert trend.span_hours == 10


def test_a_newest_hour_repeated_over_rows_that_are_all_another_run_is_a_reset() -> None:
    """Hours 2000, 1000, 1000: no earlier row anchors, yet the newest hour differs from the 2000 row.

    The reading to compare with is the first earlier row in another hour, even
    unanchored; falling back to the row directly behind compared the newest
    reading with itself and hid that the clock had come back down.
    """
    trend = trend_for(_series((2000, 5), (1000, 5), (1000, 5)), CounterKind.CRC_ERRORS)

    assert trend.verdict is TrendVerdict.RESET


def test_a_stored_series_with_no_rows_has_something_new_to_say() -> None:
    """A drive whose stored series is empty is as good as never seen: the run records it."""
    empty = DiskSeries(identity="naa.1", model="X", samples=())
    history = History(hostname="box", series=(empty,))

    assert has_new_readings(history, [_drive(5, 1)]) is True
