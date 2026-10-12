"""The ZFS pool reader, driven through its ioctl seam with a fake control device."""

from __future__ import annotations

import ctypes
import errno
import struct
from typing import TYPE_CHECKING

from nvlist_builders import pool_config, pool_names

from lsdsk.adapters.hw.linux import zfs

if TYPE_CHECKING:
    from pathlib import Path

_DST, _DST_SIZE = 4112, 4120


class FakeDevice:
    """Answers the two pool ioctls from canned nvlist bytes."""

    def __init__(self, replies: dict[tuple[int, str], bytes], *, fail: int | None = None) -> None:
        """Hold the canned replies and, optionally, an errno to fail every call with."""
        self.replies = replies
        self.fail = fail
        self.calls: list[tuple[int, str]] = []

    def __call__(self, _fd: int, request: int, command: bytearray) -> int:
        name = bytes(command[:4096]).split(b"\0", 1)[0].decode()
        self.calls.append((request, name))
        if self.fail is not None:
            raise OSError(self.fail, "refused")
        reply = self.replies[(request, name)]
        pointer, room = struct.unpack_from("<QQ", command, _DST)
        struct.pack_into("<Q", command, _DST_SIZE, len(reply))
        if len(reply) > room:
            raise OSError(errno.ENOMEM, "too small")
        ctypes.memmove(pointer, reply, len(reply))
        return 0


def _device(tmp_path: Path) -> Path:
    node = tmp_path / "zfs"
    node.write_bytes(b"")
    return node


def test_every_pool_is_read_with_its_size_and_allocation(tmp_path: Path) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["rpool", "tank"]),
            (zfs.ZFS_IOC_POOL_STATS, "rpool"): pool_config(alloc=25, space=100),
            (zfs.ZFS_IOC_POOL_STATS, "tank"): pool_config(alloc=0, space=7),
        }
    )
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert error is None
    assert pools == {"rpool": {"size": 100, "alloc": 25}, "tank": {"size": 7, "alloc": 0}}


def test_a_reply_larger_than_the_first_buffer_is_read_after_growing_it(tmp_path: Path) -> None:
    big = pool_config(alloc=1, space=2, padding=300_000)
    fake = FakeDevice({(zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["p"]), (zfs.ZFS_IOC_POOL_STATS, "p"): big})
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert (pools, error) == ({"p": {"size": 2, "alloc": 1}}, None)
    assert fake.calls.count((zfs.ZFS_IOC_POOL_STATS, "p")) == 2


def test_an_absent_control_device_is_not_a_refusal(tmp_path: Path) -> None:
    assert zfs.read_pools(tmp_path / "missing", ioctl=FakeDevice({})) == (None, None)


def test_a_refused_ioctl_is_recorded_with_its_reason(tmp_path: Path) -> None:
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=FakeDevice({}, fail=errno.EPERM))
    assert pools is None
    assert error is not None
    assert "listing the pools" in error
    assert "refused" in error


def test_one_undecodable_pool_does_not_hide_the_others(tmp_path: Path) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["good", "bad"]),
            (zfs.ZFS_IOC_POOL_STATS, "good"): pool_config(alloc=1, space=4),
            (zfs.ZFS_IOC_POOL_STATS, "bad"): b"\x00\x01\x00\x00" + b"\x00" * 4,
        }
    )
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert pools == {"good": {"size": 4, "alloc": 1}}
    assert error is not None
    assert error.startswith("pool bad: ")


def test_a_reply_that_keeps_growing_is_refused_rather_than_followed(tmp_path: Path) -> None:
    class Greedy(FakeDevice):
        def __call__(self, _fd: int, request: int, command: bytearray) -> int:
            struct.pack_into("<Q", command, _DST_SIZE, zfs.MAX_REPLY_BYTES + 1)
            raise OSError(errno.ENOMEM, "too small")

    pools, error = zfs.read_pools(_device(tmp_path), ioctl=Greedy({}))
    assert pools is None
    assert error is not None
    assert "larger than" in error
