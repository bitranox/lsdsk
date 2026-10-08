"""The pure byte parsers behind the Windows volume ioctls.

Both ``VOLUME_DISK_EXTENTS`` and ``PARTITION_INFORMATION_EX`` are parsed at
fixed SDK offsets rather than through a declared ``ctypes.Structure``, which is
what keeps them testable on every runner (see
:mod:`lsdsk.adapters.hw.windows.volume_layout`).
"""

from __future__ import annotations

import struct
import uuid

import pytest

from lsdsk.adapters.hw.windows import volume_layout
from lsdsk.adapters.hw.windows.volume_layout import parse_disk_extents, parse_is_esp

ESP = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
OTHER = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")  # Microsoft Basic Data


def _extents(*disks: int) -> bytes:
    # VOLUME_DISK_EXTENTS: DWORD count, 4 bytes of padding, then 24-byte DISK_EXTENTs.
    body = b"".join(struct.pack("<I4xqq", disk, 0, 1 << 30) for disk in disks)
    return struct.pack("<I4x", len(disks)) + body


def _partition(style: int, type_guid: uuid.UUID) -> bytes:
    # PARTITION_INFORMATION_EX: style, pad, offset, length, number, two BOOLEANs, pad, then the GPT union.
    head = struct.pack("<I4xqqIBB2x", style, 0, 1 << 20, 1, 0, 0)
    return head + type_guid.bytes_le + bytes(144 - len(head) - 16)


@pytest.mark.os_agnostic
def test_a_single_extent_names_its_disk() -> None:
    assert parse_disk_extents(_extents(1)) == [1]


@pytest.mark.os_agnostic
def test_a_spanned_volume_names_every_disk_in_order() -> None:
    assert parse_disk_extents(_extents(0, 2)) == [0, 2]


@pytest.mark.os_agnostic
def test_a_count_larger_than_the_buffer_holds_returns_only_what_is_present() -> None:
    raw = _extents(0, 2)
    declares_more = struct.pack("<I4x", 5) + raw[8:]
    assert parse_disk_extents(declares_more) == [0, 2]


@pytest.mark.os_agnostic
def test_a_short_extents_buffer_names_nothing() -> None:
    assert parse_disk_extents(b"\x00\x00\x00") == []


@pytest.mark.os_agnostic
def test_the_esp_type_guid_on_a_gpt_partition_is_esp() -> None:
    assert parse_is_esp(_partition(1, ESP)) is True


@pytest.mark.os_agnostic
def test_another_gpt_type_guid_is_not_esp() -> None:
    assert parse_is_esp(_partition(1, OTHER)) is False


@pytest.mark.os_agnostic
def test_an_mbr_partition_is_never_esp() -> None:
    assert parse_is_esp(_partition(0, ESP)) is False


@pytest.mark.os_agnostic
def test_a_short_partition_buffer_is_undecided() -> None:
    assert parse_is_esp(b"\x01\x00\x00\x00") is None


#: Sizes from winioctl.h, written as numbers rather than derived from a format
#: string: the builders above use the same strings as the parser, so a wrong
#: string would move the parser and its test input together and stay green.
SDK_SIZES = {
    # VOLUME_DISK_EXTENTS up to Extents[0]: DWORD NumberOfDiskExtents, then
    # padding to the 8-byte alignment of DISK_EXTENT's LARGE_INTEGERs.
    "EXTENTS_HEADER": 8,
    # DISK_EXTENT: DWORD DiskNumber, padding, LARGE_INTEGER StartingOffset and ExtentLength.
    "EXTENT_ENTRY": 24,
    # PARTITION_INFORMATION_EX up to its union: PARTITION_STYLE, padding, two
    # LARGE_INTEGERs, DWORD PartitionNumber, two BOOLEANs, padding.
    "PARTITION_HEAD": 32,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "size"), sorted(SDK_SIZES.items()))
def test_each_layout_is_the_size_the_sdk_gives_it(name: str, size: int) -> None:
    assert getattr(volume_layout, name).size == size


@pytest.mark.os_agnostic
def test_the_partition_buffer_holds_the_widest_arm_of_the_union() -> None:
    """PARTITION_INFORMATION_EX is 144 bytes: the 32-byte head plus PARTITION_INFORMATION_GPT (112)."""
    assert volume_layout.PARTITION_INFORMATION_EX_SIZE == 144


@pytest.mark.os_agnostic
def test_extents_are_read_at_the_sdk_offsets_of_a_hand_laid_buffer() -> None:
    """Built byte by byte at the winioctl.h offsets, never through the parser's own format strings."""
    raw = bytearray(8 + 2 * 24)
    raw[0:4] = (2).to_bytes(4, "little")  # NumberOfDiskExtents
    raw[8:12] = (5).to_bytes(4, "little")  # Extents[0].DiskNumber
    raw[32:36] = (9).to_bytes(4, "little")  # Extents[1].DiskNumber
    assert parse_disk_extents(bytes(raw)) == [5, 9]


@pytest.mark.os_agnostic
def test_the_partition_type_is_read_at_the_sdk_offset_of_a_hand_laid_buffer() -> None:
    """The GPT arm's PartitionType is the union's first field, at byte 32 of PARTITION_INFORMATION_EX."""
    raw = bytearray(144)
    raw[0:4] = (1).to_bytes(4, "little")  # PartitionStyle = PARTITION_STYLE_GPT
    raw[32:48] = ESP.bytes_le
    assert parse_is_esp(bytes(raw)) is True
