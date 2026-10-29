"""The free-extent tally both readers fold into, its capture model and its domain value."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from lsdsk.adapters.hw.capture import FreeExtentsEntry
from lsdsk.adapters.hw.decode.free_extents import MAX_FREE_EXTENTS, FreeExtentCapError, FreeExtentTally
from lsdsk.adapters.hw.free_space import free_space_of
from lsdsk.domain.fragmentation import FREE_SPACE_BUCKETS

if TYPE_CHECKING:
    from collections.abc import Mapping


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
    with pytest.raises(
        ValidationError,
        match=(
            r"histogram\n  Value error, "
            rf"a free-space histogram has {FREE_SPACE_BUCKETS} buckets, not {len(histogram)}"
        ),
    ):
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


_EMPTY_BUCKETS = [0] * FREE_SPACE_BUCKETS


@pytest.mark.parametrize("bucket", [-1, 2**64])
def test_a_histogram_bucket_outside_unsigned_64_bits_is_refused(bucket: int) -> None:
    entry = {"count": 1, "total": 1, "largest": 1, "histogram": [bucket, *_EMPTY_BUCKETS[1:]]}
    with pytest.raises(ValidationError, match=r"histogram\.0[\s\S]*(greater than or equal to 0|less than or equal to)"):
        FreeExtentsEntry.model_validate(entry)


def test_a_summary_with_extents_but_no_histogram_keeps_its_counts_and_has_no_score() -> None:
    # Two extents counted, no histogram recorded: the counts stand, and nothing is scored from a histogram not there.
    space = free_space_of(FreeExtentsEntry(count=2, total=8192, largest=4096))
    assert space is not None
    assert (space.free_extent_count, space.free_extent_bytes, space.fragmentation_percent) == (2, 8192, None)


def _histogram(total: int) -> list[int]:
    return [0, 0, 0, total, *_EMPTY_BUCKETS[4:]]


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"count": 1, "largest": 4096}, "count, total and largest are read together"),
        ({"count": 1, "total": 4096}, "count, total and largest are read together"),
        ({"total": 4096, "largest": 4096}, "count, total and largest are read together"),
        ({"error": "refused", "count": 1, "total": 4096, "largest": 4096}, "a refused reading carries no figures"),
        ({"error": "refused", "histogram": _histogram(4096)}, "a refused reading carries no figures"),
        ({"histogram": _histogram(4096)}, "a histogram without the figures it sums"),
        (
            {"count": 1, "total": 4096, "largest": 4096, "histogram": _histogram(4095)},
            "sums to 4095, not the total 4096",
        ),
        ({"count": 1, "total": 4096, "largest": 8192}, "largest extent 8192 is more than the total 4096"),
        ({"count": 1, "total": 8192, "largest": 4096}, "1 extents of at most 4096 bytes cannot hold 8192"),
        ({"count": 0, "total": 4096, "largest": 4096}, "0 extents of at most 4096 bytes cannot hold 4096"),
        ({"count": 3, "total": 2, "largest": 1}, "3 extents cannot fit in 2 bytes"),
    ],
    ids=[
        "count-without-total",
        "count-without-largest",
        "figures-without-count",
        "refused-with-figures",
        "refused-with-histogram",
        "histogram-without-figures",
        "histogram-disagrees",
        "largest-past-total",
        "total-past-count-times-largest",
        "no-extents-with-free-bytes",
        "more-extents-than-bytes",
    ],
)
def test_a_summary_whose_figures_disagree_is_refused_at_the_parse(entry: dict[str, object], reason: str) -> None:
    # A live tally cannot write any of these; only an edited replay can, and the model refuses it as a bad file.
    with pytest.raises(ValidationError, match=reason):
        FreeExtentsEntry.model_validate(entry)


def _tally_of(*lengths: int) -> dict[str, int | list[int]]:
    tally = FreeExtentTally()
    for length in lengths:
        tally.add(length)
    return tally.summary()


@pytest.mark.parametrize(
    "entry",
    [
        _tally_of(),
        _tally_of(1),
        _tally_of(4096, 4096, 1 << 30),
        _tally_of(2**63, 2**63 - 1),
        {"error": "refused"},
        {},
        {"count": 0, "total": 0, "largest": 0},
    ],
    ids=["empty", "one-byte", "several", "at-the-64-bit-total", "refused", "nothing", "counts-without-histogram"],
)
def test_every_summary_a_reader_writes_is_accepted(entry: Mapping[str, int | str | list[int]]) -> None:
    # The control for the refusals above: the shapes a reader really writes all parse.
    assert FreeExtentsEntry.model_validate(entry).model_dump(exclude_defaults=True) == {
        key: tuple(value) if isinstance(value, list) else value for key, value in entry.items()
    }
