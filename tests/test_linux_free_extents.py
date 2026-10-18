"""The ext4/xfs free-extent walk, driven through a fake FS_IOC_GETFSMAP with the kernel's byte layout."""

from __future__ import annotations

import errno
import inspect
import os
import struct
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.capture import FreeExtentsEntry
from lsdsk.adapters.hw.decode.free_extents import MAX_FREE_EXTENTS
from lsdsk.adapters.hw.linux import freespace, reader
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.usage import disks_by_device
from lsdsk.domain.text import MAX_DEVICE_TEXT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_HEAD = struct.Struct("<IIII48x")
_RECORD = struct.Struct("<IIQQQQ24x")
_LOW_KEY, _HIGH_KEY, _RECORDS = 64, 128, 192
_ALL_ONES_32, _ALL_ONES_64 = 0xFFFFFFFF, 2**64 - 1
_SPECIAL, _LAST, _FREE, _METADATA = 0x10, 0x20, 1, 3
_OF_DEV_T = 0x1  # FMH_OF_DEV_T, include/uapi/linux/fsmap.h


def _new_encode_dev(major: int, minor: int) -> int:
    """include/linux/kdev_t.h new_encode_dev, written out here so the module under test is not its own oracle."""
    return (minor & 0xFF) | (major << 8) | ((minor & ~0xFF) << 12)


# What both kernels write into fmr_device for the "8:1" the ROWS below name.
_DATA = _new_encode_dev(8, 1)

# (flags, owner, physical, length, offset), kept sorted by (physical, owner, offset) as the kernels list them.
_Record = tuple[int, int, int, int, int]


def _one_is_last(batch: list[tuple[int, _Record]], index: int, pending: list[tuple[int, _Record]]) -> bool:
    return index == len(batch) - 1 and len(batch) == len(pending)


class FakeFsmap:
    """Answers FS_IOC_GETFSMAP from a list of (flags, owner, physical, length, offset), as the kernels do.

    Each rule below mirrors the kernel code named with it.

    - A low key whose length is zero starts from the beginning, and a non-zero one continues
      (xfs_fsmap.c, the comment before ``info.next_daddr``: "identified by a non-zero length in the low key").
    - A low key naming a special owner (free space, metadata) resumes at physical plus length, because no other
      mapping can share those blocks (xfs_fsmap.c ``rmap_not_shareable``, then ``start_fsb += rm_blockcount`` in
      ``__xfs_getfsmap_datadev``). ext4 resumes every record that way, having no shared blocks (fs/ext4/fsmap.c
      ``ext4_getfsmap``: "there can be no other mapping for the same physical block range").
    - A low key naming file data is resumed by (owner, offset) instead (``rm_offset += rm_blockcount``), so the
      records sharing one physical address are continued inside it, which is why two batches can end at one
      physical address. The kernel does this ONLY for a written data-fork extent of an inode on a reflink xfs:
      ``rmap_not_shareable`` sends an xfs without reflink, an attribute-fork or bmbt block and an unwritten extent
      to the physical-plus-length rule. This fake applies the (owner, offset) rule to every non-special record,
      which answers the same as the kernel wherever one physical block has one record - every case but reflink.
    - The all-ones high key is a contract this fake imposes on the walk (it matches what xfs_io sends); the
      kernels only require low < high. A non-zero reserved byte is the kernels' own EINVAL.
    """

    def __init__(
        self,
        records: list[_Record],
        *,
        fail: int | None = None,
        device: int = _DATA,
        later: tuple[tuple[int, list[_Record]], ...] = (),
        dev_t: bool = True,
    ) -> None:
        """Hold the records of ``device`` and of every ``later`` (higher-numbered) device, and the errno to fail with.

        Each device is listed whole after the one before it, as both kernels walk their sorted device handlers
        (xfs_fsmap.c ``xfs_getfsmap``: a handler whose device is past the low key's starts from the beginning).
        ``dev_t`` is whether the answer sets FMH_OF_DEV_T, as ext4 always does and xfs does unless its realtime
        section lives inside the data device (xfs_fsmap.c: ``if (!mp->m_sb.sb_rtstart) head->fmh_oflags =
        FMH_OF_DEV_T``), when ``fmr_device`` is a section cookie (XFS_DEV_DATA 1, XFS_DEV_LOG 2, XFS_DEV_RT 3).
        """
        self.devices = ((device, records), *later)
        self.fail = fail
        self.dev_t = dev_t
        self.calls = 0

    def _validate(self, head: bytearray) -> None:
        high = _RECORD.unpack_from(head, _HIGH_KEY)
        ones = (_ALL_ONES_32, _ALL_ONES_32, _ALL_ONES_64, _ALL_ONES_64, _ALL_ONES_64, 0)
        reserved = head[16:_LOW_KEY] + head[_LOW_KEY + 40 : _HIGH_KEY] + head[_HIGH_KEY + 40 : _RECORDS]
        if high != ones or any(reserved):
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))

    def _pending_on(self, records: list[_Record], low: tuple[int, ...]) -> list[_Record]:
        _, flags, physical, owner, offset, length = low
        if flags & _SPECIAL:
            return [one for one in records if one[2] >= physical + length]
        return [one for one in records if (one[2], one[1], one[4]) >= (physical, owner, offset + length)]

    def _pending(self, head: bytearray) -> list[tuple[int, _Record]]:
        low = _RECORD.unpack_from(head, _LOW_KEY)
        pending: list[tuple[int, _Record]] = []
        for device, records in self.devices:
            if low[5] == 0 or device > low[0]:
                pending.extend((device, one) for one in records)
            elif device == low[0]:
                pending.extend((device, one) for one in self._pending_on(records, low))
        return pending

    def __call__(self, _fd: int, request: int, head: bytearray) -> int:
        assert request == freespace.FS_IOC_GETFSMAP
        self.calls += 1
        if self.fail is not None:
            raise OSError(self.fail, os.strerror(self.fail))
        self._validate(head)
        count = _HEAD.unpack_from(head, 0)[2]
        pending = self._pending(head)
        batch = pending[:count]
        for index, (device, (flags, owner, physical, length, offset)) in enumerate(batch):
            last = _LAST if _one_is_last(batch, index, pending) else 0
            place = _RECORDS + index * _RECORD.size
            _RECORD.pack_into(head, place, device, flags | last, physical, owner, offset, length)
        struct.pack_into("<I", head, 12, len(batch))
        struct.pack_into("<I", head, 4, _OF_DEV_T if self.dev_t else 0)
        return 0


def _free(physical: int, length: int) -> _Record:
    return (_SPECIAL, _FREE, physical, length, 0)


def _metadata(physical: int, length: int) -> _Record:
    return (_SPECIAL, _METADATA, physical, length, 0)


def _shared(physical: int, owner: int) -> _Record:
    """A reflinked block: file data (no special owner) at ``physical``, one record per owning inode."""
    return (0, owner, physical, 4096, 0)


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


def test_an_ext3_mount_is_walked_and_an_ext2_mount_is_not(tmp_path: Path) -> None:
    # "ext3" in mountinfo is the ext4 driver's own registration (fs/ext4/super.c ext3_fs_type, the ext3 driver
    # being gone since Linux 4.3), whose ioctl answers GETFSMAP; "ext2" is fs/ext2's wherever CONFIG_EXT2_FS is
    # built, and its ext2_ioctl answers ENOTTY, which would read as a refusal of a filesystem with no source.
    rows = [
        {"dev": "8:1", "mountpoint": "/old", "fstype": "ext3"},
        {"dev": "8:2", "mountpoint": "/older", "fstype": "ext2"},
    ]
    reading = freespace.read_free_extents(rows, ioctl=FakeFsmap([_free(0, 4096)]), opener=_opener(tmp_path))
    assert set(reading) == {"8:1"}
    assert reading["8:1"]["count"] == 1


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


def test_a_batch_claiming_more_records_than_the_room_it_was_given_is_refused(tmp_path: Path) -> None:
    # The buffer holds fmh_count records; an fmh_entries above that would read past it, so it cannot be trusted.
    class Overfull(FakeFsmap):
        def __call__(self, _fd: int, _request: int, head: bytearray) -> int:
            self.calls += 1
            room = _HEAD.unpack_from(head, 0)[2]
            _RECORD.pack_into(head, _RECORDS, 0, _SPECIAL, 0, _FREE, 0, 512)
            struct.pack_into("<I", head, 12, room + 1)
            return 0

    fake = Overfull([])
    reading = freespace.read_free_extents(ROWS, ioctl=fake, opener=_opener(tmp_path))
    assert reading == {"8:1": {"error": "the kernel reported 4097 records for room of 4096"}}
    assert fake.calls == 1


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


def test_a_reflinked_block_shared_past_one_batch_does_not_stop_the_walk(tmp_path: Path) -> None:
    # 9000 owners of one physical block: three calls, the first two ending at the same physical address.
    records = [_metadata(0, 4096), *(_shared(4096, 100 + n) for n in range(9000)), _free(1 << 40, 1 << 30)]
    fake = FakeFsmap(records)
    reading = freespace.read_free_extents(ROWS, ioctl=fake, opener=_opener(tmp_path))
    entry = FreeExtentsEntry.model_validate(reading["8:1"])
    assert (entry.count, entry.largest) == (1, 1 << 30)
    assert fake.calls == 3


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


def test_a_realtime_device_s_free_extents_are_not_folded_into_the_data_device(tmp_path: Path) -> None:
    # An xfs with a realtime section lists the rt device's free extents after the data device's, under the
    # rt device's own fmr_device (xfs_fsmap.c handlers[2], xfs_getfsmap_rtdev_rtbitmap).
    realtime = _new_encode_dev(8, 17)
    fake = FakeFsmap(
        [_metadata(0, 4096), _free(4096, 4096)],
        later=((realtime, [_metadata(0, 4096), _free(1 << 20, 1 << 30)]),),
    )
    rows = [{"dev": "8:1", "mountpoint": "/data", "fstype": "xfs"}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    entry = FreeExtentsEntry.model_validate(reading["8:1"])
    assert (entry.count, entry.total, entry.largest) == (1, 4096, 4096)


def test_a_space_map_that_names_none_of_the_filesystem_s_device_is_refused(tmp_path: Path) -> None:
    # Every record under another device number: counting them would describe some other device, and counting
    # none would report a filesystem with no free space; neither is a reading of this one.
    fake = FakeFsmap([_metadata(0, 4096), _free(4096, 4096)], device=_new_encode_dev(8, 2))
    reading = freespace.read_free_extents(ROWS, ioctl=fake, opener=_opener(tmp_path))
    assert reading == {"8:1": {"error": "the space map listed 2 records and none on this filesystem's device 8:1"}}


def test_an_empty_space_map_is_an_empty_reading_not_a_device_mismatch(tmp_path: Path) -> None:
    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap([]), opener=_opener(tmp_path))
    assert reading["8:1"]["count"] == 0


@pytest.mark.parametrize(
    ("dev", "encoded"),
    [
        ("8:1", 0x801),
        ("253:0", 0xFD00),
        ("259:300", (300 & 0xFF) | (259 << 8) | ((300 & ~0xFF) << 12)),
        ("4095:1048575", 0xFF | (4095 << 8) | ((1048575 & ~0xFF) << 12)),
    ],
)
def test_a_device_number_is_encoded_as_the_kernel_s_new_encode_dev(dev: str, encoded: int) -> None:
    major, minor = (int(part) for part in dev.split(":"))
    assert freespace.encode_device(dev) == encoded == _new_encode_dev(major, minor)
    assert encoded <= _ALL_ONES_32  # fmr_device is a u32


@pytest.mark.parametrize("dev", ["8", "8:", ":1", "x:1", "8:1:2", "-8:1", "٨:1", "4096:0", "8:1048576"])
def test_a_device_number_that_is_not_major_minor_has_no_encoding(dev: str) -> None:
    assert freespace.encode_device(dev) is None


def test_a_row_whose_device_number_cannot_be_encoded_is_refused_unwalked(tmp_path: Path) -> None:
    fake = FakeFsmap([_free(0, 4096)])
    rows = [{"dev": "8:x", "mountpoint": "/data", "fstype": "ext4"}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    assert reading == {"8:x": {"error": "the device number 8:x is not major:minor"}}
    assert fake.calls == 0


def test_a_free_total_past_64_bits_refuses_that_filesystem_and_keeps_the_others(tmp_path: Path) -> None:
    rows = [
        {"dev": "8:1", "mountpoint": "/huge", "fstype": "xfs"},
        {"dev": "8:2", "mountpoint": "/small", "fstype": "ext4"},
    ]

    class TwoFilesystems(FakeFsmap):
        def __call__(self, fd: int, request: int, head: bytearray) -> int:
            # The first walk is the huge filesystem's, the rest the small one's.
            if self.calls == 0:
                self.devices = ((_DATA, [_free(0, 2**63), _free(2**63, 2**63)]),)
            else:
                self.devices = ((_new_encode_dev(8, 2), [_free(0, 4096)]),)
            return super().__call__(fd, request, head)

    reading = freespace.read_free_extents(rows, ioctl=TwoFilesystems([]), opener=_opener(tmp_path))
    assert "add up to more than" in reading["8:1"]["error"]
    assert reading["8:2"]["count"] == 1
    for summary in reading.values():
        FreeExtentsEntry.model_validate(summary)  # the capture model takes every row, so the run survives


def test_a_filesystem_on_no_inventoried_disk_is_never_walked(tmp_path: Path) -> None:
    rows = [
        {"dev": "8:1", "mountpoint": "/", "fstype": "ext4"},
        {"dev": "7:0", "mountpoint": "/image", "fstype": "ext4"},  # a loop device: no disk behind it
        {"dev": "0:99", "mountpoint": "/by-source", "fstype": "xfs", "source_dev": "8:2"},
    ]
    opened: list[str] = []
    open_any = _opener(tmp_path)

    def opener(mountpoint: str) -> int:
        opened.append(mountpoint)
        return open_any(mountpoint)

    kept = freespace.rows_on_disks(rows, frozenset({"8:1", "8:2"}))
    reading = freespace.read_free_extents(kept, ioctl=FakeFsmap([_free(0, 4096)]), opener=opener)
    # The same rule the filesystems section keeps a row by: its own device, or the device its source names.
    assert opened == ["/", "/by-source"]
    assert set(reading) == {"8:1", "0:99"}


def test_every_mountpoint_of_a_kept_filesystem_stays_so_a_refusal_can_fall_back() -> None:
    rows = [
        {"dev": "8:1", "mountpoint": "/a", "fstype": "ext4", "hidden": True},
        {"dev": "7:0", "mountpoint": "/image", "fstype": "ext4"},
        {"dev": "8:1", "mountpoint": "/b", "fstype": "ext4", "source_dev": "7:0"},
    ]
    # One row whose source names a disk keeps its device's every row, hidden ones included, in order.
    assert freespace.rows_on_disks(rows, frozenset({"7:0"})) == rows
    assert freespace.rows_on_disks(rows, frozenset({"8:1"})) == [rows[0], rows[2]]
    assert freespace.rows_on_disks(rows, frozenset()) == []


def _raw_reading() -> dict[str, object]:
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "h",
        "kernel": "6.1",
        "pci": {},
        "block": {
            "sda": {"dev": "8:0", "holders": [], "partitions": {"sda1": {"dev": "8:1", "holders": ["dm-0"]}}},
            "loop0": {"dev": "7:0", "holders": [], "virtual": True, "partitions": {}},
        },
        "stacked": {"dm-0": {"dev": "253:0", "holders": []}},
        "mounts": [
            {"dev": "253:0", "mountpoint": "/", "fstype": "xfs"},
            {"dev": "7:0", "mountpoint": "/i", "fstype": "ext4"},
        ],
    }


def test_the_reader_asks_for_exactly_the_devices_the_filesystems_section_keeps() -> None:
    raw = _raw_reading()
    devices = reader.free_extent_devices(raw)
    assert devices == frozenset(disks_by_device(LinuxCapture.model_validate(raw)))
    assert devices == {"8:0", "8:1", "253:0"}  # the crypt or LVM device above the partition too, never the loop


def test_a_reading_that_does_not_parse_names_no_filesystem_to_walk() -> None:
    assert reader.free_extent_devices({**_raw_reading(), "block": "not a mapping"}) == frozenset()


class _Descriptors:
    """An opener handing out real descriptors (of the null device) and remembering each one."""

    def __init__(self) -> None:
        """Start with nothing opened."""
        self.opened: list[int] = []

    def __call__(self, _mountpoint: str) -> int:
        fd = os.open(os.devnull, os.O_RDONLY)
        self.opened.append(fd)
        return fd

    def all_closed(self) -> bool:
        """Whether every descriptor handed out is closed now: fstat on a closed one fails with EBADF."""
        for fd in self.opened:
            try:
                os.fstat(fd)
            except OSError as error:
                assert error.errno == errno.EBADF
                continue
            os.close(fd)  # do not leak it into the rest of the suite
            return False
        return True


@pytest.mark.parametrize(
    ("fake", "cap", "outcome"),
    [
        (FakeFsmap([_free(0, 4096)]), MAX_FREE_EXTENTS, "count"),
        (FakeFsmap([], fail=errno.EPERM), MAX_FREE_EXTENTS, "error"),  # the kernel refused: OSError
        (FakeFsmap([_free(0, 512), _free(4096, 512)]), 1, "error"),  # past the cap: ValueError
        (FakeFsmap([_free(0, 4096)], device=_new_encode_dev(9, 9)), MAX_FREE_EXTENTS, "error"),  # other device
    ],
    ids=["read", "refused", "past-the-cap", "no-record-on-the-device"],
)
def test_the_mountpoint_is_closed_on_every_way_out(fake: FakeFsmap, cap: int, outcome: str) -> None:
    opener = _Descriptors()
    reading = freespace.read_free_extents(ROWS, ioctl=fake, opener=opener, cap=cap)
    assert outcome in reading["8:1"]
    assert len(opener.opened) == 1
    assert opener.all_closed()


def test_a_record_owned_by_inode_one_without_the_special_flag_is_a_file_not_free_space(tmp_path: Path) -> None:
    # ext4's bad-blocks inode is inode 1, the very number FMR_OWN_FREE uses; only the special-owner flag tells them
    # apart (include/uapi/linux/fsmap.h: FMR_OF_SPECIAL_OWNER).
    records = [(0, _FREE, 0, 4096, 0), _free(8192, 4096)]
    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap(records), opener=_opener(tmp_path))
    assert (reading["8:1"]["count"], reading["8:1"]["total"]) == (1, 4096)


def test_the_largest_free_extent_is_the_longest_not_the_last(tmp_path: Path) -> None:
    records = [_free(0, 1 << 30), _free(1 << 31, 4096)]
    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap(records), opener=_opener(tmp_path))
    assert reading["8:1"]["largest"] == 1 << 30


def test_a_second_mountpoint_of_a_read_filesystem_is_not_walked_again() -> None:
    rows = [{"dev": "8:1", "mountpoint": "/a", "fstype": "xfs"}, {"dev": "8:1", "mountpoint": "/b", "fstype": "xfs"}]
    fake, opener = FakeFsmap([_free(0, 4096)]), _Descriptors()
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=opener)
    assert reading["8:1"]["count"] == 1
    assert (fake.calls, len(opener.opened)) == (1, 1)
    assert opener.all_closed()


def test_when_every_mountpoint_refuses_the_first_reason_is_kept(tmp_path: Path) -> None:
    refusals = {"/a": errno.EACCES, "/b": errno.ENOENT}

    def opener(mountpoint: str) -> int:
        code = refusals[mountpoint]
        raise OSError(code, os.strerror(code))

    rows = [{"dev": "8:1", "mountpoint": "/a", "fstype": "ext4"}, {"dev": "8:1", "mountpoint": "/b", "fstype": "ext4"}]
    reading = freespace.read_free_extents(rows, ioctl=FakeFsmap([_free(0, 4096)]), opener=opener)
    assert reading == {"8:1": {"error": os.strerror(errno.EACCES)}}


def test_a_walk_of_exactly_the_record_budget_is_read(tmp_path: Path) -> None:
    records = [_metadata(0, 4096), _metadata(4096, 4096), _free(8192, 4096)]
    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap(records), opener=_opener(tmp_path), max_records=3)
    assert reading["8:1"]["count"] == 1
    refused = freespace.read_free_extents(ROWS, ioctl=FakeFsmap(records), opener=_opener(tmp_path), max_records=2)
    assert refused == {"8:1": {"error": "more than 2 space-map records; stopped reading at the cap"}}


def test_a_reachable_mountpoint_s_reading_beats_a_hidden_one_s_refusal(tmp_path: Path) -> None:
    rows = [
        {"dev": "8:1", "mountpoint": "/covered", "fstype": "xfs", "hidden": True},
        {"dev": "8:1", "mountpoint": "/data", "fstype": "xfs"},
    ]
    reading = freespace.read_free_extents(rows, ioctl=FakeFsmap([_free(0, 4096)]), opener=_opener(tmp_path))
    assert reading["8:1"]["count"] == 1
    assert "error" not in reading["8:1"]


def test_a_kernel_reporting_more_records_than_the_room_given_is_refused(tmp_path: Path) -> None:
    class Overflowing(FakeFsmap):
        def __call__(self, _fd: int, _request: int, head: bytearray) -> int:
            self.calls += 1
            struct.pack_into("<I", head, 12, 4097)
            return 0

    reading = freespace.read_free_extents(ROWS, ioctl=Overflowing([]), opener=_opener(tmp_path))
    assert reading == {"8:1": {"error": "the kernel reported 4097 records for room of 4096"}}


def test_a_refusal_reason_is_cut_to_the_capture_text_cap(tmp_path: Path) -> None:
    def opener(_mountpoint: str) -> int:
        raise OSError(errno.EIO, "x" * (3 * MAX_DEVICE_TEXT))

    reading = freespace.read_free_extents(ROWS, ioctl=FakeFsmap([]), opener=opener)
    assert reading == {"8:1": {"error": "x" * MAX_DEVICE_TEXT}}
    FreeExtentsEntry.model_validate(reading["8:1"])


def test_a_device_count_is_summed_over_every_batch_not_taken_from_the_last(tmp_path: Path) -> None:
    # The data device fills the first batch exactly and the realtime device the second, so the last batch names
    # none of the mounted device's records: only a count carried across batches knows the walk saw any.
    realtime = _new_encode_dev(8, 17)
    data = [_free(4096 * 2 * n, 4096) for n in range(4096)]
    fake = FakeFsmap(data, later=((realtime, [_free(4096 * 2 * n, 4096) for n in range(10)]),))
    rows = [{"dev": "8:1", "mountpoint": "/data", "fstype": "xfs"}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    assert fake.calls == 2  # control: the walk took two batches, the second all realtime
    assert FreeExtentsEntry.model_validate(reading["8:1"]).count == 4096


def test_a_walk_continued_on_a_later_device_at_a_lower_address_is_not_refused(tmp_path: Path) -> None:
    # The data device's records sit high and fill the first batch; the realtime device's start at 0 and need two
    # more. The second batch therefore ends at a LOWER physical address than the first, on a later device, and the
    # third continues inside the realtime device. Only the (device, physical) order says the walk advanced.
    realtime = _new_encode_dev(8, 17)
    data = [_free((1 << 40) + 4096 * 2 * n, 4096) for n in range(4096)]
    fake = FakeFsmap(data, later=((realtime, [_free(4096 * 2 * n, 512) for n in range(5000)]),))
    rows = [{"dev": "8:1", "mountpoint": "/data", "fstype": "xfs"}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    assert "error" not in reading["8:1"], reading["8:1"]
    entry = FreeExtentsEntry.model_validate(reading["8:1"])
    assert (entry.count, entry.total, entry.largest) == (4096, 4096 * 4096, 4096)
    assert fake.calls == 3  # control: the walk took a batch on the data device and two on the realtime one


def test_section_cookies_in_place_of_device_numbers_are_all_the_mounted_device_s(tmp_path: Path) -> None:
    # An xfs whose realtime section lives inside its data device (zoned, mkfs -r rtstart) answers with section
    # cookies and clears FMH_OF_DEV_T: the data section is 1 and the realtime section 3, both on the mounted device.
    fake = FakeFsmap(
        [_metadata(0, 4096), _free(4096, 4096)],
        device=1,
        later=((3, [_free(1 << 20, 1 << 30)]),),
        dev_t=False,
    )
    rows = [{"dev": "8:1", "mountpoint": "/zoned", "fstype": "xfs"}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    assert "error" not in reading["8:1"], reading["8:1"]
    entry = FreeExtentsEntry.model_validate(reading["8:1"])
    assert (entry.count, entry.total, entry.largest) == (2, 4096 + (1 << 30), 1 << 30)


def test_device_numbers_flagged_as_such_are_still_filtered_to_the_mounted_device(tmp_path: Path) -> None:
    # Control for the cookie test: the same records with FMH_OF_DEV_T set are device numbers, and 1 and 3 are not
    # the mounted 8:1, so they are refused rather than counted.
    fake = FakeFsmap([_free(4096, 4096)], device=1, later=((3, [_free(1 << 20, 1 << 30)]),))
    rows = [{"dev": "8:1", "mountpoint": "/data", "fstype": "xfs"}]
    reading = freespace.read_free_extents(rows, ioctl=fake, opener=_opener(tmp_path))
    assert reading == {"8:1": {"error": "the space map listed 2 records and none on this filesystem's device 8:1"}}


class _StuckFsmap(FakeFsmap):
    """Answers every call with the very same free record, as a kernel that does not advance would."""

    def __call__(self, _fd: int, _request: int, head: bytearray) -> int:
        self.calls += 1
        assert self.calls < 50, "the walk did not stop"
        _RECORD.pack_into(head, _RECORDS, _DATA, _SPECIAL, 0, _FREE, 0, 4096)
        struct.pack_into("<I", head, 12, 1)
        struct.pack_into("<I", head, 4, _OF_DEV_T)
        return 0


class _FailsFirst(FakeFsmap):
    """Refuses the first call with ``fail`` and answers every later one from its records."""

    def __call__(self, fd: int, request: int, head: bytearray) -> int:
        try:
            return super().__call__(fd, request, head)
        finally:
            self.fail = None  # every later call is answered


TWO_MOUNTPOINTS = [
    {"dev": "8:1", "mountpoint": "/a", "fstype": "ext4"},
    {"dev": "8:1", "mountpoint": "/b", "fstype": "ext4"},
]


@pytest.mark.parametrize(
    ("make_fake", "limits", "calls", "reason"),
    [
        (lambda: FakeFsmap([_free(0, 512), _free(4096, 512)]), {"cap": 1}, 1, "more than 1 free extents"),
        (
            lambda: FakeFsmap([_metadata(4096 * n, 4096) for n in range(5000)] + [_free(1 << 40, 4096)]),
            {"max_records": 4500},
            2,
            "more than 4,500 space-map records",
        ),
        (lambda: _StuckFsmap([]), {}, 2, "did not advance"),
        (lambda: FakeFsmap([], fail=errno.ENOTTY), {}, 1, os.strerror(errno.ENOTTY)),
        (lambda: FakeFsmap([_free(0, 4096)], device=_new_encode_dev(9, 9)), {}, 1, "none on this filesystem's device"),
    ],
    ids=["past-the-cap", "past-the-record-budget", "not-advancing", "no-getfsmap", "no-record-on-the-device"],
)
def test_a_filesystem_refused_for_what_it_is_is_not_walked_again_at_its_next_mountpoint(
    make_fake: Callable[[], FakeFsmap], limits: dict[str, int], calls: int, reason: str
) -> None:
    fake, opener = make_fake(), _Descriptors()
    reading = freespace.read_free_extents(TWO_MOUNTPOINTS, ioctl=fake, opener=opener, **limits)
    assert reason in reading["8:1"]["error"]
    # Every mountpoint of one device reaches one filesystem, so the second could only repeat the walk.
    assert (fake.calls, len(opener.opened)) == (calls, 1)
    assert opener.all_closed()


def test_a_refused_ioctl_is_asked_again_at_the_next_mountpoint() -> None:
    fake, opener = _FailsFirst([_free(0, 4096)], fail=errno.EIO), _Descriptors()
    reading = freespace.read_free_extents(TWO_MOUNTPOINTS, ioctl=fake, opener=opener)
    assert reading["8:1"]["count"] == 1
    assert (fake.calls, len(opener.opened)) == (2, 2)
    assert opener.all_closed()


class _FailsOnContinuing(FakeFsmap):
    """Answers a walk's first batch, then refuses every call that continues one, as a damaged allocation group does."""

    def __call__(self, fd: int, request: int, head: bytearray) -> int:
        continuing = _RECORD.unpack_from(head, _LOW_KEY)[5] != 0
        self.fail = errno.EIO if continuing else None
        return super().__call__(fd, request, head)


def test_a_walk_that_fails_after_its_first_batch_is_not_walked_again_at_the_next_mountpoint() -> None:
    rows = [{"dev": "8:1", "mountpoint": f"/srv/ct{n}", "fstype": "xfs"} for n in range(3)]
    fake, opener = _FailsOnContinuing([_free(4096 * 2 * n, 4096) for n in range(5000)]), _Descriptors()

    reading = freespace.read_free_extents(rows, ioctl=fake, opener=opener)

    assert reading == {"8:1": {"error": os.strerror(errno.EIO)}}
    # The space map broke partway: that is the filesystem's own, and every mountpoint reaches the same one.
    assert (fake.calls, len(opener.opened)) == (2, 1)
    assert opener.all_closed()
