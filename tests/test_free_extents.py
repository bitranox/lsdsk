"""The free-extent tally both readers fold into, its capture model and its domain value."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lsdsk.adapters.hw.capture import FreeExtentsEntry
from lsdsk.adapters.hw.decode.free_extents import MAX_FREE_EXTENTS, FreeExtentCapError, FreeExtentTally
from lsdsk.adapters.hw.free_space import free_space_of
from lsdsk.domain.fragmentation import FREE_SPACE_BUCKETS


def test_a_tally_counts_sums_and_buckets_every_extent() -> None:
    tally = FreeExtentTally()
    for length in (4096, 4096, 1 << 30):
        tally.add(length)
    summary = tally.summary()
    assert (summary["count"], summary["total"], summary["largest"]) == (3, 8192 + (1 << 30), 1 << 30)
    histogram = summary["histogram"]
    assert isinstance(histogram, list)
    assert len(histogram) == FREE_SPACE_BUCKETS
    assert (histogram[3], histogram[20], sum(histogram)) == (8192, 1 << 30, 8192 + (1 << 30))


def test_an_empty_extent_is_not_an_extent() -> None:
    tally = FreeExtentTally()
    tally.add(0)
    assert tally.summary()["count"] == 0


def test_the_extent_past_the_cap_stops_the_tally_naming_the_cap() -> None:
    tally = FreeExtentTally(cap=3)
    for _ in range(3):
        tally.add(512)
    with pytest.raises(FreeExtentCapError, match="more than 3 free extents"):
        tally.add(512)


def test_the_shipped_cap_is_one_million() -> None:
    assert MAX_FREE_EXTENTS == 1_000_000
    assert f"{MAX_FREE_EXTENTS:,}" in str(FreeExtentCapError(MAX_FREE_EXTENTS))


def test_a_summary_becomes_a_free_space_value_with_its_score() -> None:
    tally = FreeExtentTally()
    tally.add(4096)
    space = free_space_of(FreeExtentsEntry.model_validate(tally.summary()))
    assert space is not None
    assert (space.fragmentation_percent, space.free_extent_count, space.largest_free_extent_bytes) == (93, 1, 4096)


def test_a_refused_or_absent_reading_is_no_value() -> None:
    assert free_space_of(None) is None
    assert free_space_of(FreeExtentsEntry(error="Operation not permitted")) is None


def test_no_free_space_reads_as_a_value_with_no_score() -> None:
    space = free_space_of(FreeExtentsEntry.model_validate(FreeExtentTally().summary()))
    assert space is not None
    assert (space.free_extent_count, space.fragmentation_percent) == (0, None)


@pytest.mark.parametrize("histogram", [[1, 2, 3], [0] * 22])
def test_a_histogram_of_another_length_is_refused_at_the_parse(histogram: list[int]) -> None:
    with pytest.raises(ValidationError, match="21"):
        FreeExtentsEntry.model_validate({"count": 1, "total": 1, "largest": 1, "histogram": histogram})


@pytest.mark.parametrize("value", [-1, 2**64])
def test_a_figure_outside_unsigned_64_bits_is_refused(value: int) -> None:
    with pytest.raises(ValidationError, match=r"greater than or equal to 0|less than or equal to"):
        FreeExtentsEntry(count=value, total=0, largest=0)


def test_free_extents_adding_up_past_64_bits_stop_the_tally() -> None:
    tally = FreeExtentTally()
    tally.add(2**63)
    tally.add(2**63 - 1)  # exactly 2**64 - 1: the largest total a capture holds
    with pytest.raises(ValueError, match=r"add up to more than 18,446,744,073,709,551,615 bytes"):
        tally.add(1)
    assert tally.summary()["total"] == 2**64 - 1  # the refused extent was not folded in


def test_a_summary_with_no_histogram_has_its_counts_and_no_score() -> None:
    # A summary carrying counts but no histogram (empty, as the capture model allows) is not scored.
    space = free_space_of(FreeExtentsEntry(count=0, total=0, largest=0))
    assert space is not None
    assert (space.fragmentation_percent, space.free_extent_count, space.histogram) == (None, 0, ())
