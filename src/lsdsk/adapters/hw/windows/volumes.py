"""Read Windows volumes: what each one is mounted as, and which disk it sits on.

Opening a volume needs no privilege at all - Task 1 measured this on two real
Windows hosts, unelevated and elevated alike, and every open and every ioctl
below succeeded either way.  A volume this cannot open (a RAM disk that
refuses even Administrator) is recorded with an ``error`` and nothing else;
:mod:`.usage` is what turns that into an undecidable disk rather than a false
"not mounted".

The two Win32 responses this issues (``VOLUME_DISK_EXTENTS`` and
``PARTITION_INFORMATION_EX``) are decoded by :mod:`.volume_layout`, which also
owns the struct layouts this module sizes its ioctl buffers from; not one line
of the I/O below can execute on a Linux or macOS runner.

System Role:
    Adapter layer, reading half for the Windows "used by" column.  Produces the
    plain mapping :mod:`.capture` types and :mod:`.usage` resolves.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from . import winapi as api
from .volume_layout import EXTENT_ENTRY, EXTENTS_HEADER, MAX_EXTENTS, PARTITION_HEAD, parse_disk_extents, parse_is_esp

_EXTENTS_BUFFER_SIZE = EXTENTS_HEADER.size + MAX_EXTENTS * EXTENT_ENTRY.size
_PARTITION_INFO_SIZE = PARTITION_HEAD.size + 112  # the Gpt union's full width

#: How wide a buffer to offer for a volume's mount paths. A volume path is a
#: drive letter or a short folder mount, so this is generous rather than tight.
_PATHS_BUFFER_CHARS = 4096
_MAX_PATH = 260


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


__all__ = ["read_volumes", "read_windows_volume"]
