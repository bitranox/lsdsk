"""Make a device-reported string safe to print, at the point it is decoded.

A model, serial, firmware revision or hostname is chosen by the hardware, not by
this tool, and a snapshot carries whatever the machine that produced it reported.
Both are therefore untrusted text, and the sink they reach is a terminal, which
executes control sequences rather than displaying them.  Left raw, a drive whose
model number contains an escape sequence can recolour the report, retitle the
operator's window, or embed a newline that fabricates an extra table row that
looks exactly like a real one.

Cleaning here rather than in the renderers is what makes it hold: the value is
sanitised once, where bytes become a string, so every consumer downstream is
covered at once, including JSON, the history store and the log.

System Role:
    Adapter layer, pure text decoding.  No I/O.
"""

from __future__ import annotations

# C0 controls, DEL, and the C1 range. Tab is not in the set that gets replaced
# with a space because it never appears in these fields and collapsing it would
# hide a difference; it is simply removed with the rest.
_UNSAFE = frozenset(range(0x00, 0x20)) | {0x7F} | frozenset(range(0x80, 0xA0))


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


__all__ = ["device_text"]
