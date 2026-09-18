"""Make text the hardware chose safe to print, at the value that carries it.

A model, serial, firmware revision, controller name or hostname is chosen by the
hardware, not by this tool, and a snapshot carries whatever the machine that
produced it reported. Both are therefore untrusted text, and the sink they reach
is a terminal, which executes control sequences rather than displaying them.
Left raw, a string containing an escape sequence can recolour the report, retitle
the operator's window, or embed a newline that fabricates an extra table row that
looks exactly like a real one.

Cleaning it on the FIELD rather than in each builder is what makes it hold.
Cleaning where bytes become a string covers only the builders that remember to
call it: the disk fields were cleaned that way and the controller's name and
firmware were not, so a crafted ``board_name`` reached the terminal with its
escapes intact while the same payload in a disk's model was stripped. A field
that declares :data:`DeviceText` cannot be constructed carrying a control
character, so a new builder, a new platform or a new field has nothing to
remember.

System Role:
    Domain. Pure text, no I/O, imports only pydantic and the standard library,
    so every layer may depend on it.

Example:
    >>> device_text("Evil\\x1b[31mDRIVE\\x1b[0m")
    'Evil[31mDRIVE[0m'
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator

# C0 controls, DEL, and the C1 range. Tab is not in the set that gets replaced
# with a space because it never appears in these fields and collapsing it would
# hide a difference; it is simply removed with the rest.
_UNSAFE = frozenset(range(0x00, 0x20)) | {0x7F} | frozenset(range(0x80, 0xA0))

__all__ = ["DeviceText", "OptionalDeviceText", "device_text", "first_reported"]


def device_text(value: str) -> str:
    """Strip control characters from a string the hardware chose.

    Args:
        value: Text as the device or a capture reported it.

    Returns:
        The same text with every control character removed and the edges
        trimmed.

    Example:
        >>> device_text("Evil\\x1b[31mDRIVE\\x1b[0m")
        'Evil[31mDRIVE[0m'
        >>> device_text("two\\nrows")
        'tworows'
        >>> device_text("  Samsung SSD 860 EVO  ")
        'Samsung SSD 860 EVO'
    """
    return "".join(character for character in value if ord(character) not in _UNSAFE).strip()


def _clean_optional(value: str | None) -> str | None:
    """Clean a field that may be absent, leaving absence alone.

    Args:
        value: Text as the device reported it, or ``None`` where it reported
            nothing.

    Returns:
        The cleaned text, or ``None`` unchanged. An empty result stays an empty
        string rather than becoming ``None``, because "reported nothing" and
        "reported only control characters" are different readings.

    Example:
        >>> _clean_optional(None) is None
        True
        >>> _clean_optional("13.00\\x1b[5m")
        '13.00[5m'
    """
    return None if value is None else device_text(value)


def first_reported(*values: str | None) -> str | None:
    """Return the first of several spellings the machine actually reported.

    A drive's model, serial and firmware are each published in more than one
    place - a decoded IDENTIFY structure, the platform's own text, sometimes a
    second decoder - and the rule is the same everywhere: take the first that
    says anything. Written out at the call site it is a chain of ``or`` around a
    conditional per candidate, and it was written out three times, once per
    builder, which is three places for one rule to drift.

    Emptiness is judged AFTER cleaning, so a field holding only control
    characters or spaces counts as unreported and the next candidate is used.

    Args:
        values: The candidate spellings, best first.

    Returns:
        The first candidate with something left after cleaning, or ``None``.

    Example:
        >>> first_reported(None, "  ", "Samsung SSD 870 EVO")
        'Samsung SSD 870 EVO'
        >>> first_reported(None, "\x1b\x1b") is None
        True
    """
    for value in values:
        if value is None:
            continue
        cleaned = device_text(value)
        if cleaned:
            return cleaned
    return None


#: A string field carrying text the hardware chose, cleaned on construction.
DeviceText = Annotated[str, AfterValidator(device_text)]

#: The same, for a field the platform may not report at all.
OptionalDeviceText = Annotated[str | None, AfterValidator(_clean_optional)]
