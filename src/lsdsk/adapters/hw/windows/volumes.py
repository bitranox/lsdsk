"""Read Windows volumes: what each one is mounted as, and which disk it sits on.

The two Win32 responses this enumerates (``VOLUME_DISK_EXTENTS`` and
``PARTITION_INFORMATION_EX``) are parsed from raw bytes at fixed SDK offsets
rather than through a declared ``ctypes.Structure``. That is what keeps the two
parsers testable on every runner: :func:`parse_disk_extents` and
:func:`parse_is_esp` sit above the impure part of this module for exactly that
reason, and :mod:`tests.test_windows_volume_parsing` exercises them directly.

Opening a volume needs no privilege at all - Task 1 measured this on two real
Windows hosts, unelevated and elevated alike, and every open and every ioctl
below succeeded either way.  A volume this cannot open (a RAM disk that
refuses even Administrator) is recorded with an ``error`` and nothing else;
:mod:`.usage` is what turns that into an undecidable disk rather than a false
"not mounted".

System Role:
    Adapter layer, reading half for the Windows "used by" column.  Produces the
    plain mapping :mod:`.capture` types and :mod:`.usage` resolves.
"""

from __future__ import annotations

import ctypes
import struct
import uuid
from ctypes import wintypes
from typing import Any

from . import winapi as api

#: The EFI System Partition's GPT partition type GUID.
_ESP_TYPE_GUID = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")

# VOLUME_DISK_EXTENTS: a DWORD count, padded to 8 bytes because the first
# DISK_EXTENT that follows holds a LARGE_INTEGER and so needs 8-byte alignment.
_EXTENTS_HEADER = struct.Struct("<I4x")
# DISK_EXTENT: DiskNumber (DWORD, padded the same way), StartingOffset and
# ExtentLength (both LARGE_INTEGER).
_EXTENT_ENTRY = struct.Struct("<I4xqq")
#: The most members this reads from a spanned or striped dynamic volume. A
#: configuration wider than this is not one anyone runs, and the ioctl buffer
#: below is sized once from it rather than probed and retried.
_MAX_EXTENTS = 32
_EXTENTS_BUFFER_SIZE = _EXTENTS_HEADER.size + _MAX_EXTENTS * _EXTENT_ENTRY.size

# PARTITION_INFORMATION_EX: PartitionStyle (int, padded to 8), StartingOffset,
# PartitionLength (both LARGE_INTEGER), PartitionNumber (DWORD), two BOOLEANs
# and their padding, then the Mbr/Gpt union. The GPT arm's own PartitionType
# GUID is the union's first field, so it sits right after the fixed head.
_PARTITION_HEAD = struct.Struct("<i4xqqIBB2x")
_PARTITION_TYPE_OFFSET = _PARTITION_HEAD.size
_PARTITION_INFO_SIZE = _PARTITION_HEAD.size + 112  # the Gpt union's full width

#: How wide a buffer to offer for a volume's mount paths. A volume path is a
#: drive letter or a short folder mount, so this is generous rather than tight.
_PATHS_BUFFER_CHARS = 4096
_MAX_PATH = 260


def parse_disk_extents(raw: bytes) -> list[int]:
    """Read the disk numbers a volume's extents span, from a raw ioctl response.

    Args:
        raw: The bytes ``IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`` returned.

    Returns:
        Each extent's disk number, in the order the driver reported them. A
        count the buffer does not have room for is clamped to what is there,
        and a buffer too short to carry even the header names nothing.

    Example:
        >>> import struct
        >>> raw = struct.pack("<I4x", 1) + struct.pack("<I4xqq", 3, 0, 1 << 20)
        >>> parse_disk_extents(raw)
        [3]
    """
    if len(raw) < _EXTENTS_HEADER.size:
        return []
    (declared,) = _EXTENTS_HEADER.unpack_from(raw)
    available = (len(raw) - _EXTENTS_HEADER.size) // _EXTENT_ENTRY.size
    count = min(declared, available)
    return [
        _EXTENT_ENTRY.unpack_from(raw, _EXTENTS_HEADER.size + index * _EXTENT_ENTRY.size)[0] for index in range(count)
    ]


def parse_is_esp(raw: bytes) -> bool | None:
    """Read whether a partition is the EFI System Partition, from a raw ioctl response.

    Args:
        raw: The bytes ``IOCTL_DISK_GET_PARTITION_INFO_EX`` returned.

    Returns:
        ``True`` for a GPT partition whose type is the ESP GUID, ``False`` for
        any other GPT type or an MBR partition, and ``None`` when the buffer
        is too short to carry even the partition style.

    Example:
        >>> import struct, uuid
        >>> esp = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
        >>> head = struct.pack("<i4xqqIBB2x", 1, 0, 1 << 20, 1, 0, 0)
        >>> parse_is_esp(head + esp.bytes_le + bytes(112 - 16))
        True
    """
    if len(raw) < _PARTITION_TYPE_OFFSET + 16:
        return None
    (style,) = struct.unpack_from("<i", raw, 0)
    if style != api.PARTITION_STYLE_GPT:
        return False
    type_guid = uuid.UUID(bytes_le=raw[_PARTITION_TYPE_OFFSET : _PARTITION_TYPE_OFFSET + 16])
    return type_guid == _ESP_TYPE_GUID


def _volume_paths(kernel32: api.WinLibrary, volume: str) -> tuple[str, ...]:
    """Return every mount path a volume answers to.

    ``GetVolumePathNamesForVolumeNameW`` writes a Windows "multi-string": each
    path NUL-terminated, the whole list ending in a second NUL.
    """
    buffer = ctypes.create_unicode_buffer(_PATHS_BUFFER_CHARS)
    needed = wintypes.DWORD()
    ok = kernel32.GetVolumePathNamesForVolumeNameW(volume, buffer, _PATHS_BUFFER_CHARS, ctypes.byref(needed))
    if not ok or not needed.value:
        return ()
    raw = ctypes.string_at(ctypes.addressof(buffer), needed.value * ctypes.sizeof(wintypes.WCHAR))
    parts = raw.decode("utf-16-le", errors="replace").split("\x00")
    return tuple(part for part in parts if part)


def _disk_extents(kernel32: api.WinLibrary, handle: int) -> tuple[list[int], str | None]:
    """Issue the disk-extents ioctl and parse its response.

    Returns:
        The disk numbers, and why the ioctl failed when it did (in which case
        the disk numbers are empty).
    """
    response = ctypes.create_string_buffer(_EXTENTS_BUFFER_SIZE)
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
        None,
        0,
        response,
        _EXTENTS_BUFFER_SIZE,
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return [], f"could not read the volume's disk extents (error {api.last_error()})"
    return parse_disk_extents(response.raw[: returned.value]), None


def _is_esp(kernel32: api.WinLibrary, handle: int) -> bool | None:
    """Issue the partition-info ioctl and parse whether it is the ESP."""
    response = ctypes.create_string_buffer(_PARTITION_INFO_SIZE)
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_DISK_GET_PARTITION_INFO_EX,
        None,
        0,
        response,
        _PARTITION_INFO_SIZE,
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return None
    return parse_is_esp(response.raw[: returned.value])


def _read_one_volume(kernel32: api.WinLibrary, volume: str) -> dict[str, Any]:
    """Read one volume's mount paths, disk extents and ESP status.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        volume: The volume's GUID path, trailing backslash included.

    Returns:
        One volume's reading, shaped for the capture model to type.
    """
    entry: dict[str, Any] = {"paths": list(_volume_paths(kernel32, volume))}
    handle = kernel32.CreateFileW(
        volume.rstrip("\\"),
        0,
        api.FILE_SHARE_READ | api.FILE_SHARE_WRITE,
        None,
        api.OPEN_EXISTING,
        0,
        None,
    )
    if handle == api.INVALID_HANDLE_VALUE:
        entry["error"] = f"could not open the volume (error {api.last_error()})"
        return entry
    try:
        disks, error = _disk_extents(kernel32, handle)
        entry["disks"] = disks
        if error is not None:
            entry["error"] = error
        esp = _is_esp(kernel32, handle)
        if esp is not None:
            entry["esp"] = esp
    finally:
        kernel32.CloseHandle(handle)
    return entry


def read_volumes(kernel32: api.WinLibrary) -> dict[str, dict[str, Any]]:
    r"""Enumerate every volume on this machine and read what it is used for.

    Args:
        kernel32: The typed facade over the Win32 entry points.

    Returns:
        One entry per volume, keyed by its ``\\?\Volume{...}\`` GUID path.
    """
    volumes: dict[str, dict[str, Any]] = {}
    buffer = ctypes.create_unicode_buffer(_MAX_PATH)
    handle = kernel32.FindFirstVolumeW(buffer, _MAX_PATH)
    if handle == api.INVALID_HANDLE_VALUE:
        return volumes
    try:
        while True:
            volumes[buffer.value] = _read_one_volume(kernel32, buffer.value)
            if not kernel32.FindNextVolumeW(handle, buffer, _MAX_PATH):
                break
    finally:
        kernel32.FindVolumeClose(handle)
    return volumes


def read_windows_volume(kernel32: api.WinLibrary) -> str | None:
    """Return the GUID path of the volume the Windows directory lives on.

    Args:
        kernel32: The typed facade over the Win32 entry points.

    Returns:
        The volume's GUID path, or ``None`` when any step of resolving it
        failed.
    """
    windir = ctypes.create_unicode_buffer(_MAX_PATH)
    if not kernel32.GetSystemWindowsDirectoryW(windir, _MAX_PATH):
        return None
    mount_path = ctypes.create_unicode_buffer(_MAX_PATH)
    if not kernel32.GetVolumePathNameW(windir.value, mount_path, _MAX_PATH):
        return None
    volume_guid = ctypes.create_unicode_buffer(_MAX_PATH)
    if not kernel32.GetVolumeNameForVolumeMountPointW(mount_path.value, volume_guid, _MAX_PATH):
        return None
    return volume_guid.value


__all__ = ["parse_disk_extents", "parse_is_esp", "read_volumes", "read_windows_volume"]
