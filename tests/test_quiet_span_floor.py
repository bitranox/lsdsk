"""A quiet counter is judged against the configured span floor, not only its expectation.

``_quiet`` calls a silence convincing only when the span is long enough AND the
drive's own rate predicted errors in it. At the shipped floor of one hour the
first leg is dead, because ``_quiet_run_start`` never returns a span shorter than
the previous reading, so a mutant dropping it survived every test. A floor the
operator raised is what makes the leg live, and what these tests set.
"""

from __future__ import annotations

from lsdsk.domain.history import CounterKind, DiskSeries, Sample, TrendVerdict, trend_for
from lsdsk.domain.thresholds import Thresholds

# A lifetime rate high enough that the expectation leg is satisfied at every span
# below, so the floor is the only thing that can separate the two verdicts.
LIFETIME_ERRORS = 5000
LATEST_HOURS = 1000


def quiet_series(span: int) -> DiskSeries:
    """A counter holding one value from ``span`` hours ago until now."""
    rows = tuple(
        Sample(power_on_hours=hours, captured_at=f"t{hours}", crc_errors=LIFETIME_ERRORS)
        for hours in (LATEST_HOURS - span, LATEST_HOURS)
    )
    return DiskSeries(identity="naa.1", model="X", samples=rows)


def test_a_silence_shorter_than_the_configured_floor_is_too_close() -> None:
    """Twenty quiet hours do not convince a fleet that asked for twenty-four."""
    trend = trend_for(quiet_series(20), CounterKind.CRC_ERRORS, Thresholds(min_span_hours=24))

    assert trend.span_hours == 20
    assert trend.expected_from_lifetime is not None
    assert trend.expected_from_lifetime >= Thresholds().quiet_expected_min, "the expectation leg must not decide this"
    assert trend.verdict is TrendVerdict.TOO_CLOSE


def test_a_silence_as_long_as_the_configured_floor_is_quiet() -> None:
    """The control: the same series at exactly the floor is convincing."""
    trend = trend_for(quiet_series(24), CounterKind.CRC_ERRORS, Thresholds(min_span_hours=24))

    assert trend.span_hours == 24
    assert trend.verdict is TrendVerdict.QUIET
