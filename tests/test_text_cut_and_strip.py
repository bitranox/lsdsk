"""What `lsdsk.domain.text` cuts, strips and bounds, pinned at its edges.

The doctests there pass for a cut with no marker as long as the option flag that
lets ``...`` stand for anything is on, so the marker and the bound are asserted
here on exact strings.
"""

from __future__ import annotations

import sys
import unicodedata

import pytest
from pydantic import TypeAdapter, ValidationError

from lsdsk.domain.text import MAX_DEVICE_TEXT, BoundedDeviceText, device_text, visible_text


@pytest.mark.os_agnostic
def test_a_value_longer_than_the_limit_is_cut_and_the_cut_is_marked() -> None:
    assert visible_text("abcdefghij", limit=4) == "abcd..."


@pytest.mark.os_agnostic
def test_a_value_exactly_the_limit_long_is_shown_whole_and_unmarked() -> None:
    assert visible_text("abcd", limit=4) == "abcd"
    assert visible_text("abcde", limit=4) == "abcd..."


@pytest.mark.os_agnostic
def test_an_escape_that_does_not_fit_is_dropped_whole_and_the_cut_is_marked() -> None:
    """An escape is written in four characters, so at a limit of five it is dropped whole, never halved."""
    assert visible_text("ab\x1bcd", limit=5) == "ab..."
    assert visible_text("a\x1b", limit=5) == "a\\x1b"


@pytest.mark.os_agnostic
def test_every_format_and_separator_character_is_stripped() -> None:
    """The category sweep: every ``Cf``, ``Zl`` and ``Zp`` code point this interpreter knows is removed.

    The explicit ranges cover most of the format characters but not all of them,
    so the category test is what stands behind the rest; dropping it left some
    dozens of format characters in a name a terminal would then act on.
    """
    swept = [chr(code) for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)) in {"Cf", "Zl", "Zp"}]
    assert len(swept) > 100, "the sweep found almost no format characters, so it proved nothing"

    survivors = [f"U+{ord(character):04X}" for character in swept if device_text(f"a{character}b") != "ab"]

    assert survivors == []


@pytest.mark.os_agnostic
def test_the_bound_on_one_piece_of_device_text_is_4096_characters() -> None:
    bounded = TypeAdapter(BoundedDeviceText)

    assert MAX_DEVICE_TEXT == 4096
    assert bounded.validate_python("a" * 4096) == "a" * 4096
    with pytest.raises(ValidationError, match="at most 4096"):
        bounded.validate_python("a" * 4097)
