"""A ``--set`` integer wider than a float holds keeps its digits, however it is written."""

from __future__ import annotations

import pytest

from lsdsk.adapters.config.overrides import coerce_value

WIDE = "99999999999999999999999"


@pytest.mark.parametrize("typed", [f" {WIDE}", f"{WIDE} ", f"\t{WIDE}\n"])
def test_a_wide_integer_with_blanks_around_it_is_still_read_exactly(typed: str) -> None:
    """``orjson`` skips the blanks and reads 1e+23, so the digits were lost the way an unpadded value's once were."""
    value = coerce_value(typed)

    assert value == int(WIDE)
    assert isinstance(value, int)


def test_a_wide_negative_integer_is_read_exactly() -> None:
    """The negative arm: the sign is not a digit, and the digits after it are all kept."""
    value = coerce_value(f"-{WIDE}")

    assert value == -int(WIDE)
    assert isinstance(value, int)


def test_a_wide_integer_inside_a_word_stays_text() -> None:
    """The control: blanks are stripped only to read a number, never to turn text into one."""
    assert coerce_value(f"{WIDE}x") == f"{WIDE}x"
