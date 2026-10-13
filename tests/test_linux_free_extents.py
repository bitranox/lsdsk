"""The ext4/xfs free-extent walk, driven through a fake FS_IOC_GETFSMAP with the kernel's byte layout."""

from __future__ import annotations

import errno
import inspect
import os
import struct
from typing import TYPE_CHECKING

from lsdsk.adapters.hw.capture import FreeExtentsEntry
from lsdsk.adapters.hw.decode.free_extents import MAX_FREE_EXTENTS
from lsdsk.adapters.hw.linux import freespace

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_HEAD = struct.Struct("<IIII48x")
_RECORD = struct.Struct("<IIQQQQ24x")
_LOW_KEY, _HIGH_KEY, _RECORDS = 64, 128, 192
_ALL_ONES_32, _ALL_ONES_64 = 0xFFFFFFFF, 2**64 - 1
_SPECIAL, _LAST, _FREE, _METADATA = 0x10, 0x20, 1, 3

_Record = tuple[int, int, int, int]


def _one_is_last(batch: list[_Record], index: int, pending: list[_Record]) -> bool:
    return index == len(batch) - 1 and len(batch) == len(pending)


class FakeFsmap:
    """Answers FS_IOC_GETFSMAP from a list of (flags, owner, physical, length), as the kernels do.

    A low key with a non-zero length resumes at its physical plus its length and a zero length starts from
    the beginning; a high key that is not all ones with length 0, or a non-zero reserved byte, is EINVAL.
    """

    def __init__(self, records: list[_Record], *, fail: int | None = None) -> None:
        """Hold the records to answer from and the errno to fail with, if any."""
        self.records = records
        self.fail = fail
        self.calls = 0

    def _validate(self, head: bytearray) -> None:
        high = _RECORD.unpack_from(head, _HIGH_KEY)
        ones = (_ALL_ONES_32, _ALL_ONES_32, _ALL_ONES_64, _ALL_ONES_64, _ALL_ONES_64, 0)
        reserved = head[16:_LOW_KEY] + head[_LOW_KEY + 40 : _HIGH_KEY] + head[_HIGH_KEY + 40 : _RECORDS]
        if high != ones or any(reserved):
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))

    def _pending(self, head: bytearray) -> list[_Record]:
        _, _, physical, _, _, length = _RECORD.unpack_from(head, _LOW_KEY)
        if length == 0:
            return list(self.records)
        return [one for one in self.records if one[2] >= physical + length]

    def __call__(self, _fd: int, request: int, head: bytearray) -> int:
        assert request == freespace.FS_IOC_GETFSMAP
        self.calls += 1
        if self.fail is not None:
            raise OSError(self.fail, os.strerror(self.fail))
        self._validate(head)
        count = _HEAD.unpack_from(head, 0)[2]
        pending = self._pending(head)
        batch = pending[:count]
        for index, (flags, owner, physical, length) in enumerate(batch):
            last = _LAST if _one_is_last(batch, index, pending) else 0
            _RECORD.pack_into(head, _RECORDS + index * _RECORD.size, 0, flags | last, physical, owner, 0, length)
        struct.pack_into("<I", head, 12, len(batch))
        return 0


def _free(physical: int, length: int) -> _Record:
    return (_SPECIAL, _FREE, physical, length)


def _metadata(physical: int, length: int) -> _Record:
    return (_SPECIAL, _METADATA, physical, length)


def _opener(tmp_path: Path) -> Callable[[str], int]:
    # A real descriptor the reader can os.close; a plain file, because Windows refuses os.open on a directory.
    node = tmp_path / "mount"
    node.write_bytes(b"")

    def opener(_mountpoint: str) -> int:
        return os.open(node, os.O_RDONLY)

    return opener


ROWS = [
    {"dev": "8:1", "mountpoint": "/", "fstype": "ext4"},
    {"dev": "8:2", "mountpoint": "/boot/efi", "fstype": "vfat"},
]


def test_only_free_records_are_folded_across_continued_calls(tmp_path: Path) -> None:
    records = [_metadata(0, 4096), *(_free(4096 * (2 * n + 1), 4096) for n in range(5000)), _free(1 << 40, 1 << 30)]
    fake = FakeFsmap(records)
    reading = freespace.read_free_extents(ROWS, ioctl=fake, opener=_opener(tmp_path))
    assert set(reading) == {"8:1"}  # vfat is never asked
    entry = FreeExtentsEntry.model_validate(reading["8:1"])
    assert (entry.count, entry.largest, entry.total) == (5001, 1 << 30, 5000 * 4096 + (1 << 30))
    assert fake.calls > 1  # the walk needed a continuation


def test_a_refused_ioctl_is_recorded_with_its_reason(tmp_path: Path) -> None:
    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap([], fail=errno.EPERM), opener=_opener(tmp_path))
    assert reading == {"8:1": {"error": os.strerror(errno.EPERM)}}


def test_a_filesystem_past_the_cap_is_refused_naming_the_cap(tmp_path: Path) -> None:
    records = [_free(1024 * 2 * n, 1024) for n in range(4)]
    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap(records), opener=_opener(tmp_path), cap=3)
    assert "more than 3 free extents" in reading["8:1"]["error"]


def test_the_walk_uses_the_shipped_cap_unless_told_otherwise() -> None:
    assert inspect.signature(freespace.read_free_extents).parameters["cap"].default == MAX_FREE_EXTENTS


def test_a_walk_past_the_record_cap_is_refused_naming_the_number(tmp_path: Path) -> None:
    # Two calls of non-free records and one free extent: far under the free-extent cap, over the record cap.
    records = [_metadata(4096 * n, 4096) for n in range(5000)] + [_free(1 << 40, 4096)]
    fake = FakeFsmap(records)
    reading = freespace.read_free_extents(ROWS, ioctl=fake, opener=_opener(tmp_path), max_records=4500)
    assert reading == {"8:1": {"error": "more than 4,500 space-map records; stopped reading at the cap"}}
    assert fake.calls == 2


def test_the_walk_reads_the_shipped_record_budget_unless_told_otherwise() -> None:
    assert inspect.signature(freespace.read_free_extents).parameters["max_records"].default == (
        freespace.MAX_FSMAP_RECORDS
    )
    assert freespace.MAX_FSMAP_RECORDS == 4_000_000


def test_a_batch_that_goes_backwards_is_refused(tmp_path: Path) -> None:
    class Backwards(FakeFsmap):
        def __call__(self, _fd: int, _request: int, head: bytearray) -> int:
            self.calls += 1
            assert self.calls < 50, "the walk did not stop"
            physical = (1 << 40) - self.calls * 4096  # never equal twice, so only an ordering check stops it
            _RECORD.pack_into(head, _RECORDS, 0, _SPECIAL, physical, _FREE, 0, 512)
            struct.pack_into("<I", head, 12, 1)
            return 0

    reading = freespace.read_free_extents(ROWS, ioctl=Backwards([]), opener=_opener(tmp_path))
    assert "did not advance" in reading["8:1"]["error"]


def test_a_kernel_that_does_not_advance_is_refused_not_looped(tmp_path: Path) -> None:
    class Stuck(FakeFsmap):
        def __call__(self, _fd: int, _request: int, head: bytearray) -> int:
            self.calls += 1
            assert self.calls < 50, "the walk did not stop"
            _RECORD.pack_into(head, _RECORDS, 0, _SPECIAL, 0, _FREE, 0, 4096)
            struct.pack_into("<I", head, 12, 1)
            return 0

    reading = freespace.read_free_extents(ROWS, ioctl=Stuck([]), opener=_opener(tmp_path))
    assert "did not advance" in reading["8:1"]["error"]


def test_a_hidden_only_filesystem_is_recorded_as_refused_and_never_asked(tmp_path: Path) -> None:
    fake = FakeFsmap([_free(0, 4096)])
    rows = [{"dev": "8:1", "mountpoint": "/data", "fstype": "xfs", "hidden": True}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    assert "hides every mountpoint" in reading["8:1"]["error"]
    assert fake.calls == 0


def test_an_unopenable_first_mountpoint_falls_back_to_the_next(tmp_path: Path) -> None:
    open_second = _opener(tmp_path)

    def opener(mountpoint: str) -> int:
        if mountpoint == "/a":
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES))
        return open_second(mountpoint)

    rows = [{"dev": "8:1", "mountpoint": "/a", "fstype": "ext4"}, {"dev": "8:1", "mountpoint": "/b", "fstype": "ext4"}]
    reading = freespace.read_free_extents(rows, ioctl=FakeFsmap([_free(0, 4096)]), opener=opener)
    assert reading["8:1"]["count"] == 1


def test_the_real_ioctl_number_is_the_uapi_one() -> None:
    # _IOWR('X', 59, struct fsmap_head): dir 3, size 192, type 0x58, nr 59.
    assert (3 << 30) | (192 << 16) | (ord("X") << 8) | 59 == freespace.FS_IOC_GETFSMAP
    assert _HEAD.size + 2 * _RECORD.size == 192
