"""Pure byte parsers for the Windows volume ioctl responses.

``VOLUME_DISK_EXTENTS`` and ``PARTITION_INFORMATION_EX`` are parsed from raw
bytes at fixed SDK offsets rather than through a declared ``ctypes.Structure``.
That is what keeps :func:`parse_disk_extents` and :func:`parse_is_esp`
testable on every runner, Windows or not: :mod:`tests.test_windows_volume_parsing`
exercises them directly, with no ``ctypes`` or Win32 call in sight.

:mod:`.volumes` is the impure half: it issues the two ioctls these decode and
sizes its response buffers from the struct layouts declared here.

System Role:
    Adapter layer, pure half.  No I/O; everything here takes ``bytes`` (or
    sizes a buffer from a struct layout) and returns a plain value.
"""

from __future__ import annotations

import struct
import uuid

from . import winapi as api

#: The EFI System Partition's GPT partition type GUID.
_ESP_TYPE_GUID = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")

# VOLUME_DISK_EXTENTS: a DWORD count, padded to 8 bytes because the first
# DISK_EXTENT that follows holds a LARGE_INTEGER and so needs 8-byte alignment.
EXTENTS_HEADER = struct.Struct("<I4x")
# DISK_EXTENT: DiskNumber (DWORD, padded the same way), StartingOffset and
# ExtentLength (both LARGE_INTEGER).
EXTENT_ENTRY = struct.Struct("<I4xqq")
#: The most members this reads from a spanned or striped dynamic volume. A
#: configuration wider than this is not one anyone runs, and :mod:`.volumes`
#: sizes its ioctl buffer once from it rather than probing and retrying.
MAX_EXTENTS = 32

# PARTITION_INFORMATION_EX: PartitionStyle (int, padded to 8), StartingOffset,
# PartitionLength (both LARGE_INTEGER), PartitionNumber (DWORD), two BOOLEANs
# and their padding, then the Mbr/Gpt union. The GPT arm's own PartitionType
# GUID is the union's first field, so it sits right after the fixed head.
PARTITION_HEAD = struct.Struct("<i4xqqIBB2x")
_PARTITION_TYPE_OFFSET = PARTITION_HEAD.size


def parse_disk_extents(raw: bytes) -> list[int]:
    """Read the disk numbers a volume's extents span, from a raw ioctl response.

    Args:
        raw: The bytes ``IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`` returned.

    Returns:
        Each extent's disk number, in the order the driver reported them. A
        count the buffer does not have room for is clamped to what is there,
        and a buffer too short to carry even the header names nothing.

    Example:
        >>> import struct
        >>> raw = struct.pack("<I4x", 1) + struct.pack("<I4xqq", 3, 0, 1 << 20)
        >>> parse_disk_extents(raw)
        [3]
    """
    if len(raw) < EXTENTS_HEADER.size:
        return []
    (declared,) = EXTENTS_HEADER.unpack_from(raw)
    available = (len(raw) - EXTENTS_HEADER.size) // EXTENT_ENTRY.size
    count = min(declared, available)
    return [EXTENT_ENTRY.unpack_from(raw, EXTENTS_HEADER.size + index * EXTENT_ENTRY.size)[0] for index in range(count)]


def parse_is_esp(raw: bytes) -> bool | None:
    """Read whether a partition is the EFI System Partition, from a raw ioctl response.

    Args:
        raw: The bytes ``IOCTL_DISK_GET_PARTITION_INFO_EX`` returned.

    Returns:
        ``True`` for a GPT partition whose type is the ESP GUID, ``False`` for
        any other GPT type or an MBR partition, and ``None`` when the buffer
        is too short to carry even the partition style.

    Example:
        >>> import struct, uuid
        >>> esp = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
        >>> head = struct.pack("<i4xqqIBB2x", 1, 0, 1 << 20, 1, 0, 0)
        >>> parse_is_esp(head + esp.bytes_le + bytes(112 - 16))
        True
    """
    if len(raw) < _PARTITION_TYPE_OFFSET + 16:
        return None
    (style,) = struct.unpack_from("<i", raw, 0)
    if style != api.PARTITION_STYLE_GPT:
        return False
    type_guid = uuid.UUID(bytes_le=raw[_PARTITION_TYPE_OFFSET : _PARTITION_TYPE_OFFSET + 16])
    return type_guid == _ESP_TYPE_GUID


__all__ = [
    "EXTENTS_HEADER",
    "EXTENT_ENTRY",
    "MAX_EXTENTS",
    "PARTITION_HEAD",
    "parse_disk_extents",
    "parse_is_esp",
]
