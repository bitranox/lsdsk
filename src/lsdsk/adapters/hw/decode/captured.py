"""Tolerant decoding of the values a capture records as text.

A reading holds integers, identifiers and binary structures as text, because
that is what sysfs publishes and what a snapshot stores. Both platform builders
decode those values the same way, so they share one copy here and cannot drift
apart: an unparsable value reads as not measured rather than refusing a whole
capture over one attribute.

System Role:
    Adapter layer, pure text decoding shared by the Linux and Windows builders.
    No I/O.
"""

from __future__ import annotations

import binascii
from base64 import b64decode


def parse_int(text: str | None, base: int = 10) -> int | None:
    """Parse an integer a capture holds as text.

    Args:
        text: The recorded value, or ``None`` when nothing was recorded.
        base: The base the value is written in.

    Returns:
        The integer, or ``None`` for anything unparsable.

    Example:
        >>> parse_int("0x1022", 16)
        4130
        >>> parse_int("Unknown") is None
        True
    """
    if text is None:
        return None
    try:
        return int(text, base)
    except ValueError:
        return None


def decode_base64(value: str | None) -> bytes | None:
    """Decode a binary structure a capture holds as base64.

    Args:
        value: The recorded blob, or ``None`` when nothing was recorded.

    Returns:
        The bytes, or ``None`` for a malformed blob.

    Example:
        >>> decode_base64("AAEC")
        b'\\x00\\x01\\x02'
        >>> decode_base64("not base64!") is None
        True
    """
    if value is None:
        return None
    try:
        return b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


__all__ = ["decode_base64", "parse_int"]
