"""``record`` keeps a series at the cap it is given, never one over.

``record`` thins each series after folding a reading in, so the store a run
writes holds at most ``cap`` samples per drive. The other history tests either
pass no cap or one far above their series, so a cap that was ignored, or applied
before the new reading was folded in, left the file one sample over and nothing
noticed.
"""

from __future__ import annotations

import pytest

from lsdsk.domain.history import History, record
from lsdsk.domain.models import Disk, Health


def disk_at(hours: int) -> Disk:
    """One drive, read when its own clock stood at ``hours``."""
    return Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=hours, crc_errors=1))


@pytest.mark.parametrize("cap", [5, 8, 20])
def test_a_series_never_holds_more_samples_than_the_cap_after_each_reading(cap: int) -> None:
    """Fold forty readings one at a time, as forty runs do, and measure every step."""
    history = History(hostname="box")
    sizes: list[int] = []
    for hours in range(100, 140):
        history = record(history, [disk_at(hours)], f"t{hours}", cap=cap)
        sizes.append(len(history.series[0].samples))

    assert all(size <= cap for size in sizes), f"a series sat over the cap of {cap}: {sizes}"
    assert max(sizes) == cap, f"the series was never filled to the cap, so the test saw no thinning: {sizes}"


def test_a_reading_past_the_cap_keeps_the_baseline_and_the_newest() -> None:
    """Thinning is not truncation: the first row and the row just added both survive."""
    history = History(hostname="box")
    for hours in range(100, 130):
        history = record(history, [disk_at(hours)], f"t{hours}", cap=6)

    kept = [sample.power_on_hours for sample in history.series[0].samples]
    assert kept[0] == 100
    assert kept[-1] == 129
