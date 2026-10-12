"""Read every imported ZFS pool's size and allocation from the control device.

Two ioctls, both answered to an unprivileged caller (OpenZFS 2.4.4:
ZFS_IOC_POOL_CONFIGS has policy zfs_secpolicy_none, ZFS_IOC_POOL_STATS has
zfs_secpolicy_read, which asks only for the global zone or a visible pool).
Linux passes the request number through as it is, with no _IOWR encoding.

Only the pool name, size and allocation leave this module. The config a pool
answers with also names every vdev by path, often a by-id name that embeds a
drive serial, so it is decoded here and dropped.
"""

from __future__ import annotations

import ctypes
import errno
import os
import struct
from collections.abc import Callable
from pathlib import Path

from ..decode.nvlist import decode_nvlist, pool_space

ZFS_DEVICE = Path("/dev/zfs")
ZFS_IOC_POOL_CONFIGS = 0x5A04
ZFS_IOC_POOL_STATS = 0x5A05
#: Largest reply followed. A config grows with the vdev count; this is far past
#: any real pool and stops a kernel answer from sizing our allocation.
MAX_REPLY_BYTES = 16 * 1024 * 1024

#: Larger than sizeof(zfs_cmd_t), which the kernel copies in and out whole.
_COMMAND_BYTES = 32 * 1024
_NAME_BYTES = 4096
_DST, _DST_SIZE = 4112, 4120
_FIRST_REPLY_BYTES = 256 * 1024
_ATTEMPTS = 6
#: fcntl.ioctl's mutate_flag: the kernel's answer is written back into the command buffer.
_WRITE_BACK = True

Ioctl = Callable[[int, int, bytearray], int]


def _system_ioctl(fd: int, request: int, command: bytearray) -> int:
    import fcntl  # noqa: PLC0415 - POSIX-only module; this file must still import on Windows

    return fcntl.ioctl(fd, request, command, _WRITE_BACK)


def _ask(fd: int, request: int, name: str, ioctl: Ioctl) -> bytes:
    """Issue one ioctl, growing the reply buffer while the kernel asks for more."""
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
            room = struct.unpack_from("<Q", command, _DST_SIZE)[0]
            if room > MAX_REPLY_BYTES:
                msg = f"the reply is larger than {MAX_REPLY_BYTES} bytes ({room})"
                raise ValueError(msg) from error
            continue
        written = struct.unpack_from("<Q", command, _DST_SIZE)[0]
        return reply.raw[: min(written, room)]
    msg = "the reply kept growing"
    raise ValueError(msg)


def _pool_names(fd: int, ioctl: Ioctl) -> list[str]:
    return sorted(decode_nvlist(_ask(fd, ZFS_IOC_POOL_CONFIGS, "", ioctl)))


def _space_of(fd: int, pool: str, ioctl: Ioctl) -> dict[str, int]:
    space = pool_space(decode_nvlist(_ask(fd, ZFS_IOC_POOL_STATS, pool, ioctl)))
    return {"size": space.size_bytes, "alloc": space.alloc_bytes}


def read_pools(
    device: Path = ZFS_DEVICE, *, ioctl: Ioctl | None = None
) -> tuple[dict[str, dict[str, int]] | None, str | None]:
    """Read every imported pool's size and allocation.

    Args:
        device: The ZFS control device.
        ioctl: The call that reaches it; the system's ``fcntl.ioctl`` by default.

    Returns:
        ``(None, None)`` when there is no control device (no ZFS here, or a
        container that was not given it); ``(None, reason)`` when the pools
        could not be listed; otherwise the pools by name, with the reason any
        single pool could not be read.
    """
    if not device.exists():
        return None, None
    call = ioctl or _system_ioctl
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError as error:
        return None, f"could not open {device} ({error.strerror})"
    try:
        try:
            names = _pool_names(fd, call)
        except (OSError, ValueError) as error:
            return None, f"listing the pools failed: {error}"
        pools: dict[str, dict[str, int]] = {}
        problems: list[str] = []
        for name in names:
            try:
                pools[name] = _space_of(fd, name, call)
            except (OSError, ValueError) as error:
                problems.append(f"pool {name}: {error}")
        return pools, "; ".join(problems) or None
    finally:
        os.close(fd)


__all__ = [
    "MAX_REPLY_BYTES",
    "ZFS_DEVICE",
    "ZFS_IOC_POOL_CONFIGS",
    "ZFS_IOC_POOL_STATS",
    "Ioctl",
    "read_pools",
]
