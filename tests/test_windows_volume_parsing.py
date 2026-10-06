"""The pure byte parsers behind the Windows volume ioctls.

Both ``VOLUME_DISK_EXTENTS`` and ``PARTITION_INFORMATION_EX`` are parsed at
fixed SDK offsets rather than through a declared ``ctypes.Structure``, which is
what keeps them testable on every runner (see :mod:`lsdsk.adapters.hw.windows.volumes`).
"""

from __future__ import annotations

import struct
import uuid

import pytest

from lsdsk.adapters.hw.windows.volumes import parse_disk_extents, parse_is_esp

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
