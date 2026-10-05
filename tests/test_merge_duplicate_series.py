"""Merging a store's repeated series: who wins a shared hour, and what it costs.

A store reaches here as a file a caller points ``--history-file`` at, so the
number of copies of one drive is the file's to choose. Merging folded each copy
into everything merged so far and rebuilt the series every time, which is
quadratic in the copies: measured, 16,000 copies of one drive took 9 s and
64,000 took 208 s, inside the load every command runs.
"""

from __future__ import annotations

import pytest
from workcount import work_to_run

from lsdsk.domain.history import DiskSeries, Sample, merge_duplicate_series

#: How many times the doubled store may cost the single one. Merging copy by
#: copy grew the work about fourfold per doubling at these sizes; one pass over
#: the copies doubles it.
_ACCEPTABLE_GROWTH = 2.5


def _copy(model: str, *readings: tuple[int, int]) -> DiskSeries:
    """One stored copy of drive ``naa.1``, each reading an (hour, CRC count) pair."""
    samples = tuple(Sample(power_on_hours=hour, captured_at=f"t{hour}", crc_errors=crc) for hour, crc in readings)
    return DiskSeries(identity="naa.1", model=model, samples=samples)


@pytest.mark.os_agnostic
def test_a_repeated_hour_keeps_the_reading_of_the_later_copy() -> None:
    """The rule ``record`` applies: a reading that repeats an hour replaces the earlier one."""
    earlier = _copy("old name", (4, 0), (5, 10))
    later = _copy("new name", (5, 99), (6, 100))
    other = DiskSeries(identity="naa.2", model="Y", samples=(Sample(power_on_hours=1, captured_at="a"),))

    merged = merge_duplicate_series([earlier, other, later])

    assert [series.identity for series in merged] == ["naa.1", "naa.2"], "a series moved from its first position"
    drive = merged[0]
    assert drive.model == "new name", "the model is not the last copy's"
    assert [(sample.power_on_hours, sample.crc_errors) for sample in drive.samples] == [(4, 0), (5, 99), (6, 100)]


@pytest.mark.os_agnostic
def test_a_drive_stored_once_is_returned_as_it_was() -> None:
    """The control: merging is for repeats, so a single copy is not re-sorted or rebuilt."""
    alone = _copy("X", (9, 1), (3, 2))

    assert merge_duplicate_series([alone]) == (alone,)


@pytest.mark.os_agnostic
def test_merging_copies_of_one_drive_grows_with_the_copies_not_their_square() -> None:
    """Counted rather than timed, so a loaded runner cannot fail it or pass it."""

    def merged(copies: int) -> int:
        stored = [_copy("X", (hour, hour)) for hour in range(copies)]
        result, work = work_to_run(lambda: merge_duplicate_series(stored))
        # The control: every copy's reading survived, so the arm merged what
        # it was given rather than returning early.
        assert len(result) == 1 and len(result[0].samples) == copies
        return work

    single = merged(400)
    doubled = merged(800)

    assert single > 400, f"the smaller arm did {single} units of work, so the counter never ran"
    assert doubled < single * _ACCEPTABLE_GROWTH, f"doubling the copies took {doubled} units of work against {single}"
