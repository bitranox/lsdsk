"""Boundaries of the capacity writer and of the cut that keeps a count whole.

Each is a single figure at the edge of a rule, where an off-by-one or a
loop that runs one unit too far reads the same as the right answer on every
ordinary input.
"""

from __future__ import annotations

from lsdsk.adapters.render.layout import clip_keeping
from lsdsk.domain.size_text import format_capacity

PEBIBYTE = 1024**5


def test_the_last_unit_keeps_one_decimal_below_ten() -> None:
    """The largest unit is written by the same rule as every other: one decimal below ten."""
    assert format_capacity(5 * PEBIBYTE) == "5.0PiB"


def test_a_figure_past_the_last_unit_stays_in_it_and_is_not_divided_again() -> None:
    """There is no unit after PiB, so 2048 PiB is written as 2048PiB rather than scaled down to 2.0."""
    assert format_capacity(2048 * PEBIBYTE) == "2048PiB"


def test_a_count_is_kept_when_the_room_before_it_is_exactly_one_character_and_the_mark() -> None:
    """Two cells before the count is the least the cut keeps the count for, not the most it refuses."""
    assert clip_keeping("abcdef +2", 5, " +2") == "a> +2"
    assert clip_keeping("abcdef +2", 4, " +2") == "abc>"
