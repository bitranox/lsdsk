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

import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field

# C0 controls, DEL, and the C1 range. Tab is not in the set that gets replaced
# with a space because it never appears in these fields and collapsing it would
# hide a difference; it is simply removed with the rest.
_UNSAFE = frozenset(range(0x00, 0x20)) | {0x7F} | frozenset(range(0x80, 0xA0))

#: The Unicode Default_Ignorable_Code_Point ranges, inclusive. Most are format
#: characters, which the category test already catches; the rest are combining
#: marks, letters or unassigned code points that a terminal draws as nothing -
#: the grapheme joiner, the Hangul fillers, the Khmer inherent vowels, the
#: Mongolian and plain variation selectors and their supplement - so a stripper
#: keyed on ``Cf`` alone let them hide a difference between two identifiers. The
#: whole property is listed rather than only its non-``Cf`` members so the set
#: reads against the Unicode table line for line.
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)

#: Every code point stripped by number rather than by category, as one set.
#: Every string a domain model holds is cleaned one character at a time, so the
#: test has to be one lookup: walking the ranges above for each ordinary letter
#: made a diagnosis seven times slower. About 4,200 entries.
_UNSAFE_CODES = _UNSAFE | frozenset(code for low, high in _DEFAULT_IGNORABLE for code in range(low, high + 1))

#: Line and paragraph separators: a terminal may break the line on them, which
#: splits one table row into two that look like separate rows.
_SEPARATOR_CATEGORIES = frozenset({"Cf", "Zl", "Zp"})


def _is_unsafe(character: str) -> bool:
    r"""Whether a terminal would act on ``character`` rather than show it.

    The controls above, and every FORMAT character (Unicode category ``Cf``): a
    right-to-left override or isolate reorders the rest of its line on screen,
    and a zero-width space or joiner hides a difference between two strings that
    look identical. None of them is a control character, so a stripper that knew
    only the controls passed them through. The line and paragraph separators
    (``Zl``, ``Zp``) and the default-ignorable code points outside ``Cf`` are
    unsafe for the same two reasons. A device identifier has no use for any of
    them.

    Example:
        >>> _is_unsafe("\u202e"), _is_unsafe("\x1b"), _is_unsafe("e")
        (True, True, False)
        >>> _is_unsafe("\u2028"), _is_unsafe("\ufe0f"), _is_unsafe("\U000e0100")
        (True, True, True)
    """
    return ord(character) in _UNSAFE_CODES or unicodedata.category(character) in _SEPARATOR_CATEGORIES


#: How much of one untrusted value a message quotes before cutting it. Enough to
#: recognise a PCI address, a sysfs name or a field somebody mistyped; short
#: enough that a value which is most of a file stays one line of a refusal.
QUOTED_TEXT_LIMIT = 80

#: The longest a piece of text a DEVICE chose may be. Every one of these is an
#: identifier, a name or a rate as the platform published it: four hex
#: characters from sysfs, a model string, a driver name, `8.0 GT/s PCIe`. The
#: bound is far above anything real and exists because the file ceiling is not a
#: bound on what ONE field can do downstream - an identifier round-trips through
#: an integer parse, a hex re-format and a per-character generator, which
#: measured a 12 to 13x memory multiplier, so a single field inside the 64 MB
#: file limit could reach roughly 800 MB, and a resolved device name is looked
#: up once per device sharing its id, which multiplies it again. It lives here
#: rather than with the capture because a capture is not the only file that
#: carries such text: the counter store is one a caller points
#: ``--history-file`` at, and its identities and hostname go through the same
#: per-character cleaning. Unbounded there, one 40 MB identity cost about 445 MB
#: to replay a ``trend``. :data:`BoundedDeviceText` is that bound on a domain
#: field.
MAX_DEVICE_TEXT = 4096

__all__ = [
    "MAX_DEVICE_TEXT",
    "QUOTED_TEXT_LIMIT",
    "BoundedDeviceText",
    "DeviceText",
    "OptionalDeviceText",
    "device_text",
    "first_reported",
    "visible_text",
]


def device_text(value: str) -> str:
    r"""Strip control and format characters from a string the hardware chose.

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
        >>> device_text("MODEL\u202eLEDOM")
        'MODELLEDOM'
        >>> device_text("  Samsung SSD 860 EVO  ")
        'Samsung SSD 860 EVO'
    """
    return "".join(character for character in value if not _is_unsafe(character)).strip()


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
        The text with every unsafe character written as its fixed-width escape
        (``\xNN``, ``\uNNNN`` or ``\UNNNNNNNN``), cut to ``limit`` characters
        plus the mark where it was longer.

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
        piece = _escaped(character) if _is_unsafe(character) else character
        if used + len(piece) > limit:
            return "".join(shown) + "..."
        shown.append(piece)
        used += len(piece)
    return "".join(shown)


#: The first code point a two-digit ``\xNN`` escape cannot spell.
_PAST_TWO_HEX_DIGITS = 0x100

#: The first code point a four-digit ``\uNNNN`` escape cannot spell.
_PAST_FOUR_HEX_DIGITS = 0x10000


def _escaped(character: str) -> str:
    r"""The escape a message shows in place of a character it must not emit.

    Every form is fixed-width, as Python's own string literals are: a ``\u``
    followed by five digits would read as a four-digit code point and a stray
    digit, so ``A`` + U+E0001 + ``1B`` could not be told from U+E000 + ``11B``.

    Example:
        >>> _escaped("\x1b"), _escaped("\u202e"), _escaped("\U000e0001")
        ('\\x1b', '\\u202e', '\\U000e0001')
    """
    code = ord(character)
    if code < _PAST_TWO_HEX_DIGITS:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code < _PAST_FOUR_HEX_DIGITS else f"\\U{code:08x}"


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
#:
#: Not length-bounded, deliberately. Its values reach the domain through a
#: capture, whose own fields already stop at :data:`MAX_DEVICE_TEXT`, and many
#: are text this tool COMPOSES around device text - a finding's title is a
#: device's name plus a sentence about it, a disk's path is ``/dev/`` plus its
#: node - so the same bound here refused, deep inside ``diagnose``, a capture
#: the capture model had accepted.
DeviceText = Annotated[str, AfterValidator(device_text)]

#: The same, for a field the platform may not report at all.
OptionalDeviceText = Annotated[str | None, AfterValidator(_clean_optional)]

#: :data:`DeviceText` of at most :data:`MAX_DEVICE_TEXT` characters, for a field
#: whose value can reach the domain WITHOUT passing a capture's bound first -
#: the counter store, which a caller names with ``--history-file`` and which is
#: validated straight into the domain's own models. The bound is part of the
#: ``str`` schema itself, so pydantic refuses an over-long value before
#: :func:`device_text` walks it a character at a time, which is the cost the
#: bound exists to cap. Only raw device text belongs here, never text composed
#: around it.
BoundedDeviceText = Annotated[Annotated[str, Field(max_length=MAX_DEVICE_TEXT)], AfterValidator(device_text)]
