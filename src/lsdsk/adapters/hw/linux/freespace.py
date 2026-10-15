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
it), and the next mountpoint only after a refusal.

System Role:
    Adapter layer, Linux reading half. Impure: opens mountpoints and issues ioctls.
"""

from __future__ import annotations

import os
import struct
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from ....domain.models import FREE_EXTENT_MOUNT_TYPES
from ....domain.text import MAX_DEVICE_TEXT
from ..decode.free_extents import MAX_FREE_EXTENTS, FreeExtentTally
from .zfs import Ioctl, system_ioctl

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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
_HIDDEN_REASON: Final = (
    "another mount hides every mountpoint of it, so the free-space map there would read a different filesystem"
)


def _open_directory(mountpoint: str) -> int:
    # O_DIRECTORY is Linux-only; read at call time so the module imports anywhere.
    return os.open(
        mountpoint.encode("utf-8", "surrogateescape"),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )


def _new_head() -> bytearray:
    head = bytearray(_RECORDS + _RECORDS_PER_CALL * _RECORD.size)
    _HEAD.pack_into(head, 0, 0, 0, _RECORDS_PER_CALL, 0)
    _RECORD.pack_into(head, _HIGH_KEY, _ALL_ONES_32, _ALL_ONES_32, _ALL_ONES_64, _ALL_ONES_64, _ALL_ONES_64, 0)
    return head


def _fold_batch(head: bytearray, entries: int, tally: FreeExtentTally) -> int:
    """Fold one call's free records into ``tally``; return the last record's flags."""
    flags = 0
    for index in range(entries):
        _, flags, _, owner, _, length = _RECORD.unpack_from(head, _RECORDS + index * _RECORD.size)
        if flags & _SPECIAL_OWNER and owner == _OWN_FREE:
            tally.add(length)
    return flags


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


def _walk(fd: int, ioctl: Ioctl, tally: FreeExtentTally, max_records: int) -> None:
    """Every free record of the filesystem behind ``fd``, folded into ``tally``.

    Raises:
        OSError: The kernel refused the call.
        ValueError: The kernel answered something the walk cannot continue from,
            or the filesystem has more records than ``max_records``.
        FreeExtentCapError: The filesystem has more free extents than the cap.
    """
    head = _new_head()
    previous: tuple[bytes, tuple[int, int]] | None = None
    seen = 0
    while True:
        ioctl(fd, FS_IOC_GETFSMAP, head)
        entries = _HEAD.unpack_from(head, 0)[3]
        if entries == 0:
            return
        seen += entries
        _check_batch(entries, seen, max_records)
        if _fold_batch(head, entries, tally) & _LAST:
            return
        last, position = _last_position(head, entries)
        # XFS lists a block shared by several owners as several records at one physical address and continues
        # inside that run by owner and offset, so two batches may end at one place; only going backwards, or
        # returning the very same record again, is a kernel that did not advance.
        if previous is not None and (position < previous[1] or last == previous[0]):
            message = "the kernel did not advance past its last record"
            raise ValueError(message)
        previous = (last, position)
        head[_LOW_KEY : _LOW_KEY + _RECORD.size] = last


def _reason(error: Exception) -> str:
    text = (error.strerror if isinstance(error, OSError) else None) or str(error)
    return text[:MAX_DEVICE_TEXT]


def _ask(mountpoint: str, ioctl: Ioctl, opener: Callable[[str], int], *, cap: int, max_records: int) -> dict[str, Any]:
    """One mountpoint's free-extent summary, or ``{"error": <reason>}``."""
    try:
        fd = opener(mountpoint)
    except OSError as error:
        return {"error": _reason(error)}
    try:
        tally = FreeExtentTally(cap)
        _walk(fd, ioctl, tally, max_records)
    except (OSError, ValueError) as error:  # FreeExtentCapError is a ValueError
        return {"error": _reason(error)}
    finally:
        os.close(fd)
    return dict(tally.summary())


class _Target(NamedTuple):
    """A mountpoint worth asking, and the filesystem device it belongs to."""

    dev: str
    mountpoint: str


def _wanted(row: Mapping[str, str | bool], readings: Mapping[str, Any]) -> _Target | None:
    """The row's device and mountpoint when it is worth asking about, else ``None``."""
    dev, mountpoint = row.get("dev", ""), row.get("mountpoint", "")
    if not isinstance(dev, str) or not isinstance(mountpoint, str) or not dev:
        return None
    if row.get("fstype") not in FREE_EXTENT_MOUNT_TYPES or "count" in readings.get(dev, {}):
        return None
    return _Target(dev, mountpoint)


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
    hidden_only: list[str] = []
    for row in rows:
        wanted = _wanted(row, readings)
        if wanted is None:
            continue
        dev = wanted.dev
        if row.get("hidden") is True:
            hidden_only.append(dev)
            continue
        reading = _ask(wanted.mountpoint, call, open_one, cap=cap, max_records=max_records)
        if "error" in reading and dev in readings:
            continue
        readings[dev] = reading
    for dev in hidden_only:
        readings.setdefault(dev, {"error": _HIDDEN_REASON})
    return readings


__all__ = ["FS_IOC_GETFSMAP", "MAX_FSMAP_RECORDS", "read_free_extents"]
