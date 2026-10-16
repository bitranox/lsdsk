"""Read every imported ZFS pool's size and allocation from the control device.

Two ioctls, both answered to an unprivileged caller (OpenZFS 2.4.4:
ZFS_IOC_POOL_CONFIGS has policy zfs_secpolicy_none, ZFS_IOC_POOL_STATS has
zfs_secpolicy_read, which asks only for the global zone or a visible pool).
Linux passes the request number through as it is, with no _IOWR encoding.

Only the pool name, size and allocation, and where each pool's members are,
leave this module. The config a pool answers with names every vdev by path,
often a by-id name that embeds a drive serial, so a path is resolved here to
the device number it names and dropped; the host name and the vdev GUIDs the
config also carries never leave either.
"""

from __future__ import annotations

import ctypes
import errno
import os
import struct
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from ....domain.text import MAX_DEVICE_TEXT
from ..decode.nvlist import NvList, NvValue, decode_nvlist, pool_fragmentation, pool_space
from .mounts import device_number_of

ZFS_DEVICE = Path("/dev/zfs")
ZFS_IOC_POOL_CONFIGS = 0x5A04
ZFS_IOC_POOL_STATS = 0x5A05
ZFS_IOC_POOL_GET_PROPS = 0x5A27
#: Largest reply followed. A config grows with the vdev count; this is far past
#: any real pool and stops a kernel answer from sizing our allocation.
MAX_REPLY_BYTES = 16 * 1024 * 1024

#: Larger than sizeof(zfs_cmd_t), which the kernel copies in and out whole.
_COMMAND_BYTES = 32 * 1024
_NAME_BYTES = 4096
_DST, _DST_SIZE = 4112, 4120
_FIRST_REPLY_BYTES = 256 * 1024
_ATTEMPTS = 6
_CUT_MARK = " ... (more pools failed)"
#: fcntl.ioctl's mutate_flag: the kernel's answer is written back into the command buffer.
_WRITE_BACK = True

#: What asking about the pools can raise. The decoder checks every length before following it, so OSError and
#: ValueError are the expected two; an IndexError or a struct.error stands for a check it lacks, and must cost
#: that one reading rather than end the run with every other reading in it.
_READ_FAILURES = (OSError, ValueError, IndexError, struct.error)

_T = TypeVar("_T")

Ioctl = Callable[[int, int, bytearray], int]
#: Opens a path with the given flags. ``read_pools`` closes the descriptor it returns with
#: ``os.close``, so a substitute must return a real descriptor or raise.
Opener = Callable[[Path, int], int]


def system_ioctl(fd: int, request: int, command: bytearray) -> int:
    """Issue the real ``fcntl.ioctl``, with the kernel's answer written back into ``command``.

    Args:
        fd: An open file descriptor.
        request: The ioctl request number.
        command: The argument buffer, mutated in place by the kernel.

    Returns:
        The ioctl's return value.
    """
    import fcntl  # noqa: PLC0415 - POSIX-only module; this file must still import on Windows

    return fcntl.ioctl(fd, request, command, _WRITE_BACK)


def _ask(fd: int, request: int, name: str, ioctl: Ioctl) -> bytes:
    """Issue one ioctl, growing the reply buffer while the kernel asks for more.

    The kernel refuses a reply too large for the buffer with ``ENOMEM`` and
    writes the size it needs back. An ``ENOMEM`` that names no size larger than
    the buffer it was given - a reply refused without a bigger request, or an
    allocation that failed for its own reasons - gains nothing from the same
    buffer again, so it is refused at once, saying what was seen rather than
    which of the two it was.
    """
    room = _FIRST_REPLY_BYTES
    for _ in range(_ATTEMPTS):
        command = bytearray(_COMMAND_BYTES)
        encoded = name.encode()[: _NAME_BYTES - 1]
        command[: len(encoded)] = encoded
        reply = ctypes.create_string_buffer(room)
        struct.pack_into("<QQ", command, _DST, ctypes.addressof(reply), room)
        try:
            ioctl(fd, request, command)
        except OSError as error:
            if error.errno != errno.ENOMEM:
                raise
            needed = struct.unpack_from("<Q", command, _DST_SIZE)[0]
            if needed > MAX_REPLY_BYTES:
                msg = f"the reply is larger than {MAX_REPLY_BYTES} bytes ({needed})"
                raise ValueError(msg) from error
            if needed <= room:
                msg = f"the ioctl failed with ENOMEM on a {room}-byte buffer without asking for more ({needed} bytes)"
                raise ValueError(msg) from error
            room = needed
            continue
        written = struct.unpack_from("<Q", command, _DST_SIZE)[0]
        return ctypes.string_at(reply, min(written, room))
    msg = "the reply kept growing"
    raise ValueError(msg)


def _pool_names(fd: int, ioctl: Ioctl) -> list[str]:
    return sorted(decode_nvlist(_ask(fd, ZFS_IOC_POOL_CONFIGS, "", ioctl)))


#: The vdev keys under which a config nests further vdevs: a top-level vdev's
#: children, and the cache and spare devices the vdev tree lists beside them.
_NESTED_VDEVS = ("children", "l2cache", "spares")


def _nested(node: NvList) -> list[NvList]:
    """Every vdev nested directly under ``node``."""
    found: list[NvList] = []
    for key in _NESTED_VDEVS:
        value = node.get(key)
        if isinstance(value, tuple):
            found.extend(member for member in value if isinstance(member, Mapping))
    return found


def _leaf_vdevs(tree: NvList, device_number: Callable[[str], str | None]) -> list[dict[str, str]]:
    """Every leaf vdev with a path, as its type and the device number the path resolves to.

    Walked with an explicit stack: the decoder already bounds how deep and how
    many lists a reply may hold, so this walk is bounded by the same figures.
    """
    leaves: list[dict[str, str]] = []
    pending: list[NvList] = [tree]
    while pending:
        node = pending.pop()
        below = _nested(node)
        pending.extend(reversed(below))
        path, kind = node.get("path"), node.get("type")
        if isinstance(node.get("children"), tuple) or not isinstance(path, str) or not isinstance(kind, str):
            continue
        leaf = {"kind": kind[:MAX_DEVICE_TEXT]}
        dev = device_number(path)
        if dev is not None:
            leaf["dev"] = dev
        leaves.append(leaf)
    return leaves


def _layout(config: NvValue, device_number: Callable[[str], str | None]) -> dict[str, Any]:
    """One pool's GUID and leaf vdevs from its config, without any path or name in it."""
    if not isinstance(config, Mapping):
        return {}
    layout: dict[str, Any] = {}
    guid = config.get("pool_guid")
    if isinstance(guid, int):
        layout["guid"] = str(guid)
    tree = config.get("vdev_tree")
    if isinstance(tree, Mapping):
        layout["vdevs"] = _leaf_vdevs(tree, device_number)
    return layout


def _zfs_absent(error: OSError) -> bool:
    """Whether opening the control device failed because ZFS is not here, which is no refusal.

    No node, or a node with no driver behind it (ENODEV/ENXIO).
    """
    return isinstance(error, FileNotFoundError) or error.errno in {errno.ENODEV, errno.ENXIO}


def read_pool_layouts(
    device: Path = ZFS_DEVICE,
    *,
    ioctl: Ioctl | None = None,
    opener: Opener | None = None,
    device_number: Callable[[str], str | None] = device_number_of,
) -> dict[str, dict[str, Any]] | None:
    """Where every listed pool has its members, from each pool's own config.

    Args:
        device: The ZFS control device.
        ioctl: The call that reaches it; the system's ``fcntl.ioctl`` by default.
        opener: Opens the device read-only and returns a descriptor, which is
            closed with ``os.close``; ``os.open`` by default.
        device_number: Resolves a vdev's path to its block device number,
            overridable because no unprivileged test can create a block device.

    Returns:
        One layout per listed pool, keyed by name: ``guid`` (the pool GUID in
        decimal, as udev writes it on a member) and ``vdevs`` (every leaf vdev
        with a path, as ``kind`` and the ``dev`` it resolved to), either absent
        when the config did not carry it. Empty when there is no control
        device, so no pool to lay out. ``None`` when the device could not be
        opened or the listing could not be read, which leaves the pools to
        their member labels as in a capture taken before layouts were read:
        an empty answer there would call every listed pool untraced.
        ``read_pools`` reads the same listing and records why.
    """
    try:
        fd = (opener or os.open)(device, os.O_RDONLY)
    except OSError as error:
        return {} if _zfs_absent(error) else None
    try:
        configs = decode_nvlist(_ask(fd, ZFS_IOC_POOL_CONFIGS, "", ioctl or system_ioctl))
    except _READ_FAILURES:
        return None
    finally:
        os.close(fd)
    return {name: _layout(config, device_number) for name, config in configs.items()}


def _space_of(fd: int, pool: str, ioctl: Ioctl) -> dict[str, int]:
    space = pool_space(decode_nvlist(_ask(fd, ZFS_IOC_POOL_STATS, pool, ioctl)))
    return {"size": space.size_bytes, "alloc": space.alloc_bytes}


def _fragmentation_of(fd: int, pool: str, ioctl: Ioctl) -> int | None:
    return pool_fragmentation(decode_nvlist(_ask(fd, ZFS_IOC_POOL_GET_PROPS, pool, ioctl)))


def _bounded(text: str) -> str:
    """Cut ``text`` to the capture's text cap, marking the cut."""
    if len(text) <= MAX_DEVICE_TEXT:
        return text
    return text[: MAX_DEVICE_TEXT - len(_CUT_MARK)] + _CUT_MARK


def capture_fields(pools: dict[str, dict[str, int]] | None, error: str | None) -> dict[str, object]:
    """The reading keys the pool read contributes.

    Args:
        pools: The pools by name, or ``None`` when there was no control device.
        error: Why the pools or one pool could not be read, if it could not.

    Returns:
        ``zfs_pools`` always, and ``zfs_pools_error`` only when there is a reason.
    """
    fields: dict[str, object] = {"zfs_pools": pools}
    if error is not None:
        fields["zfs_pools_error"] = error
    return fields


def _each_pool(
    device: Path, ioctl: Ioctl | None, opener: Opener | None, read_one: Callable[[int, str, Ioctl], _T]
) -> tuple[dict[str, _T] | None, str | None]:
    """Open the control device, list the pools and read each with ``read_one``."""
    call = ioctl or system_ioctl
    try:
        fd = (opener or os.open)(device, os.O_RDONLY)
    except OSError as error:
        if _zfs_absent(error):
            return None, None
        return None, f"could not open {device} ({error.strerror})"
    try:
        try:
            names = _pool_names(fd, call)
        except _READ_FAILURES as error:
            return None, f"listing the pools failed: {error}"
        readings: dict[str, _T] = {}
        problems: list[str] = []
        for name in names:
            try:
                readings[name] = read_one(fd, name, call)
            except _READ_FAILURES as error:
                problems.append(f"pool {name}: {error}")
        return readings, _bounded("; ".join(problems)) or None
    finally:
        os.close(fd)


def read_pools(
    device: Path = ZFS_DEVICE, *, ioctl: Ioctl | None = None, opener: Opener | None = None
) -> tuple[dict[str, dict[str, int]] | None, str | None]:
    """Read every imported pool's size and allocation.

    Args:
        device: The ZFS control device.
        ioctl: The call that reaches it; the system's ``fcntl.ioctl`` by default.
        opener: Opens the device read-only and returns a descriptor, which is closed
            with ``os.close``; ``os.open`` by default.

    Returns:
        ``(None, None)`` when there is no control device (no ZFS here, or a
        container that was not given it); ``(None, reason)`` when the pools
        could not be listed; otherwise the pools by name, with the reason any
        single pool could not be read.
    """
    return _each_pool(device, ioctl, opener, _space_of)


def read_pool_fragmentation(
    device: Path = ZFS_DEVICE, *, ioctl: Ioctl | None = None, opener: Opener | None = None
) -> tuple[dict[str, int | None] | None, str | None]:
    """Read every imported pool's own FRAG, unprivileged.

    Args:
        device: The ZFS control device.
        ioctl: The call that reaches it; the system's ``fcntl.ioctl`` by default.
        opener: Opens the device read-only and returns a descriptor, which is closed
            with ``os.close``; ``os.open`` by default.

    Returns:
        ``(None, None)`` when there is no control device; ``(None, reason)`` when
        the pools could not be listed; otherwise the percentage by pool name, with
        the reason any single pool could not be read. A value of ``None`` means
        ZFS publishes no figure for that pool.
    """
    return _each_pool(device, ioctl, opener, _fragmentation_of)


def fragmentation_fields(fragmentation: dict[str, int | None] | None, error: str | None) -> dict[str, object]:
    """The reading keys the fragmentation read contributes.

    Args:
        fragmentation: Each pool's FRAG by name, or ``None`` when there was no control device.
        error: Why the reading or one pool's could not be made, if it could not.

    Returns:
        ``zfs_fragmentation`` always, and ``zfs_fragmentation_error`` only when there is a reason.
    """
    fields: dict[str, object] = {"zfs_fragmentation": fragmentation}
    if error is not None:
        fields["zfs_fragmentation_error"] = error
    return fields


__all__ = [
    "MAX_REPLY_BYTES",
    "ZFS_DEVICE",
    "ZFS_IOC_POOL_CONFIGS",
    "ZFS_IOC_POOL_GET_PROPS",
    "ZFS_IOC_POOL_STATS",
    "Ioctl",
    "Opener",
    "capture_fields",
    "fragmentation_fields",
    "read_pool_fragmentation",
    "read_pool_layouts",
    "read_pools",
    "system_ioctl",
]
