r"""Make text the hardware chose safe to print, at the value that carries it.

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
    >>> device_text("Evil\x1b[31mDRIVE\x1b[0m")
    'Evil[31mDRIVE[0m'
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator

# C0 controls, DEL, and the C1 range. Tab is not in the set that gets replaced
# with a space because it never appears in these fields and collapsing it would
# hide a difference; it is simply removed with the rest.
_UNSAFE = frozenset(range(0x00, 0x20)) | {0x7F} | frozenset(range(0x80, 0xA0))

#: How much of one untrusted value a message quotes before cutting it. Enough to
#: recognise a PCI address, a sysfs name or a field somebody mistyped; short
#: enough that a value which is most of a file stays one line of a refusal.
QUOTED_TEXT_LIMIT = 80

__all__ = ["QUOTED_TEXT_LIMIT", "DeviceText", "OptionalDeviceText", "device_text", "first_reported", "visible_text"]


def device_text(value: str) -> str:
    r"""Strip control characters from a string the hardware chose.

    Args:
        value: Text as the device or a capture reported it.

    Returns:
        The same text with every control character removed and the edges
        trimmed.

    Example:
        >>> device_text("Evil\x1b[31mDRIVE\x1b[0m")
        'Evil[31mDRIVE[0m'
        >>> device_text("two\nrows")
        'tworows'
        >>> device_text("  Samsung SSD 860 EVO  ")
        'Samsung SSD 860 EVO'
    """
    return "".join(character for character in value if ord(character) not in _UNSAFE).strip()


def visible_text(value: str, limit: int = QUOTED_TEXT_LIMIT) -> str:
    r"""Quote text from outside in a message, inert and of bounded length.

    :func:`device_text` REMOVES control characters, which is right for a value
    drawn as a reading. A message that quotes where a file went wrong needs the
    opposite, because its reader has to find that place in the file: a key
    whose escape was silently dropped names a key that is not there. So each
    control character is shown as its escape instead of reaching the terminal,
    and the result is cut at ``limit`` characters with a marked ``...``, so a
    value that is most of a file stays one line of the message quoting it.

    Args:
        value: Text a file or a device chose.
        limit: The most characters of it to show.

    Returns:
        The text with every control character written as ``\xNN``, cut to
        ``limit`` characters plus the mark where it was longer.

    Example:
        >>> print(visible_text("0000:00:1f.2\x1b]0;title\x07"))
        0000:00:1f.2\x1b]0;title\x07
        >>> visible_text("a" * 10, limit=4)
        'aaaa...'
        >>> visible_text("0000:00:1f.2")
        '0000:00:1f.2'
        >>> visible_text("ab\x1b", limit=4)
        'ab...'
    """
    # Walked a character at a time and only as far as the limit: escaping all of
    # a value that is most of a file to keep eighty characters would be the cost
    # the cut exists to avoid, and a cut between whole escapes never leaves a
    # dangling "\x1" that reads as a different byte.
    shown: list[str] = []
    used = 0
    for character in value[: limit + 1]:
        piece = f"\\x{ord(character):02x}" if ord(character) in _UNSAFE else character
        if used + len(piece) > limit:
            return "".join(shown) + "..."
        shown.append(piece)
        used += len(piece)
    return "".join(shown)


def _clean_optional(value: str | None) -> str | None:
    r"""Clean a field that may be absent, leaving absence alone.

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
        >>> _clean_optional("13.00\x1b[5m")
        '13.00[5m'
    """
    return None if value is None else device_text(value)


def first_reported(*values: str | None) -> str | None:
    r"""Return the first of several spellings the machine actually reported.

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
