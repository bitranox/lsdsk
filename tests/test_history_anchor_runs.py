"""Which row starts the newest run of a drive's clock, over series that reset more than once.

``trend_for`` anchors a span at the newest reading's own run. A drive swapped or
reset twice leaves several runs in one series, and the newest one is judged from
its own first row, not from a row of an older run and not from nothing.
"""

from __future__ import annotations

from lsdsk.domain.history import CounterKind, DiskSeries, Sample, TrendVerdict, trend_for

# A counter holding one high value on every row, so the lifetime expectation is met at any span
# and only the choice of anchor can separate the verdicts.
HELD_VALUE = 5000


def _held_counter(hours: list[int]) -> DiskSeries:
    """A series whose CRC counter never moves, read at the given power-on hours in order."""
    rows = tuple(
        Sample(power_on_hours=hour, captured_at=f"t{index}", crc_errors=HELD_VALUE) for index, hour in enumerate(hours)
    )
    return DiskSeries(identity="naa.1", model="X", samples=rows)


def test_the_newest_run_after_two_resets_is_judged_from_its_own_first_row() -> None:
    """Hours 1000, 5, 50, 3, 13: the clock restarted at 5 and again at 3, so the run is 3 to 13.

    A running maximum carried over the first reset made the row at hour 3 look
    like a misread of the row at hour 5 and left the run with no anchor at all,
    which reported a ten-hour silence as too close to rate.
    """
    trend = trend_for(_held_counter([1000, 5, 50, 3, 13]), CounterKind.CRC_ERRORS)

    assert trend.span_hours == 10
    assert trend.verdict is TrendVerdict.QUIET


def test_a_row_below_one_the_newest_reading_regained_is_a_misread_not_a_restart() -> None:
    """Hours 1000, 5, 1001: the clock carried on past 1000, so the row at 5 was a misread, not a restart."""
    trend = trend_for(_held_counter([1000, 5, 1001]), CounterKind.CRC_ERRORS)

    assert trend.span_hours == 1
    assert trend.verdict is TrendVerdict.TOO_CLOSE


def test_a_row_at_the_newest_hour_still_makes_a_lower_row_a_stray() -> None:
    """Hours 1000, 5, 1000: the newest reading is back AT 1000, so the row at 5 anchors nothing.

    Taking the equal row as not counting stood the lower one up as an anchor and
    rated a silence of 995 hours that the series never recorded.
    """
    trend = trend_for(_held_counter([1000, 5, 1000]), CounterKind.CRC_ERRORS)

    assert trend.span_hours == 0
    assert trend.verdict is TrendVerdict.TOO_CLOSE
