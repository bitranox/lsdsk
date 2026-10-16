"""A rising counter's rate is written one way, in the finding and in the trend table.

The finding that a counter is rising - the critical one - wrote its rate to one
decimal, so a drive gaining one CRC error in thirty power-on hours was "about
0.0 an hour": a rate of zero, in the sentence explaining why the drive is
failing now. The trend table beside it said ``0.033``. Two formatters, one in
the domain and one in the render layer, had drifted apart; there is one now, in
the domain, and both write through it.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.render.trend import trend_row
from lsdsk.domain.diagnostics import refine
from lsdsk.domain.enums import Severity
from lsdsk.domain.history import CounterKind, Trend, TrendVerdict
from lsdsk.domain.models import Finding
from lsdsk.domain.rate_text import format_rate


def _rising(delta: int, span_hours: int) -> Trend:
    return Trend(
        kind=CounterKind.CRC_ERRORS,
        verdict=TrendVerdict.RISING,
        latest=1000 + delta,
        delta=delta,
        span_hours=span_hours,
        per_hour=delta / span_hours,
        expected_from_lifetime=None,
    )


#: One error in thirty hours, the reported case; one in three thousand, which a
#: fixed three decimals still wrote as zero; and the ordinary range above.
CASES = [(1, 30), (1, 3000), (1, 50_000), (16, 16), (16_642, 15), (3, 31)]


@pytest.mark.parametrize(("delta", "span"), CASES)
def test_the_finding_and_the_trend_table_write_a_rising_rate_the_same_way(delta: int, span: int) -> None:
    trend = _rising(delta, span)
    finding = refine(Finding(severity=Severity.WARNING, subject="/dev/sdd", title="has errors"), trend)
    table = trend_row("/dev/sdd", CounterKind.CRC_ERRORS, trend)["rate"][0]
    assert f"about {table} an hour" in finding.detail, f"table {table!r}, finding {finding.detail!r}"


@pytest.mark.parametrize(("delta", "span"), CASES)
def test_a_rising_rate_is_never_written_as_zero(delta: int, span: int) -> None:
    written = format_rate(delta / span)
    assert float(written) > 0, f"{delta} in {span}h written as {written!r}"


def test_the_reported_rate_reads_as_the_table_already_wrote_it() -> None:
    finding = refine(Finding(severity=Severity.WARNING, subject="/dev/sdd", title="has errors"), _rising(1, 30))
    assert "about 0.033 an hour" in finding.detail, finding.detail
    assert "0.0 an hour" not in finding.detail, finding.detail
