"""The pure byte parsers behind the Windows volume ioctls.

Both ``VOLUME_DISK_EXTENTS`` and ``PARTITION_INFORMATION_EX`` are parsed at
fixed SDK offsets rather than through a declared ``ctypes.Structure``, which is
what keeps them testable on every runner (see
:mod:`lsdsk.adapters.hw.windows.volume_layout`).
"""

from __future__ import annotations

import hashlib
import struct
import tracemalloc
import uuid

import pytest

from lsdsk.adapters.hw.decode.free_extents import FreeExtentTally
from lsdsk.adapters.hw.windows import volume_layout
from lsdsk.adapters.hw.windows.volume_layout import (
    FreeRunFolder,
    parse_bitmap_header,
    parse_disk_extents,
    parse_is_esp,
)

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


def _naive_runs(bits: list[int]) -> list[int]:
    runs: list[int] = []
    current = 0
    for bit in bits:
        if bit == 0:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _pack(bits: list[int]) -> bytes:
    value = sum(bit << index for index, bit in enumerate(bits))
    return value.to_bytes((len(bits) + 7) // 8, "little")


def _stream(seed: int, count: int) -> list[int]:
    """``count`` deterministic bytes for ``seed``, so a failing seed replays exactly."""
    return list(hashlib.shake_256(f"free-runs-{seed}".encode()).digest(count))


@pytest.mark.parametrize("seed", range(20))
def test_free_runs_match_a_per_bit_count_across_chunk_edges(seed: int) -> None:
    drawn = _stream(seed, 12000)
    used_below = (26, 128, 230)[seed % 3]  # about 10%, 50% and 90% of the clusters in use
    bits = [1 if value < used_below else 0 for value in drawn[: 1 + drawn[-1] * 19 % 4999]]
    sizes = iter(8 * (1 + value % 39) for value in drawn[5000:])
    tally = FreeExtentTally()
    folder = FreeRunFolder(4096, tally)
    position = 0
    while position < len(bits):
        size = min(next(sizes), len(bits) - position)
        folder.feed(_pack(bits[position : position + size]), size)
        position += size
    folder.finish()
    expected = _naive_runs(bits)
    summary = tally.summary()
    assert (summary["count"], summary["total"]) == (len(expected), 4096 * sum(expected))
    assert summary["largest"] == 4096 * max(expected, default=0)


def _stretches(seed: int) -> list[int]:
    """A bitmap of alternating used and free stretches, some long enough to span many whole bytes.

    Real volumes hold files laid out contiguously, so a used stretch of
    hundreds of clusters is the common case; uniform random bits never make one.
    """
    drawn = _stream(seed, 600)
    bits: list[int] = []
    for index, value in enumerate(drawn[:300]):
        scale = (1, 37, 700)[value % 3]
        bits += [index % 2] * (1 + (drawn[300 + index] % 4) * scale)
    return bits


@pytest.mark.parametrize("seed", range(12))
def test_long_stretches_match_a_per_bit_count_across_chunk_edges_and_rounded_starts(seed: int) -> None:
    """Chunks of 1-300 bytes, each starting at a cluster rounded down as the filesystem may round it."""
    bits = _stretches(seed)
    rounding = (1, 8, 64)[seed % 3]
    sizes = iter(8 * (rounding // 8 + 1 + value % 300) for value in _stream(seed + 1000, 5000))
    tally = FreeExtentTally()
    folder = FreeRunFolder(4096, tally)
    position = 0
    while position < len(bits):
        lcn = position - position % rounding
        size = min(next(sizes), len(bits) - lcn)
        folder.feed(_pack(bits[lcn : lcn + size]), size, skip=position - lcn)
        position = lcn + size
    folder.finish()
    expected = _naive_runs(bits)
    summary = tally.summary()
    assert max(_naive_runs([1 - bit for bit in bits])) >= 8 * 64  # control: a used stretch of 64 whole bytes
    assert (summary["count"], summary["total"]) == (len(expected), 4096 * sum(expected))
    assert summary["largest"] == 4096 * max(expected, default=0)


def _peak_bytes_to_fold(chunk: bytes) -> int:
    """The most memory folding one chunk held at once, which an expansion to one character per cluster shows."""
    folder = FreeRunFolder(4096, FreeExtentTally())
    tracemalloc.start()
    try:
        folder.feed(chunk, len(chunk) * 8)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_an_all_used_chunk_is_folded_without_expanding_a_character_per_cluster() -> None:
    """A full volume is mostly whole bytes of used clusters, and those hold no free run to look for.

    Expanding one of them into a string of one character per cluster cost
    about 55 ms per MiB of bitmap, 1.8 s per TiB of a full volume. Memory is
    the deterministic witness: the expansion holds eight bytes per bitmap
    byte at once, which no clock on a loaded runner has to be trusted for.
    """
    size = 1 << 20
    assert _peak_bytes_to_fold(b"\xff" * size) < 4 * size
    # The control: the same probe does see the expansion where one is needed,
    # used stretches one byte too short to skip with a free byte after each.
    assert _peak_bytes_to_fold((b"\xff" * 63 + b"\x00") * (size // 64)) >= 8 * size


def test_a_bitmap_header_is_the_starting_cluster_and_the_bit_count() -> None:
    raw = (8).to_bytes(8, "little", signed=True) + (100).to_bytes(8, "little", signed=True)
    assert parse_bitmap_header(raw) == (8, 100)


def test_a_negative_header_field_is_refused() -> None:
    # The field that is negative, named with its value: the refusal must say which one it was.
    with pytest.raises(ValueError, match=r"the volume bitmap header is negative \(lcn -1, bits 0\)"):
        parse_bitmap_header((-1).to_bytes(8, "little", signed=True) + bytes(8))


def _folded(*chunks: tuple[bytes, int]) -> dict[str, int | list[int]]:
    """Feed each ``(data, nbits)`` chunk to one folder in turn, and summarise the tally."""
    tally = FreeExtentTally()
    folder = FreeRunFolder(4096, tally)
    for data, nbits in chunks:
        folder.feed(data, nbits)
    folder.finish()
    return tally.summary()


def test_a_restart_rounded_down_inside_a_byte_folds_the_overlap_once() -> None:
    """Clusters 0-15 free, then an answer from cluster 12 (rounded down from 16) over 12-27, all free: one run of 28.

    The rounded-starts sweep above only ever skips whole bytes, which never
    reaches the mask for a skip that ends inside a byte.
    """
    tally = FreeExtentTally()
    folder = FreeRunFolder(4096, tally)
    folder.feed(b"\x00\x00", 16)
    folder.feed(b"\x00\x00", 16, skip=4)
    folder.finish()
    summary = tally.summary()
    assert (summary["count"], summary["total"], summary["largest"]) == (1, 28 * 4096, 28 * 4096)


def test_bits_past_nbits_in_the_last_byte_are_not_clusters() -> None:
    """0xA0 is clusters 0-4 free, 5 used, 6 free, 7 used; only the first four are the volume's."""
    summary = _folded((b"\xa0", 4))
    assert (summary["count"], summary["total"], summary["largest"]) == (1, 4 * 4096, 4 * 4096)


def test_a_chunk_of_zero_clusters_folds_nothing() -> None:
    """A zero-cluster chunk is not one free cluster, alone or between two halves of an open run."""
    assert (_folded((b"\x00", 0))["count"], _folded((b"\x00", 0))["total"]) == (0, 0)
    summary = _folded((b"\x00", 8), (b"\x00", 0), (b"\x00", 8))
    assert (summary["count"], summary["total"]) == (1, 16 * 4096)


def test_a_negative_bitmap_size_is_refused() -> None:
    with pytest.raises(ValueError, match=r"negative \(lcn 0, bits -1\)"):
        parse_bitmap_header(bytes(8) + (-1).to_bytes(8, "little", signed=True))
