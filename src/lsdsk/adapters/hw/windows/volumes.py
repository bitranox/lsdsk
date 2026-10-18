"""Read Windows volumes: what each one is mounted as, and which disk it sits on.

Opening a volume for its identity needs no privilege: on a virtual and a
physical Windows host, unelevated and elevated alike, every ordinary volume
opened and every ioctl below succeeded. Opening it to read its cluster bitmap
does need Administrator, which is why that walk happens only when asked. A
volume this cannot open (one that refuses even Administrator) is recorded with
an ``error``, its paths and the drive type of its first path; :mod:`.usage`
decides from those whether its failure leaves a disk undecidable rather than a
false "not mounted".

The two Win32 responses this issues (``VOLUME_DISK_EXTENTS`` and
``PARTITION_INFORMATION_EX``) are decoded by :mod:`.volume_layout`, which also
owns the struct layouts this module sizes its ioctl buffers from. Every Win32
call here goes through the ``kernel32`` facade a caller hands in, so a fake of
it runs this module on any runner and it is measured for coverage like the
pure code, unlike :mod:`.reader`, whose device tree loads the real DLLs.

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
from typing import TYPE_CHECKING, Any, NamedTuple

from ..decode.free_extents import FreeExtentTally
from . import winapi as api
from .usage import can_be_a_row, named_first
from .volume_layout import (
    BITMAP_HEADER_BYTES,
    EXTENT_ENTRY,
    EXTENTS_HEADER,
    MAX_EXTENTS,
    PARTITION_INFORMATION_EX_SIZE,
    FreeRunFolder,
    parse_bitmap_header,
    parse_disk_extents,
    parse_is_esp,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Container

_EXTENTS_BUFFER_SIZE = EXTENTS_HEADER.size + MAX_EXTENTS * EXTENT_ENTRY.size

#: How wide a buffer to offer for a volume's mount paths. A volume path is a
#: drive letter or a short folder mount, so this is generous rather than tight.
_PATHS_BUFFER_CHARS = 4096
_MAX_PATH = 260
#: The filesystem name buffer: names are a handful of characters (NTFS, ReFS,
#: exFAT), so this is generous rather than tight.
_FS_NAME_CHARS = 64
#: How many bytes of cluster bits one bitmap call asks for: 8 Mi clusters, so a
#: 500 GB volume of 4 KiB clusters takes about sixteen calls.
_BITMAP_CHUNK_BYTES = 1 << 20


class VolumeEnumeration(NamedTuple):
    """Every volume Windows listed, or why the list could not be used.

    Attributes:
        volumes: One entry per volume, keyed by its GUID path; ``None`` when
            the enumeration failed, since a partial set reads as complete to
            :mod:`.usage`.
        error: Why the enumeration failed, ``None`` when it ran to its end.
    """

    volumes: dict[str, dict[str, Any]] | None
    error: str | None


class _ReadSoFar(NamedTuple):
    """What the walk decision reads of a volume, once its paths, drive type and extents are in."""

    paths: tuple[str, ...]
    disks: tuple[int, ...]
    drive_type: int | None


class _BitmapRefusedError(Exception):
    """The bitmap walk met an answer it cannot continue from; the message is the reason."""


def _volume_paths(
    kernel32: api.WinLibrary, volume: str, *, last_error: Callable[[], int]
) -> tuple[tuple[str, ...], str | None]:
    """Return every mount path a volume answers to, or why they were not read.

    ``GetVolumePathNamesForVolumeNameW`` writes a Windows "multi-string": each
    path NUL-terminated, the whole list ending in a second NUL. Offered too
    small a buffer it fails with ``ERROR_MORE_DATA`` and reports the length it
    needs (measured on two real hosts), so one retry at that length follows;
    the call keys on the reported length rather than the error code, which is
    what the length is for.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        volume: The volume's GUID path, trailing backslash included.
        last_error: Answers ``GetLastError`` for the call that just failed.

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
    return (), f"could not read the volume's paths (error {last_error()})"


def _disk_extents(
    kernel32: api.WinLibrary, handle: int, *, last_error: Callable[[], int]
) -> tuple[list[int], str | None]:
    """Issue the disk-extents ioctl and parse its response.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: The volume, opened for its identity.
        last_error: Answers ``GetLastError`` for the call that just failed.

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
        return [], f"could not read the volume's disk extents (error {last_error()})"
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


def _volume_space(kernel32: api.WinLibrary, path: str, *, last_error: Callable[[], int]) -> dict[str, Any]:
    """Read one volume's capacity, free space, filesystem name and read-only flag.

    Asked of the volume's first mount path, the same rule as ``GetDriveTypeW``:
    measured unelevated on Windows 11, both calls answer there. The two calls
    stand alone, so a refusal of one leaves the other's figures in place.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        path: The volume's first mount path.
        last_error: Answers ``GetLastError`` for the call that just failed.

    Returns:
        The figures that were read, plus ``space_error`` when the space call
        was refused and ``fs_info_error`` when ``GetVolumeInformationW`` was,
        so an absent field is never an unexplained one.
    """
    available, total, free = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
    entry: dict[str, Any] = {}
    if kernel32.GetDiskFreeSpaceExW(path, ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
        entry.update(total_bytes=total.value, free_bytes=free.value, available_bytes=available.value)
    else:
        entry["space_error"] = f"could not read the volume's free space (error {last_error()})"
    flags = api.DWORD()
    fs_name = ctypes.create_unicode_buffer(_FS_NAME_CHARS)
    if kernel32.GetVolumeInformationW(path, None, 0, None, None, ctypes.byref(flags), fs_name, _FS_NAME_CHARS):
        entry["fs_type"] = fs_name.value
        entry["read_only"] = bool(flags.value & api.FILE_READ_ONLY_VOLUME)
    else:
        entry["fs_info_error"] = f"could not read the volume's filesystem name (error {last_error()})"
    return entry


def _cluster_bytes(kernel32: api.WinLibrary, path: str, *, last_error: Callable[[], int]) -> int:
    """One cluster's size in bytes, from the volume's first mount path.

    Raises:
        _BitmapRefusedError: The call was refused or answered zero.
    """
    sectors, sector_bytes, free, total = api.DWORD(), api.DWORD(), api.DWORD(), api.DWORD()
    if not kernel32.GetDiskFreeSpaceW(
        path, ctypes.byref(sectors), ctypes.byref(sector_bytes), ctypes.byref(free), ctypes.byref(total)
    ):
        message = f"could not read the cluster size (error {last_error()})"
        raise _BitmapRefusedError(message)
    if sectors.value * sector_bytes.value <= 0:
        message = "the volume reported a cluster size of zero"
        raise _BitmapRefusedError(message)
    return sectors.value * sector_bytes.value


class _BitmapAnswer(NamedTuple):
    """One ``FSCTL_GET_VOLUME_BITMAP`` answer, its header read and its byte count checked.

    Attributes:
        more: Whether the call answered ``ERROR_MORE_DATA``, clusters being left over.
        lcn: The first cluster its bits describe.
        remaining: How many clusters the volume has from ``lcn`` on.
        data: The cluster bits it returned, header excluded.
    """

    more: bool
    lcn: int
    remaining: int
    data: bytes


def _ask_bitmap(
    kernel32: api.WinLibrary,
    handle: int,
    output: ctypes.Array[ctypes.c_char],
    start: int,
    *,
    last_error: Callable[[], int],
) -> _BitmapAnswer:
    """Ask for the cluster bits from ``start`` on, and read back what the call returned.

    Raises:
        _BitmapRefusedError: The call failed other than with ``ERROR_MORE_DATA``,
            or returned less than its own header, which would otherwise be read
            from whatever the buffer held.
        ValueError: A header field is negative.
    """
    starting, returned = ctypes.c_int64(start), api.DWORD()
    done = kernel32.DeviceIoControl(
        handle,
        api.FSCTL_GET_VOLUME_BITMAP,
        ctypes.byref(starting),
        ctypes.sizeof(starting),
        output,
        len(output),
        ctypes.byref(returned),
        None,
    )
    code = 0 if done else last_error()
    if code not in (0, api.ERROR_MORE_DATA):
        message = f"could not read the volume bitmap (error {code})"
        raise _BitmapRefusedError(message)
    raw = output.raw[: returned.value]
    if len(raw) < BITMAP_HEADER_BYTES:
        message = f"the volume bitmap answer is {len(raw)} bytes, shorter than its {BITMAP_HEADER_BYTES}-byte header"
        raise _BitmapRefusedError(message)
    lcn, remaining = parse_bitmap_header(raw)
    return _BitmapAnswer(code == api.ERROR_MORE_DATA, lcn, remaining, raw[BITMAP_HEADER_BYTES:])


def _check_answer(answer: _BitmapAnswer, start: int, clusters: int) -> int:
    """How many clusters ``answer`` carries, once it is known to continue the walk at ``start``.

    Args:
        answer: The call's answer.
        start: The cluster the call asked from.
        clusters: How many clusters the volume has, from its first answer.

    Returns:
        The clusters to fold, counted from ``answer.lcn``.

    Raises:
        _BitmapRefusedError: The answer starts past ``start``, describes a
            volume of another size, ends early, or does not advance.
    """
    if answer.lcn > start:
        message = f"the volume bitmap restarted at cluster {answer.lcn}, past {start}"
        raise _BitmapRefusedError(message)
    if answer.lcn + answer.remaining != clusters:
        message = f"the volume bitmap changed from {clusters} to {answer.lcn + answer.remaining} clusters mid-walk"
        raise _BitmapRefusedError(message)
    nbits = min(len(answer.data) * 8, answer.remaining)
    if not answer.more and nbits < answer.remaining:
        message = f"the volume bitmap ended after {answer.lcn + nbits} of {clusters} clusters"
        raise _BitmapRefusedError(message)
    if answer.more and answer.lcn + nbits <= start:
        message = "the volume bitmap did not advance"
        raise _BitmapRefusedError(message)
    return nbits


def _walk_bitmap(
    kernel32: api.WinLibrary, handle: int, folder: FreeRunFolder, *, last_error: Callable[[], int], chunk_bytes: int
) -> None:
    """Fold a volume's whole cluster bitmap into ``folder``, one call's worth at a time.

    ``ERROR_MORE_DATA`` is the normal answer to a call that filled its buffer
    and left clusters over, so the next call starts where this one ended. The
    filesystem may round the starting cluster DOWN (Microsoft Learn,
    FSCTL_GET_VOLUME_BITMAP: "The starting LCN in the input buffer may be
    rounded down before the bitmap is calculated"), so an answer starting
    before the cluster asked for is folded from that cluster on; one starting
    after it would leave clusters unread, and is refused.

    The first answer's starting cluster plus its ``BitmapSize`` is the
    volume's cluster count, and every later answer must name the same one.
    That is the walk's ceiling: each call advances at least one cluster and
    none goes past the count, so a driver answering ``ERROR_MORE_DATA``
    forever cannot keep it walking.

    Raises:
        _BitmapRefusedError: A call failed another way, or an answer could not
            be continued from (see :func:`_check_answer`).
    """
    output = ctypes.create_string_buffer(BITMAP_HEADER_BYTES + chunk_bytes)
    start = 0
    clusters: int | None = None
    while True:
        answer = _ask_bitmap(kernel32, handle, output, start, last_error=last_error)
        if clusters is None:
            clusters = answer.lcn + answer.remaining
        nbits = _check_answer(answer, start, clusters)
        folder.feed(answer.data, nbits, skip=start - answer.lcn)
        if not answer.more:
            return
        start = answer.lcn + nbits


def _free_extents(
    kernel32: api.WinLibrary, volume: str, path: str, *, last_error: Callable[[], int], chunk_bytes: int
) -> dict[str, Any]:
    """One volume's free-extent summary from its cluster bitmap, or ``{"error": ...}``.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        volume: The volume's GUID path, trailing backslash included.
        path: The volume's first mount path, which the cluster size is asked of.
        last_error: Answers ``GetLastError`` for the call that just failed.
        chunk_bytes: How many bytes of cluster bits one call asks for.

    Returns:
        The count, total, largest and histogram of the free runs, or the
        reason the bitmap was not read to its end.
    """
    try:
        cluster = _cluster_bytes(kernel32, path, last_error=last_error)
    except _BitmapRefusedError as refused:
        return {"error": str(refused)}
    handle = kernel32.CreateFileW(
        volume.rstrip("\\"),
        api.GENERIC_READ,
        api.FILE_SHARE_READ | api.FILE_SHARE_WRITE,
        None,
        api.OPEN_EXISTING,
        0,
        None,
    )
    if handle == api.INVALID_HANDLE_VALUE:
        return {"error": f"could not open the volume for reading (error {last_error()})"}
    tally = FreeExtentTally()
    folder = FreeRunFolder(cluster, tally)
    try:
        _walk_bitmap(kernel32, handle, folder, last_error=last_error, chunk_bytes=chunk_bytes)
        folder.finish()
    # The tally's cap and total bounds and a negative header field are ValueErrors.
    except (_BitmapRefusedError, ValueError) as refused:
        return {"error": str(refused)}
    finally:
        kernel32.CloseHandle(handle)
    return dict(tally.summary())


def _read_one_volume(
    kernel32: api.WinLibrary,
    volume: str,
    *,
    last_error: Callable[[], int] = api.last_error,
    walk_on: Container[int] | None = None,
    chunk_bytes: int = _BITMAP_CHUNK_BYTES,
) -> dict[str, Any]:
    """Read one volume's mount paths, drive type, space, disk extents and ESP status.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        volume: The volume's GUID path, trailing backslash included.
        last_error: Answers ``GetLastError`` for whichever call just failed.
        walk_on: The inventoried disk numbers, when the volume's cluster
            bitmap is to be walked into free extents, which opens it for
            reading and so needs Administrator; ``None`` walks nothing. Only
            a volume that can be a row on one of these disks is walked (see
            :func:`.usage.can_be_a_row`), which is why its extents are read
            first.
        chunk_bytes: How many bytes of cluster bits one bitmap call asks for.

    Returns:
        One volume's reading, shaped for the capture model to type.
    """
    listed, paths_error = _volume_paths(kernel32, volume, last_error=last_error)
    # Letters first before anything is asked of a path, so the drive type, the
    # space and the cluster size are read from the letter the row is named by.
    paths = named_first(listed)
    entry: dict[str, Any] = {"paths": list(paths)}
    if paths_error is not None:
        entry["paths_error"] = paths_error
    if paths:
        # Asked of the first path rather than the GUID path: measured on a
        # volume that refuses to open, its letter answered DRIVE_FIXED while
        # its GUID path answered DRIVE_NO_ROOT_DIR.
        entry["drive_type"] = int(kernel32.GetDriveTypeW(paths[0]))
        entry.update(_volume_space(kernel32, paths[0], last_error=last_error))
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
        entry["error"] = f"could not open the volume (error {last_error()})"
        return entry
    try:
        disks, error = _disk_extents(kernel32, handle, last_error=last_error)
        entry["disks"] = disks
        if error is not None:
            entry["error"] = error
        esp = _is_esp(kernel32, handle)
        if esp is not None:
            entry["esp"] = esp
    finally:
        kernel32.CloseHandle(handle)
    read_so_far = _ReadSoFar(paths, tuple(entry.get("disks", ())), entry.get("drive_type"))
    if walk_on is not None and can_be_a_row(read_so_far, walk_on):
        entry["free_extents"] = _free_extents(
            kernel32, volume, paths[0], last_error=last_error, chunk_bytes=chunk_bytes
        )
    return entry


def read_volumes(
    kernel32: api.WinLibrary,
    *,
    last_error: Callable[[], int] = api.last_error,
    walk_on: Container[int] | None = None,
    chunk_bytes: int = _BITMAP_CHUNK_BYTES,
) -> VolumeEnumeration:
    r"""Enumerate every volume on this machine and read what it is used for.

    Both enumeration calls report the end of the list as a failure carrying
    ``ERROR_NO_MORE_FILES``; any other failure means the set was not read, or
    not read to its end. That is returned as no volumes at all rather than as
    the volumes seen so far, because :mod:`.usage` reads a mapping as complete
    and would call every disk on a missing volume "not mounted"; the reason
    goes with it, so the run can say it is incomplete.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        last_error: Answers ``GetLastError`` for the call that just failed.
        walk_on: The inventoried disk numbers, to also walk the cluster
            bitmap of each volume that can be a row on one of them, which
            needs Administrator; ``None`` walks none.
        chunk_bytes: How many bytes of cluster bits one bitmap call asks for.

    Returns:
        One entry per volume, keyed by its ``\\?\Volume{...}\`` GUID path,
        or no volumes and the reason when the enumeration failed.
    """
    volumes: dict[str, dict[str, Any]] = {}
    buffer = ctypes.create_unicode_buffer(_MAX_PATH)
    handle = kernel32.FindFirstVolumeW(buffer, _MAX_PATH)
    if handle == api.INVALID_HANDLE_VALUE:
        code = last_error()
        if code == api.ERROR_NO_MORE_FILES:
            return VolumeEnumeration(volumes, None)
        return VolumeEnumeration(None, f"could not list the volumes (Win32 error {code})")
    try:
        while True:
            volumes[buffer.value] = _read_one_volume(
                kernel32, buffer.value, last_error=last_error, walk_on=walk_on, chunk_bytes=chunk_bytes
            )
            if not kernel32.FindNextVolumeW(handle, buffer, _MAX_PATH):
                return _list_end(volumes, last_error())
    finally:
        kernel32.FindVolumeClose(handle)


def _list_end(volumes: dict[str, dict[str, Any]], code: int) -> VolumeEnumeration:
    """The volumes listed, when ``FindNextVolumeW`` ended the list; none and why, when it failed.

    Example:
        >>> _list_end({"a": {}}, api.ERROR_NO_MORE_FILES).error is None
        True
        >>> _list_end({"a": {}}, 21).error
        'the list of volumes ended early at entry 2 (Win32 error 21), so none of it was used'
    """
    if code == api.ERROR_NO_MORE_FILES:
        return VolumeEnumeration(volumes, None)
    return VolumeEnumeration(
        None,
        f"the list of volumes ended early at entry {len(volumes) + 1} (Win32 error {code}), so none of it was used",
    )


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


__all__ = ["VolumeEnumeration", "read_volumes", "read_windows_volume"]
