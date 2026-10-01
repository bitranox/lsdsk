"""Fetch a USB device's BOS through usbfs, for kernels that do not publish it in sysfs.

``bos_descriptors`` arrived in sysfs with kernel 6.9. On an older kernel the same
bytes come from the device itself: a standard GET_DESCRIPTOR(BOS) control
request through ``/dev/bus/usb/<bus>/<device>``, which needs root. A device whose
bcdUSB is below 2.01 has no BOS and STALLs the request; that is an answer, not
a refusal, so it is raised as :class:`NoBos` rather than as the OSError it
arrives as.

System Role:
    Impure Linux transport. Decoding the bytes is ``decode.usb.decode_bos``.
"""

from __future__ import annotations

import ctypes
import errno
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DEVICE_TO_HOST = 0x80
_GET_DESCRIPTOR = 6
_BOS_DESCRIPTOR = 0x0F
_DESCRIPTOR_TYPE_SHIFT = 8
_BOS_HEADER_LENGTH = 5
_TIMEOUT_MS = 1000

# The kernel's _IOC encoding: direction in the top two bits (3 = read and
# write), then the argument size, the type character and the number.
_IOC_READ_WRITE = 3
_IOC_DIRECTION_SHIFT = 30
_IOC_SIZE_SHIFT = 16
_IOC_TYPE_SHIFT = 8


class UsbCtrlTransfer(ctypes.Structure):
    """``struct usbdevfs_ctrltransfer`` from linux/usbdevice_fs.h."""

    _fields_ = (
        ("bRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
        ("timeout", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    )


def _iowr(kind: str, number: int, size: int) -> int:
    """The kernel's _IOWR: read-write direction, the structure size, the type and the number."""
    return (
        (_IOC_READ_WRITE << _IOC_DIRECTION_SHIFT) | (size << _IOC_SIZE_SHIFT) | (ord(kind) << _IOC_TYPE_SHIFT) | number
    )


USBDEVFS_CONTROL = _iowr("U", 0, ctypes.sizeof(UsbCtrlTransfer))


class NoBos(Exception):  # noqa: N818 - it names an answer the device gave, not a failure
    """The device STALLed GET_DESCRIPTOR(BOS): it has none."""


def read_bos(node: Path) -> bytes:
    """Read a device's whole BOS: the five-byte header first, then the length it names.

    Args:
        node: The device's usbfs node, ``/dev/bus/usb/<bus>/<device>``.

    Returns:
        The BOS descriptor and every capability after it.

    Raises:
        NoBos: The device has no BOS.
        OSError: The node could not be opened or the request was refused.
        ValueError: The device answered with a header that is not a BOS.
    """
    # Imported here rather than at module scope because fcntl is Linux-only.
    import fcntl  # noqa: PLC0415 - platform-only dependency

    fd = os.open(node, os.O_RDWR)
    try:
        header = _control_in(fd, fcntl.ioctl, length=_BOS_HEADER_LENGTH)
        if len(header) < _BOS_HEADER_LENGTH or header[1] != _BOS_DESCRIPTOR:
            raise ValueError(f"{node} answered GET_DESCRIPTOR(BOS) with {header.hex()}")
        return _control_in(fd, fcntl.ioctl, length=int.from_bytes(header[2:4], "little"))
    finally:
        os.close(fd)


def _control_in(fd: int, ioctl: Callable[[int, int, UsbCtrlTransfer], int], *, length: int) -> bytes:
    """Issue one GET_DESCRIPTOR(BOS) of ``length`` bytes and return what the device sent."""
    buffer = ctypes.create_string_buffer(length)
    transfer = UsbCtrlTransfer(
        _DEVICE_TO_HOST,
        _GET_DESCRIPTOR,
        _BOS_DESCRIPTOR << _DESCRIPTOR_TYPE_SHIFT,
        0,
        length,
        _TIMEOUT_MS,
        ctypes.addressof(buffer),
    )
    try:
        moved = ioctl(fd, USBDEVFS_CONTROL, transfer)
    except OSError as error:
        if error.errno == errno.EPIPE:
            raise NoBos from error
        raise
    return buffer.raw[: max(0, min(moved, length))]


__all__ = ["USBDEVFS_CONTROL", "NoBos", "UsbCtrlTransfer", "read_bos"]
