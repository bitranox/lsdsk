"""Read how scattered each ext4 or xfs filesystem's free space is, through FS_IOC_GETFSMAP.

The kernel lists a filesystem's space as records - free, metadata, owned by a
file - in physical order, a batch per call, each call resuming after the last
record of the one before. Only the free records are kept, folded into the
bounded summary a capture records. Two budgets keep a read from running for
minutes, and a filesystem past either is refused with a reason naming it: how
many free extents it may have (``MAX_FREE_EXTENTS``) and how many records of
any kind the walk may read (``MAX_FSMAP_RECORDS``), because a filesystem with
reverse mapping can list every file extent it holds.

One mountpoint per filesystem is asked, chosen by the rules ``read_space``
uses: a hidden mountpoint never (the open would reach the filesystem covering
it), and the next mountpoint only after a refusal another mountpoint could
change - the open or the first call through it failing. A refusal of the
filesystem itself (past a budget, a walk that did not advance, no GETFSMAP, a
call failing once the space map has answered) is final, so a filesystem
bind-mounted many times is walked once.

Every device the filesystem spans is listed - an xfs realtime device or an
external log beside the data device, in device-number order - so only the
records naming the mounted device itself are counted. Where the kernel says
its device fields are not device numbers (FMH_OF_DEV_T clear: an xfs whose
realtime section lives inside the data device names its sections 1, 2 and 3),
every section is on the mounted device and every record is counted.

System Role:
    Adapter layer, Linux reading half. Impure: opens mountpoints and issues ioctls.
"""

from __future__ import annotations

import errno
import os
import struct
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from ....domain.models import FREE_EXTENT_MOUNT_TYPES
from ....domain.text import MAX_DEVICE_TEXT
from ..decode.free_extents import MAX_FREE_EXTENTS, FreeExtentTally
from .mounts import kernel_path
from .zfs import Ioctl, system_ioctl

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

#: ``_IOWR('X', 59, struct fsmap_head)`` from include/uapi/linux/fsmap.h.
FS_IOC_GETFSMAP: Final = 0xC0C0583B

_HEAD: Final = struct.Struct("<IIII48x")  # fmh_iflags, fmh_oflags, fmh_count, fmh_entries, fmh_reserved[6]
_RECORD: Final = struct.Struct("<IIQQQQ24x")  # device, flags, physical, owner, offset, length, reserved[3]
_LOW_KEY: Final = _HEAD.size
_HIGH_KEY: Final = _LOW_KEY + _RECORD.size
_RECORDS: Final = _HIGH_KEY + _RECORD.size
_RECORDS_PER_CALL: Final = 4096
#: A reading budget, not a judged figure, so it has no configuration key: records of ANY kind one walk may read.
MAX_FSMAP_RECORDS: Final = 4_000_000
_ALL_ONES_32: Final = 0xFFFFFFFF
_ALL_ONES_64: Final = 2**64 - 1
_SPECIAL_OWNER: Final = 0x10  # FMR_OF_SPECIAL_OWNER
_LAST: Final = 0x20  # FMR_OF_LAST
_OWN_FREE: Final = 1  # FMR_OWNER(0, 1)
_OF_DEV_T: Final = 0x1  # FMH_OF_DEV_T: every fmr_device is a dev_t
_MAJOR_LIMIT: Final = 1 << 12  # dev_t holds 12 bits of major and MINORBITS (20) of minor: include/linux/kdev_t.h
_MINOR_LIMIT: Final = 1 << 20
_LOW_MINOR: Final = 0xFF
_HIDDEN_REASON: Final = (
    "another mount hides every mountpoint of it, so the free-space map there would read a different filesystem"
)


def _open_directory(mountpoint: str) -> int:
    # O_DIRECTORY is Linux-only; read at call time so the module imports anywhere.
    return os.open(
        kernel_path(mountpoint),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )


def encode_device(dev: str) -> int | None:
    """The ``fmr_device`` number GETFSMAP gives the device a mountinfo ``maj:min`` names.

    Both kernels name the mounted device by ``new_encode_dev`` of its ``dev_t``
    and say so with FMH_OF_DEV_T (fs/xfs/xfs_fsmap.c ``handlers[0].dev``,
    fs/ext4/fsmap.c ``handlers[0].gfd_dev``), and include/linux/kdev_t.h writes
    that as ``(minor & 0xff) | (major << 8) | ((minor & ~0xff) << 12)``.
    mountinfo prints the same ``dev_t``'s major and minor.

    Args:
        dev: A ``maj:min`` as mountinfo prints it.

    Returns:
        The encoded number, or ``None`` when ``dev`` is not two ASCII numbers
        that fit a ``dev_t``.

    Example:
        >>> hex(encode_device("259:300"))
        '0x11032c'
    """
    major_text, colon, minor_text = dev.partition(":")
    if not colon or not all(part.isascii() and part.isdigit() for part in (major_text, minor_text)):
        return None
    major, minor = int(major_text), int(minor_text)
    if major >= _MAJOR_LIMIT or minor >= _MINOR_LIMIT:
        return None
    return (minor & _LOW_MINOR) | (major << 8) | ((minor & ~_LOW_MINOR) << 12)


def _new_head() -> bytearray:
    head = bytearray(_RECORDS + _RECORDS_PER_CALL * _RECORD.size)
    _HEAD.pack_into(head, 0, 0, 0, _RECORDS_PER_CALL, 0)
    _RECORD.pack_into(head, _HIGH_KEY, _ALL_ONES_32, _ALL_ONES_32, _ALL_ONES_64, _ALL_ONES_64, _ALL_ONES_64, 0)
    return head


class _Batch(NamedTuple):
    """What one call's records said: the last record's flags, and how many named the mounted device."""

    last_flags: int
    on_device: int


def _fold_batch(head: bytearray, entries: int, tally: FreeExtentTally, device: int | None) -> _Batch:
    """Fold one call's free records on ``device`` into ``tally``; every record when ``device`` is ``None``."""
    flags, on_device = 0, 0
    for index in range(entries):
        record_device, flags, _, owner, _, length = _RECORD.unpack_from(head, _RECORDS + index * _RECORD.size)
        if device is not None and record_device != device:
            continue
        on_device += 1
        if flags & _SPECIAL_OWNER and owner == _OWN_FREE:
            tally.add(length)
    return _Batch(flags, on_device)


def _check_batch(entries: int, seen: int, max_records: int) -> None:
    if entries > _RECORDS_PER_CALL:
        message = f"the kernel reported {entries} records for room of {_RECORDS_PER_CALL}"
        raise ValueError(message)
    if seen > max_records:
        message = f"more than {max_records:,} space-map records; stopped reading at the cap"
        raise ValueError(message)


def _last_position(head: bytearray, entries: int) -> tuple[bytes, tuple[int, int]]:
    """The batch's last record, and the (device, physical) place it stands at."""
    start = _RECORDS + (entries - 1) * _RECORD.size
    last = bytes(head[start : start + _RECORD.size])
    device, _, physical, *_ = _RECORD.unpack(last)
    return last, (device, physical)


class _MidWalkError(OSError):
    """A GETFSMAP call refused after the space map had already answered a batch.

    The space map belongs to the superblock, so a walk that broke partway -
    a damaged allocation group, an I/O error - breaks the same way at every
    mountpoint of the filesystem, each time after re-reading what came before.
    """


class _Counts(NamedTuple):
    """How many records a walk read on every device, and how many of them named the mounted one."""

    records: int
    on_device: int


def _walk(fd: int, ioctl: Ioctl, tally: FreeExtentTally, *, device: int, max_records: int) -> _Counts:
    """Every free record on ``device`` of the filesystem behind ``fd``, folded into ``tally``.

    Where the kernel clears FMH_OF_DEV_T its device fields are section cookies,
    every one of them on the mounted device, so every record is counted.

    Returns:
        How many records were read, and how many of them named ``device``.

    Raises:
        OSError: The kernel refused the first call; :class:`_MidWalkError`
            when it refused a later one.
        ValueError: The kernel answered something the walk cannot continue from,
            or the filesystem has more records than ``max_records``.
        FreeExtentCapError: The filesystem has more free extents than the cap.
    """
    head = _new_head()
    previous: tuple[bytes, tuple[int, int]] | None = None
    seen = on_device = 0
    while True:
        try:
            ioctl(fd, FS_IOC_GETFSMAP, head)
        except OSError as error:
            if not seen:
                raise
            raise _MidWalkError(error.errno, error.strerror or str(error)) from error
        _, out_flags, _, entries = _HEAD.unpack_from(head, 0)
        if entries == 0:
            break
        seen += entries
        _check_batch(entries, seen, max_records)
        # Section cookies rather than device numbers all stand for the mounted device (xfs_getfsmap_device).
        batch = _fold_batch(head, entries, tally, device if out_flags & _OF_DEV_T else None)
        on_device += batch.on_device
        if batch.last_flags & _LAST:
            break
        last, position = _last_position(head, entries)
        # XFS lists a block shared by several owners as several records at one physical address and continues
        # inside that run by owner and offset, so two batches may end at one place; only going backwards, or
        # returning the very same record again, is a kernel that did not advance.
        if previous is not None and (position < previous[1] or last == previous[0]):
            message = "the kernel did not advance past its last record"
            raise ValueError(message)
        previous = (last, position)
        head[_LOW_KEY : _LOW_KEY + _RECORD.size] = last
    return _Counts(seen, on_device)


class _Target(NamedTuple):
    """A mountpoint worth asking, and the filesystem device it belongs to."""

    dev: str
    mountpoint: str


def _reason(error: Exception) -> str:
    text = (error.strerror if isinstance(error, OSError) else None) or str(error)
    return text[:MAX_DEVICE_TEXT]


class _Answer(NamedTuple):
    """What one mountpoint said, and whether the filesystem's other mountpoints could say anything else.

    Attributes:
        reading: The free-extent summary, or ``{"error": <reason>}``.
        settled: ``True`` for a summary and for a refusal of the filesystem
            itself - past a cap or the record budget, a walk that did not
            advance, no GETFSMAP at all, a call failing after the space map
            answered a batch, a device number the space map does not name -
            which every mountpoint of one device would repeat. ``False`` only
            where opening the mountpoint, or the first call through it,
            failed, which another mountpoint can change.
    """

    reading: dict[str, Any]
    settled: bool


def _refused(reason: str, *, settled: bool) -> _Answer:
    return _Answer({"error": reason[:MAX_DEVICE_TEXT]}, settled)


def _ask(target: _Target, ioctl: Ioctl, opener: Callable[[str], int], *, cap: int, max_records: int) -> _Answer:
    """One mountpoint's free-extent summary or refusal, and whether it settles the filesystem."""
    device = encode_device(target.dev)
    if device is None:
        return _refused(f"the device number {target.dev} is not major:minor", settled=True)
    try:
        fd = opener(target.mountpoint)
    except OSError as error:
        return _refused(_reason(error), settled=False)
    try:
        tally = FreeExtentTally(cap)
        counts = _walk(fd, ioctl, tally, device=device, max_records=max_records)
    except OSError as error:
        # ENOTTY is the filesystem having no GETFSMAP, the same at every mountpoint of it.
        return _refused(_reason(error), settled=isinstance(error, _MidWalkError) or error.errno == errno.ENOTTY)
    except ValueError as error:  # FreeExtentCapError is a ValueError
        return _refused(_reason(error), settled=True)
    finally:
        os.close(fd)
    if counts.records and not counts.on_device:
        # A wrong idea of the device number would otherwise read as a filesystem with no free space at all.
        message = f"the space map listed {counts.records} records and none on this filesystem's device {target.dev}"
        return _refused(message, settled=True)
    return _Answer(dict(tally.summary()), settled=True)


def _wanted(row: Mapping[str, str | bool], settled: Collection[str]) -> _Target | None:
    """The row's device and mountpoint when it is worth asking about, else ``None``."""
    dev, mountpoint = row.get("dev", ""), row.get("mountpoint", "")
    if not isinstance(dev, str) or not isinstance(mountpoint, str) or not dev:
        return None
    if row.get("fstype") not in FREE_EXTENT_MOUNT_TYPES or dev in settled:
        return None
    return _Target(dev, mountpoint)


def rows_on_disks(rows: Sequence[Mapping[str, str | bool]], devices: Collection[str]) -> list[Mapping[str, str | bool]]:
    """Every row of each filesystem that lives on an inventoried disk, so nothing else is walked.

    A filesystem is kept by the rule the filesystems section keeps a row by -
    its own device, or the device its source names, is on a disk - and keeps
    every one of its rows, so a refused or hidden mountpoint still falls back
    to the next.

    Args:
        rows: What ``mounts.read_mounts`` returned.
        devices: Every ``maj:min`` that lives on an inventoried disk.

    Returns:
        The kept rows, in their own order.
    """
    kept = {row.get("dev") for row in rows if row.get("dev") in devices or row.get("source_dev") in devices}
    return [row for row in rows if row.get("dev") in kept]


def read_free_extents(
    rows: Sequence[Mapping[str, str | bool]],
    *,
    ioctl: Ioctl | None = None,
    opener: Callable[[str], int] | None = None,
    cap: int = MAX_FREE_EXTENTS,
    max_records: int = MAX_FSMAP_RECORDS,
) -> dict[str, dict[str, Any]]:
    """Read the free extents of every ext4 and xfs filesystem once.

    Args:
        rows: What ``mounts.read_mounts`` returned.
        ioctl: The call that reaches the kernel; ``fcntl.ioctl`` by default.
        opener: Opens a mountpoint read-only; closed with ``os.close``.
        cap: How many free extents one filesystem may have before it is refused.
        max_records: How many records of any kind one walk may read before the
            filesystem is refused.

    Returns:
        One summary per filesystem device, keyed by its ``maj:min``; a device
        whose every mountpoint refused, or is hidden, is ``{"error": <reason>}``.
    """
    call, open_one = ioctl or system_ioctl, opener or _open_directory
    readings: dict[str, dict[str, Any]] = {}
    settled: set[str] = set()
    hidden_only: list[str] = []
    for row in rows:
        wanted = _wanted(row, settled)
        if wanted is None:
            continue
        dev = wanted.dev
        if row.get("hidden") is True:
            hidden_only.append(dev)
            continue
        answer = _ask(wanted, call, open_one, cap=cap, max_records=max_records)
        if answer.settled:
            settled.add(dev)
        if "error" in answer.reading and dev in readings:
            continue
        readings[dev] = answer.reading
    for dev in hidden_only:
        readings.setdefault(dev, {"error": _HIDDEN_REASON})
    return readings


__all__ = ["FS_IOC_GETFSMAP", "MAX_FSMAP_RECORDS", "encode_device", "read_free_extents", "rows_on_disks"]
