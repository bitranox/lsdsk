"""A drive worn exactly to the configured floor earns its trend row.

``display.wear_row_floor_percent`` is documented as the wear BELOW which the
trend view stays quiet, so a drive AT the floor is shown. Only the shipped
default and far-from-it values were exercised, so `>=` could become `>` with the
suite green and a fleet that set the floor to the wear it cared about would lose
exactly that row.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.render.trend import worth_showing
from lsdsk.domain.history import CounterKind, Trend, TrendVerdict


def quiet_wear(percent: int) -> Trend:
    """A wear reading with no rate to project from, so only the floor can earn its row."""
    return Trend(
        kind=CounterKind.PERCENT_USED,
        verdict=TrendVerdict.TOO_CLOSE,
        latest=percent,
        delta=0,
        span_hours=16,
        per_hour=None,
        expected_from_lifetime=0.0,
    )


@pytest.mark.parametrize(("percent", "shown"), [(9, False), (10, True), (11, True)])
def test_wear_is_shown_from_the_floor_upward(percent: int, shown: bool) -> None:
    """Below the floor is quiet; at it and above it earns a row."""
    assert worth_showing(CounterKind.PERCENT_USED, quiet_wear(percent), wear_floor=10) is shown
