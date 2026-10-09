"""Tolerant decoding of the values a capture records as text.

A reading holds integers, identifiers and binary structures as text, because
that is what sysfs publishes and what a snapshot stores. Both platform builders
decode those values the same way, so they share one copy here and cannot drift
apart: an unparsable value reads as not measured rather than refusing a whole
capture over one attribute. The same goes for a temperature no drive could report
(see :func:`plausible_celsius`).

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


#: A PCI vendor or device identifier is a 16-bit configuration register, so a
#: wider or negative value in a capture was never read from hardware.
_PCI_ID_MAX = 0xFFFF


def parse_pci_id(text: str | None) -> int | None:
    """Parse a PCI vendor or device identifier a capture holds as hex text.

    Args:
        text: The recorded value, or ``None`` when nothing was recorded.

    Returns:
        The identifier, or ``None`` for anything unparsable or outside 16 bits.

    Example:
        >>> parse_pci_id("0x8086")
        32902
        >>> parse_pci_id("0x123456") is None
        True
        >>> parse_pci_id("-0x1") is None
        True
    """
    value = parse_int(text, 16)
    return value if value is not None and 0 <= value <= _PCI_ID_MAX else None


def decode_base64(value: str | None) -> bytes | None:
    r"""Decode a binary structure a capture holds as base64.

    Args:
        value: The recorded blob, or ``None`` when nothing was recorded.

    Returns:
        The bytes, or ``None`` for a malformed blob.

    Example:
        >>> decode_base64("AAEC")
        b'\x00\x01\x02'
        >>> decode_base64("not base64!") is None
        True
    """
    if value is None:
        return None
    try:
        return b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


# Wider than any operating range a drive datasheet lists (industrial parts go to
# -40 C and 85 C; NAND throttles and shuts down below 125 C), so a real reading
# is never refused, and narrow enough that every value a floating bus or a
# corrupt attribute produces (0xFFFF kelvin is 65262 C) lies outside it.
MIN_PLAUSIBLE_CELSIUS = -60
MAX_PLAUSIBLE_CELSIUS = 200


def plausible_celsius(reading: int, *, per_degree: int = 1) -> int | None:
    """Round a temperature to whole degrees, or ``None`` when it cannot be real.

    The range is checked on the integer as read, before any division: a
    sysfs attribute can hold an integer of any length, and dividing one past
    the float range raises instead of answering.

    Args:
        reading: The temperature as the device published it.
        per_degree: How many units of ``reading`` make one degree Celsius
            (1000 for the millidegrees sysfs publishes).

    Returns:
        The rounded temperature, or ``None`` outside the plausible range.

    Example:
        >>> plausible_celsius(41_600, per_degree=1000)
        42
        >>> plausible_celsius(65262) is None
        True
        >>> plausible_celsius(10**400, per_degree=1000) is None
        True
    """
    if not MIN_PLAUSIBLE_CELSIUS * per_degree <= reading <= MAX_PLAUSIBLE_CELSIUS * per_degree:
        return None
    return round(reading / per_degree)


__all__ = [
    "MAX_PLAUSIBLE_CELSIUS",
    "MIN_PLAUSIBLE_CELSIUS",
    "decode_base64",
    "parse_int",
    "parse_pci_id",
    "plausible_celsius",
]
