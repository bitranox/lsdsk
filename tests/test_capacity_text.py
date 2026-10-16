"""A capacity moves to the next unit on the figure it would WRITE, not on the unrounded one.

`size_text` keeps a figure under its scale's step. That promise is about the
text a reader sees, so it is decided on the rounded figure: a value just under
a step that rounds up to it is written in the next unit. The one-decimal limit
below ten is pinned at its own boundary by `test_limit_equalities.py`.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.render import theme
from lsdsk.domain.size_text import format_capacity, format_capacity_both


@pytest.mark.parametrize(
    ("size_bytes", "written"),
    [
        (1_048_575, "1.0MiB"),
        (1_048_064, "1.0MiB"),
        (1_048_063, "1023KiB"),
        (1_024 * 1_048_576 - 1, "1.0GiB"),
        (1023, "1023B"),
        (1024, "1.0KiB"),
        (10_137, "9.9KiB"),
    ],
)
def test_a_binary_figure_never_reaches_its_own_step(size_bytes: int, written: str) -> None:
    assert format_capacity(size_bytes) == written


@pytest.mark.parametrize(
    ("size_bytes", "written"),
    [
        (999_999_999_999, "1.0TB/931GiB"),
        (999_499_999_999, "999GB/931GiB"),
        (999_500, "1.0MB/976KiB"),
        (999_499, "999KB/976KiB"),
        (9_940_000_000, "9.9GB/9.3GiB"),
    ],
)
def test_a_decimal_figure_never_reaches_its_own_step(size_bytes: int, written: str) -> None:
    assert format_capacity_both(size_bytes) == written


def test_the_rendered_size_columns_write_through_the_same_rule() -> None:
    assert theme.format_size(1_048_575) == "1.0MiB"
    assert theme.format_size_both(999_999_999_999) == "1.0TB/931GiB"


def test_no_figure_on_any_scale_is_written_at_its_own_step() -> None:
    # Every boundary on both scales, each approached from just below.
    for exponent in range(1, 6):
        for step in (1000, 1024):
            for offset in (1, step**exponent // 2000 + 1):
                text = format_capacity_both(step**exponent - offset)
                for figure in text.split("/"):
                    number = figure.rstrip("BKMGTPi")
                    own_step = "1024" if "i" in figure else "1000"
                    assert number != own_step, (step, exponent, offset, text)


@pytest.mark.parametrize(
    ("size_bytes", "written"),
    [
        (9_949, "9.9KB/9.7KiB"),
        (9_960, "10KB/9.7KiB"),
        (9_999, "10KB/9.8KiB"),
        (10_000, "10KB/9.8KiB"),
        (10 * 1000**3 - 1, "10GB/9.3GiB"),
        (10 * 1024 - 1, "10KB/10KiB"),
        (10 * 1024**3 - 1, "11GB/10GiB"),
    ],
)
def test_a_figure_that_rounds_to_ten_is_written_whole_like_every_figure_from_ten_on(
    size_bytes: int, written: str
) -> None:
    """The one-decimal rule is about the figure as written, as the step rule is.

    Judged on the unrounded value, 9.96 KB stayed under ten and printed
    ``10.0KB``, a figure of ten carrying the decimal every figure from ten on
    drops, so one capacity read two ways in one column.
    """
    assert format_capacity_both(size_bytes) == written
