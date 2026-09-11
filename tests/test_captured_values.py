"""Tolerant decoding of the values a capture records as text, shared by both platform builders.

A platform writes an unreadable value as text rather than leaving it out, and a
blob can arrive truncated, so each of these reads as not measured instead of
refusing a whole capture over one attribute.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode.captured import decode_base64, parse_int


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("text", "base", "expected"),
    [
        pytest.param("42", 10, 42, id="decimal"),
        pytest.param("0x1022", 16, 0x1022, id="hex-with-prefix"),
        pytest.param("10de", 16, 0x10DE, id="hex-without-prefix"),
        pytest.param("Unknown", 10, None, id="text-written-for-no-value"),
        pytest.param("", 10, None, id="empty"),
        pytest.param(None, 10, None, id="absent"),
    ],
)
def test_captured_integer_text_is_parsed_or_reads_as_not_measured(
    text: str | None, base: int, expected: int | None
) -> None:
    """An integer the capture holds as text parses, and anything else is not measured."""
    assert parse_int(text, base) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("AAEC", b"\x00\x01\x02", id="well-formed"),
        pytest.param("not base64!", None, id="malformed"),
        pytest.param("AAE", None, id="truncated-padding"),
        pytest.param(None, None, id="absent"),
    ],
)
def test_a_captured_blob_is_decoded_or_reads_as_not_measured(value: str | None, expected: bytes | None) -> None:
    """A base64 blob decodes, and a malformed or missing one is not measured."""
    assert decode_base64(value) == expected
