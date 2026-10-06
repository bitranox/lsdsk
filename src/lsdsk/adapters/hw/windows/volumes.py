"""Read Windows volumes: what each one is mounted as, and which disk it sits on.

Opening a volume needs no privilege: on a virtual and a physical Windows
host, unelevated and elevated alike, every ordinary volume opened and every
ioctl below succeeded. A volume this cannot open (one that refuses even
Administrator) is recorded with an ``error``, its paths and the drive type of
its first path; :mod:`.usage` decides from those whether its failure leaves a
disk undecidable rather than a false "not mounted".

The two Win32 responses this issues (``VOLUME_DISK_EXTENTS`` and
``PARTITION_INFORMATION_EX``) are decoded by :mod:`.volume_layout`, which also
owns the struct layouts this module sizes its ioctl buffers from; not one line
of the I/O below can execute on a Linux or macOS runner.

This module's own two functions still key and name a volume by its GUID path;
:func:`.reader.read_volumes_section` is where that GUID is rewritten to a
capture-local ordinal before anything is written to a snapshot, so no GUID
path reaches a capture.

System Role:
    Adapter layer, reading half for the Windows "used by" column.  Produces the
    plain mapping :mod:`.capture` types and :mod:`.usage` resolves.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from . import winapi as api
from .volume_layout import (
    EXTENT_ENTRY,
    EXTENTS_HEADER,
    MAX_EXTENTS,
    PARTITION_INFORMATION_EX_SIZE,
    parse_disk_extents,
    parse_is_esp,
)

_EXTENTS_BUFFER_SIZE = EXTENTS_HEADER.size + MAX_EXTENTS * EXTENT_ENTRY.size

#: How wide a buffer to offer for a volume's mount paths. A volume path is a
#: drive letter or a short folder mount, so this is generous rather than tight.
_PATHS_BUFFER_CHARS = 4096
_MAX_PATH = 260


def _volume_paths(kernel32: api.WinLibrary, volume: str) -> tuple[tuple[str, ...], str | None]:
    """Return every mount path a volume answers to, or why they were not read.

    ``GetVolumePathNamesForVolumeNameW`` writes a Windows "multi-string": each
    path NUL-terminated, the whole list ending in a second NUL. Offered too
    small a buffer it fails with ``ERROR_MORE_DATA`` and reports the length it
    needs (measured on two real hosts), so one retry at that length follows;
    the call keys on the reported length rather than the error code, which is
    what the length is for.

    Returns:
        The paths, and ``None``; or no paths and why they could not be read,
        which is a different fact from a volume with no path at all.
    """
    size = _PATHS_BUFFER_CHARS
    for _ in range(2):
        buffer = ctypes.create_unicode_buffer(size)
        needed = wintypes.DWORD()
        if kernel32.GetVolumePathNamesForVolumeNameW(volume, buffer, size, ctypes.byref(needed)):
            parts = ctypes.wstring_at(ctypes.addressof(buffer), min(needed.value, size)).split("\x00")
            return tuple(part for part in parts if part), None
        if needed.value <= size:
            break
        size = needed.value
    return (), f"could not read the volume's paths (error {api.last_error()})"


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
    response = ctypes.create_string_buffer(PARTITION_INFORMATION_EX_SIZE)
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_DISK_GET_PARTITION_INFO_EX,
        None,
        0,
        response,
        PARTITION_INFORMATION_EX_SIZE,
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return None
    return parse_is_esp(response.raw[: returned.value])


def _read_one_volume(kernel32: api.WinLibrary, volume: str) -> dict[str, Any]:
    """Read one volume's mount paths, drive type, disk extents and ESP status.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        volume: The volume's GUID path, trailing backslash included.

    Returns:
        One volume's reading, shaped for the capture model to type.
    """
    paths, paths_error = _volume_paths(kernel32, volume)
    entry: dict[str, Any] = {"paths": list(paths)}
    if paths_error is not None:
        entry["paths_error"] = paths_error
    if paths:
        # Asked of the first path rather than the GUID path: measured on a
        # volume that refuses to open, its letter answered DRIVE_FIXED while
        # its GUID path answered DRIVE_NO_ROOT_DIR.
        entry["drive_type"] = int(kernel32.GetDriveTypeW(paths[0]))
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


__all__ = ["read_volumes", "read_windows_volume"]
