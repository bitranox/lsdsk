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

import re
import struct
import uuid
from typing import TYPE_CHECKING, Final, NamedTuple

from . import winapi as api

if TYPE_CHECKING:
    from ..decode.free_extents import FreeExtentTally

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
# The union's widest arm, PARTITION_INFORMATION_GPT: PartitionType and
# PartitionId (two GUIDs), Attributes (DWORD64) and Name (36 WCHARs).
_GPT_UNION = struct.Struct("<16s16sQ72s")
#: The whole PARTITION_INFORMATION_EX, which :mod:`.volumes` offers as its
#: response buffer so the GPT arm is never cut short.
PARTITION_INFORMATION_EX_SIZE = PARTITION_HEAD.size + _GPT_UNION.size


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
        >>> parse_is_esp(head + esp.bytes_le + bytes(PARTITION_INFORMATION_EX_SIZE - len(head) - 16))
        True
    """
    if len(raw) < _PARTITION_TYPE_OFFSET + 16:
        return None
    (style,) = struct.unpack_from("<i", raw, 0)
    if style != api.PARTITION_STYLE_GPT:
        return False
    type_guid = uuid.UUID(bytes_le=raw[_PARTITION_TYPE_OFFSET : _PARTITION_TYPE_OFFSET + 16])
    return type_guid == _ESP_TYPE_GUID


#: The fixed head of a ``VOLUME_BITMAP_BUFFER``, which the cluster bits follow.
BITMAP_HEADER_BYTES: Final = 16
_BITMAP_HEADER: Final = struct.Struct("<qq")  # StartingLcn, BitmapSize
_FREE_RUN: Final = re.compile("0+")


class BitmapHeader(NamedTuple):
    """The fixed head of a ``VOLUME_BITMAP_BUFFER``.

    Both fields are cluster counts, so a bare pair could be unpacked the wrong
    way round without the type checker noticing.

    Attributes:
        starting_lcn: The first cluster the bits that follow describe.
        bitmap_size: How many clusters the volume has from there.
    """

    starting_lcn: int
    bitmap_size: int


def parse_bitmap_header(raw: bytes) -> BitmapHeader:
    """The starting cluster and the clusters left, from a ``VOLUME_BITMAP_BUFFER``.

    Args:
        raw: The bytes the bitmap ioctl returned, head first.

    Returns:
        The first cluster the bits describe, and how many clusters the
        volume still has from there; it unpacks as that pair.

    Raises:
        ValueError: The buffer is short or a field is negative.

    Example:
        >>> parse_bitmap_header((8).to_bytes(8, "little") + (100).to_bytes(8, "little"))
        BitmapHeader(starting_lcn=8, bitmap_size=100)
    """
    if len(raw) < BITMAP_HEADER_BYTES:
        message = f"the volume bitmap header is {len(raw)} bytes, not {BITMAP_HEADER_BYTES}"
        raise ValueError(message)
    lcn, bits = _BITMAP_HEADER.unpack_from(raw)
    if lcn < 0 or bits < 0:
        message = f"the volume bitmap header is negative (lcn {lcn}, bits {bits})"
        raise ValueError(message)
    return BitmapHeader(lcn, bits)


class FreeRunFolder:
    """Fold a cluster bitmap, chunk by chunk, into free runs measured in bytes.

    Bit ``i`` of a chunk is cluster ``i`` (least significant bit first), ``1``
    in use. A run touching a chunk's end stays open until the next chunk says
    whether it continues.
    """

    def __init__(self, cluster_bytes: int, tally: FreeExtentTally) -> None:
        """Fold into ``tally``, each cluster ``cluster_bytes`` long."""
        self._cluster = cluster_bytes
        self._tally = tally
        self._open = 0

    def feed(self, data: bytes, nbits: int) -> None:
        """Fold the first ``nbits`` clusters of ``data``.

        Args:
            data: The chunk's bytes, cluster bits least significant first.
            nbits: How many of its bits are clusters.

        Raises:
            FreeExtentCapError: The volume has more free runs than the tally allows.
        """
        if nbits <= 0:
            return
        value = int.from_bytes(data[: (nbits + 7) // 8], "little") & ((1 << nbits) - 1)
        # Binary text is most significant first; reversed, cluster 0 comes first.
        clusters = format(value, f"0{nbits}b")[::-1]
        carried, self._open = self._open, 0
        for run in _FREE_RUN.finditer(clusters):
            start, end = run.span()
            length = end - start
            if start == 0:
                length, carried = length + carried, 0
            if end == nbits:
                self._open = length
            else:
                self._tally.add(length * self._cluster)
        if carried:
            self._tally.add(carried * self._cluster)

    def finish(self) -> None:
        """Close a run still open at the end of the last chunk."""
        if self._open:
            self._tally.add(self._open * self._cluster)
            self._open = 0


__all__ = [
    "BITMAP_HEADER_BYTES",
    "EXTENTS_HEADER",
    "EXTENT_ENTRY",
    "MAX_EXTENTS",
    "PARTITION_HEAD",
    "PARTITION_INFORMATION_EX_SIZE",
    "BitmapHeader",
    "FreeRunFolder",
    "parse_bitmap_header",
    "parse_disk_extents",
    "parse_is_esp",
]
