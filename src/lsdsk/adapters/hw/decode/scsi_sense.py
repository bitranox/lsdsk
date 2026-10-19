"""Say why a SCSI device refused a command, from the sense data it returned.

A translator that rejects an ATA PASS-THROUGH answers CHECK CONDITION and puts
the reason in its sense data: a sense key naming the class of failure and an
additional sense code (ASC) with its qualifier (ASCQ) naming the failure itself.
The status byte alone says only that the command failed, so a refusal recorded
without the sense key cannot tell a device that does not translate ATA (ILLEGAL
REQUEST) from one that is not ready or reports a medium error.

Sense data comes in two layouts, told apart by the response code in byte 0:
fixed (0x70 current, 0x71 deferred), with the key in byte 2 and ASC/ASCQ in
bytes 12 and 13, and descriptor (0x72, 0x73), with the key in byte 1 and
ASC/ASCQ in bytes 2 and 3. Anything else, or a buffer too short to hold the
fields, describes nothing rather than a guess read from the wrong offsets.

Reference: SPC-5, section 4.4 (sense data) and table 49 (sense keys).

System Role:
    Pure adapter-layer decoding, shared by the Linux SG_IO reader and the
    Windows SCSI passthrough reader.
"""

from __future__ import annotations

__all__ = ["describe_sense"]

_RESPONSE_CODE_MASK = 0x7F
_SENSE_KEY_MASK = 0x0F
_FIXED_FORMATS = frozenset({0x70, 0x71})
_DESCRIPTOR_FORMATS = frozenset({0x72, 0x73})
# Fixed format: the key in byte 2, ASC and ASCQ in bytes 12 and 13.
_FIXED_KEY = 2
_FIXED_ASC = 12
# Descriptor format: the key in byte 1, ASC and ASCQ in bytes 2 and 3.
_DESCRIPTOR_KEY = 1
_DESCRIPTOR_ASC = 2

_SENSE_KEYS = {
    0x0: "NO SENSE",
    0x1: "RECOVERED ERROR",
    0x2: "NOT READY",
    0x3: "MEDIUM ERROR",
    0x4: "HARDWARE ERROR",
    0x5: "ILLEGAL REQUEST",
    0x6: "UNIT ATTENTION",
    0x7: "DATA PROTECT",
    0x8: "BLANK CHECK",
    0x9: "VENDOR SPECIFIC",
    0xA: "COPY ABORTED",
    0xB: "ABORTED COMMAND",
    0xD: "VOLUME OVERFLOW",
    0xE: "MISCOMPARE",
    0xF: "COMPLETED",
}


def describe_sense(sense: bytes) -> str | None:
    """Name the sense key and ASC/ASCQ a device returned, or nothing if it returned none.

    Args:
        sense: The sense bytes the device wrote, cut to the length the transport
            reported; trailing zero fill past that length is harmless.

    Returns:
        ``sense key 0x5 ILLEGAL REQUEST, ASC/ASCQ 0x20/0x00`` and the like, or
        ``None`` when the buffer is empty, carries a response code that is not
        a sense layout, or ends before the fields that layout places.

    Example:
        >>> fixed = bytes([0x70, 0, 0x05, 0, 0, 0, 0, 10, 0, 0, 0, 0, 0x20, 0x00])
        >>> describe_sense(fixed)
        'sense key 0x5 ILLEGAL REQUEST, ASC/ASCQ 0x20/0x00'
        >>> describe_sense(bytes([0x72, 0x02, 0x04, 0x01]))
        'sense key 0x2 NOT READY, ASC/ASCQ 0x04/0x01'
        >>> describe_sense(bytes(32)) is None
        True
    """
    if not sense:
        return None
    code = sense[0] & _RESPONSE_CODE_MASK
    if code in _FIXED_FORMATS:
        key_at, asc_at = _FIXED_KEY, _FIXED_ASC
    elif code in _DESCRIPTOR_FORMATS:
        key_at, asc_at = _DESCRIPTOR_KEY, _DESCRIPTOR_ASC
    else:
        return None
    if len(sense) <= asc_at + 1:
        return None
    key = sense[key_at] & _SENSE_KEY_MASK
    name = _SENSE_KEYS.get(key, "RESERVED")
    return f"sense key 0x{key:X} {name}, ASC/ASCQ 0x{sense[asc_at]:02X}/0x{sense[asc_at + 1]:02X}"
