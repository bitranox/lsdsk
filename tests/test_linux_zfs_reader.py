"""The ZFS pool reader, driven through its ioctl seam with a fake control device."""

from __future__ import annotations

import ctypes
import errno
import os
import struct
import sys
from typing import TYPE_CHECKING

import pytest
from nvlist_builders import UINT64_ARRAY, embedded, pair, pool_config, pool_names, pool_props, stream, text, u64_array
from pydantic import ValidationError

from lsdsk.adapters.hw.linux import zfs
from lsdsk.adapters.hw.linux.capture import LinuxCapture, PoolEntry
from lsdsk.adapters.hw.snapshot import SCHEMA_VERSION
from lsdsk.domain.text import MAX_DEVICE_TEXT

if TYPE_CHECKING:
    from collections.abc import Callable
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


def test_the_growing_reply_gives_up_after_its_attempts(tmp_path: Path) -> None:
    class Creeping(FakeDevice):
        size = 300_000

        def __call__(self, _fd: int, request: int, command: bytearray) -> int:
            type(self).size += 1_000
            struct.pack_into("<Q", command, _DST_SIZE, type(self).size)
            raise OSError(errno.ENOMEM, "too small")

    pools, error = zfs.read_pools(_device(tmp_path), ioctl=Creeping({}))
    assert pools is None
    assert error is not None
    assert "the reply kept growing" in error


def test_a_needed_size_no_larger_than_the_refused_buffer_is_refused_at_once(tmp_path: Path) -> None:
    class Shrinking(FakeDevice):
        answers = 0

        def __call__(self, _fd: int, request: int, command: bytearray) -> int:
            type(self).answers += 1
            struct.pack_into("<Q", command, _DST_SIZE, 1_000)
            raise OSError(errno.ENOMEM, "too small")

    pools, error = zfs.read_pools(_device(tmp_path), ioctl=Shrinking({}))
    assert pools is None
    assert error is not None
    assert "without asking for more (1000 bytes)" in error
    assert "kept growing" not in error
    # Refused on the first answer rather than retried with a buffer that cannot fit.
    assert Shrinking.answers == 1


_MINIMAL: dict[str, object] = {
    "schema": SCHEMA_VERSION,
    "platform": "linux",
    "hostname": "example",
    "kernel": "6.1.0",
    "pci": dict[str, object](),
}


def test_many_failing_pools_keep_the_error_inside_the_capture_text_cap(tmp_path: Path) -> None:
    names = [f"pool{n:04d}" + "x" * 60 for n in range(200)]
    replies = {(zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(names)}
    replies.update({(zfs.ZFS_IOC_POOL_STATS, name): b"\x00\x01\x00\x00" + b"\x00" * 4 for name in names})
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=FakeDevice(replies))
    assert pools == {}
    assert error is not None
    assert len(error) == MAX_DEVICE_TEXT
    assert error.endswith("(more pools failed)")
    LinuxCapture.model_validate({**_MINIMAL, **zfs.capture_fields(pools, error)})


@pytest.mark.os_posix
def test_the_system_ioctl_writes_the_kernels_answer_back_into_the_command() -> None:
    import termios

    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"x" * 7)
        command = bytearray(32 * 1024)
        zfs.system_ioctl(read_fd, termios.FIONREAD, command)
        assert int.from_bytes(command[:4], sys.byteorder) == 7
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_a_capture_carries_the_pools_and_the_reason_they_were_not_all_read() -> None:
    capture = LinuxCapture.model_validate(
        {**_MINIMAL, **zfs.capture_fields({"rpool": {"size": 100, "alloc": 25}}, "pool tank: boom")}
    )
    assert capture.zfs_pools is not None
    assert (capture.zfs_pools["rpool"].size, capture.zfs_pools["rpool"].alloc) == (100, 25)
    assert capture.zfs_pools_error == "pool tank: boom"


@pytest.mark.parametrize(("value", "fragment"), [(-1, "greater than or equal to 0"), (2**64, "less than or equal to")])
def test_a_pool_entry_outside_unsigned_64_bits_is_refused(value: int, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        PoolEntry.model_validate({"size": value, "alloc": 0})


def test_the_reason_key_appears_only_when_there_is_a_reason() -> None:
    assert zfs.capture_fields(None, None) == {"zfs_pools": None}
    assert zfs.capture_fields({}, "why") == {"zfs_pools": {}, "zfs_pools_error": "why"}


def _refusing_open(code: int) -> Callable[[Path, int], int]:
    def opener(_path: Path, _flags: int) -> int:
        raise OSError(code, os.strerror(code))

    return opener


@pytest.mark.parametrize("code", [errno.ENODEV, errno.ENXIO])
def test_a_node_with_no_driver_behind_it_is_absent_not_a_refusal(tmp_path: Path, code: int) -> None:
    assert zfs.read_pools(_device(tmp_path), opener=_refusing_open(code)) == (None, None)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM, errno.ENOTDIR])
def test_a_device_that_cannot_be_opened_is_a_refusal_with_its_reason(tmp_path: Path, code: int) -> None:
    pools, error = zfs.read_pools(_device(tmp_path), opener=_refusing_open(code))
    assert pools is None
    assert error is not None
    assert error.startswith("could not open ")
    assert os.strerror(code) in error


def test_every_pool_is_asked_for_its_fragmentation(tmp_path: Path) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["rpool", "tank"]),
            (zfs.ZFS_IOC_POOL_GET_PROPS, "rpool"): pool_props(fragmentation=22),
            (zfs.ZFS_IOC_POOL_GET_PROPS, "tank"): pool_props(fragmentation=2**64 - 1),
        }
    )
    assert zfs.read_pool_fragmentation(_device(tmp_path), ioctl=fake) == ({"rpool": 22, "tank": None}, None)


def test_a_pool_whose_properties_fail_is_named_in_the_reason(tmp_path: Path) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["good", "bad"]),
            (zfs.ZFS_IOC_POOL_GET_PROPS, "good"): pool_props(fragmentation=5),
            (zfs.ZFS_IOC_POOL_GET_PROPS, "bad"): pool_props(fragmentation=None),
        }
    )
    pools, error = zfs.read_pool_fragmentation(_device(tmp_path), ioctl=fake)
    assert pools == {"good": 5}
    assert error is not None
    assert "pool bad" in error


def test_no_control_device_is_no_fragmentation_and_no_refusal(tmp_path: Path) -> None:
    assert zfs.read_pool_fragmentation(tmp_path / "missing", ioctl=FakeDevice({})) == (None, None)


def test_the_fragmentation_reason_key_appears_only_with_a_reason() -> None:
    assert zfs.fragmentation_fields(None, None) == {"zfs_fragmentation": None}
    assert zfs.fragmentation_fields({}, "why") == {"zfs_fragmentation": {}, "zfs_fragmentation_error": "why"}


def test_a_capture_carries_each_pool_s_fragmentation() -> None:
    capture = LinuxCapture.model_validate({**_MINIMAL, **zfs.fragmentation_fields({"rpool": 22, "tank": None}, None)})
    assert capture.zfs_fragmentation == {"rpool": 22, "tank": None}


@pytest.mark.parametrize("value", [-1, 101])
def test_a_fragmentation_outside_0_to_100_is_refused(value: int) -> None:
    with pytest.raises(ValidationError, match="zfs_fragmentation"):
        LinuxCapture.model_validate({**_MINIMAL, "zfs_fragmentation": {"rpool": value}})


def test_the_three_request_numbers_are_openzfs_own() -> None:
    # include/sys/fs/zfs.h (zfs-2.4.0), enum zfs_ioc: ZFS_IOC_FIRST is ('Z' << 8) on Linux and the requests count up
    # from it - POOL_CONFIGS the 5th (/* 0x5a04 */), POOL_STATS the 6th (/* 0x5a05 */), POOL_GET_PROPS the 40th
    # (/* 0x5a27 */). Linux passes them through without _IOWR encoding.
    first = ord("Z") << 8
    assert (
        first + 0x04,
        first + 0x05,
        first + 0x27,
    ) == (zfs.ZFS_IOC_POOL_CONFIGS, zfs.ZFS_IOC_POOL_STATS, zfs.ZFS_IOC_POOL_GET_PROPS)


class _ControlDevice:
    """An opener handing out real descriptors of the null device, remembering each one and the flags asked."""

    def __init__(self) -> None:
        """Start with nothing opened."""
        self.opened: list[int] = []
        self.flags: list[int] = []

    def __call__(self, _path: Path, flags: int) -> int:
        self.flags.append(flags)
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


_LISTED = {(zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["tank"])}


@pytest.mark.parametrize(
    ("read", "fake"),
    [
        (zfs.read_pools, FakeDevice({**_LISTED, (zfs.ZFS_IOC_POOL_STATS, "tank"): pool_config(alloc=1, space=2)})),
        (
            zfs.read_pool_fragmentation,
            FakeDevice({**_LISTED, (zfs.ZFS_IOC_POOL_GET_PROPS, "tank"): pool_props(fragmentation=3)}),
        ),
        (zfs.read_pools, FakeDevice({}, fail=errno.EPERM)),  # the listing itself refused
    ],
    ids=["pools", "fragmentation", "listing-refused"],
)
def test_the_control_device_is_opened_read_only_and_closed(
    tmp_path: Path, read: Callable[..., tuple[object, str | None]], fake: FakeDevice
) -> None:
    device = _ControlDevice()
    read(_device(tmp_path), ioctl=fake, opener=device)
    assert device.flags == [os.O_RDONLY]  # every request needs only read access; nothing here writes to a pool
    assert device.all_closed()


def test_a_needed_size_equal_to_the_refused_buffer_is_refused_at_once(tmp_path: Path) -> None:
    class SameSize(FakeDevice):
        answers = 0

        def __call__(self, _fd: int, request: int, command: bytearray) -> int:
            type(self).answers += 1
            room = struct.unpack_from("<Q", command, _DST_SIZE)[0]
            struct.pack_into("<Q", command, _DST_SIZE, room)  # asks for exactly the buffer it refused
            raise OSError(errno.ENOMEM, "too small")

    pools, error = zfs.read_pools(_device(tmp_path), ioctl=SameSize({}))
    assert pools is None
    assert error == (
        "listing the pools failed: the ioctl failed with ENOMEM on a 262144-byte buffer without asking for more"
        " (262144 bytes)"
    )
    assert SameSize.answers == 1


_SHORT_REASON = "nvlist buffer too short (8 bytes)"


def test_a_reason_exactly_at_the_text_cap_is_kept_whole(tmp_path: Path) -> None:
    name = "p" * (MAX_DEVICE_TEXT - len(f"pool : {_SHORT_REASON}"))
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names([name]),
            (zfs.ZFS_IOC_POOL_STATS, name): b"\x00\x01\x00\x00" + bytes(4),
        }
    )
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert pools == {}
    assert error is not None
    assert error == f"pool {name}: {_SHORT_REASON}"
    assert len(error) == MAX_DEVICE_TEXT


def _stats_reply(stats: bytes) -> bytes:
    return stream([text("name", "pool"), embedded("vdev_tree", [text("type", "root"), stats])])


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        pytest.param(
            _stats_reply(u64_array("vdev_stats", [0, 7, 0, 1])), "pool vdev_stats too short (4 values)", id="four-stats"
        ),
        pytest.param(
            _stats_reply(pair("vdev_stats", UINT64_ARRAY, bytes(8), nelem=-1)),
            "a uint64 array runs past its pair",
            id="negative-array-count",
        ),
    ],
)
def test_a_malformed_pool_reply_is_that_pool_s_refusal_and_the_others_are_read(
    tmp_path: Path, reply: bytes, reason: str
) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["bad", "good"]),
            (zfs.ZFS_IOC_POOL_STATS, "bad"): reply,
            (zfs.ZFS_IOC_POOL_STATS, "good"): pool_config(alloc=1, space=4),
        }
    )
    assert zfs.read_pools(_device(tmp_path), ioctl=fake) == ({"good": {"size": 4, "alloc": 1}}, f"pool bad: {reason}")


def test_a_bound_the_decoder_missed_costs_one_pool_its_reading_not_the_run(tmp_path: Path) -> None:
    # The decoder checks every length before following it, so these stand for a check it lacks: an index past a
    # short tuple, a struct format built from a bad count. Either one ends that pool's reading, never the run's.
    class Slipping(FakeDevice):
        def __call__(self, fd: int, request: int, command: bytearray) -> int:
            name = bytes(command[:16]).split(b"\0", 1)[0]
            if name == b"short":
                raise IndexError("tuple index out of range")
            if name == b"badcount":
                raise struct.error("bad char in struct format")
            return super().__call__(fd, request, command)

    fake = Slipping(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["badcount", "good", "short"]),
            (zfs.ZFS_IOC_POOL_STATS, "good"): pool_config(alloc=1, space=4),
        }
    )
    assert zfs.read_pools(_device(tmp_path), ioctl=fake) == (
        {"good": {"size": 4, "alloc": 1}},
        "pool badcount: bad char in struct format; pool short: tuple index out of range",
    )


def test_a_listing_the_decoder_could_not_bound_is_the_listing_s_refusal(tmp_path: Path) -> None:
    class Slipping(FakeDevice):
        def __call__(self, fd: int, request: int, command: bytearray) -> int:
            raise struct.error("unpack_from requires a buffer of at least 8 bytes")

    assert zfs.read_pool_fragmentation(_device(tmp_path), ioctl=Slipping({})) == (
        None,
        "listing the pools failed: unpack_from requires a buffer of at least 8 bytes",
    )
