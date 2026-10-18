"""What the Windows volume reader records, driven through a fake ``kernel32``.

``kernel32`` is the edge the reader talks to, so a fake of it holds the
request shapes and the bookkeeping on every runner, not only on a Windows
machine with an optical drive or a separately secured volume. The answers
follow what two real Windows hosts returned: ``GetVolumePathNamesForVolumeNameW``
offered too small a buffer fails with ``ERROR_MORE_DATA`` and reports the size
it needs, and a separately secured fixed volume refuses to open even elevated
while ``GetDriveTypeW`` on its letter still answers ``DRIVE_FIXED``.
"""

from __future__ import annotations

import ctypes
import json
import struct
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.hw.decode.free_extents import MAX_FREE_EXTENTS
from lsdsk.adapters.hw.windows import volumes
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.capture import VolumeEntry, WindowsCapture
from lsdsk.adapters.hw.windows.filesystems import build_filesystems
from lsdsk.adapters.hw.windows.reader import read_volumes_section
from lsdsk.adapters.hw.windows.usage import resolve_usage
from lsdsk.domain.enums import Environment

if TYPE_CHECKING:
    from lsdsk.domain.models import DiskUsage

DRIVE_FIXED = 3
ESP_TYPE = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
BASIC_DATA_TYPE = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")


def _gpt_partition(type_guid: uuid.UUID) -> bytes:
    """PARTITION_INFORMATION_EX for a GPT partition: style at byte 0, PartitionType at byte 32, 144 in all."""
    raw = bytearray(144)
    raw[0:4] = (1).to_bytes(4, "little")
    raw[32:48] = type_guid.bytes_le
    return bytes(raw)


ERROR_ACCESS_DENIED = 5
ERROR_NOT_READY = 21
#: What the fake's three identity calls fail with, each a different number so a
#: test can tell which call's error a message carries.
ERROR_INVALID_FUNCTION = 1
ERROR_SHARING_VIOLATION = 32
ERROR_INVALID_PARAMETER = 87


@dataclass
class FakeVolume:
    """One volume as the fake answers for it.

    Attributes:
        paths: Its mount paths, or ``None`` when asking for them fails outright.
        drive_type: What ``GetDriveTypeW`` answers for its first path.
        disks: Its extents' disk numbers, or ``None`` when that ioctl fails.
        openable: Whether ``CreateFileW`` opens it.
        esp: Whether its GPT partition is the EFI System Partition, or ``None``
            when the partition ioctl fails.
        space: Total, free and free-for-the-caller bytes, or ``None`` when
            ``GetDiskFreeSpaceExW`` fails.
        fs_type: The filesystem name, or ``None`` when ``GetVolumeInformationW`` fails.
        flags: The file-system flags ``GetVolumeInformationW`` reports.
        bitmap: Its cluster bitmap (``1`` is in use), or ``None`` when the
            volume refuses an open for reading.
        cluster_bytes: The size of one cluster.
        bitmap_rounding: The multiple ``FSCTL_GET_VOLUME_BITMAP`` rounds a
            starting cluster down to, which the documentation allows.
        bitmap_lcn_ahead: Clusters the answer's starting cluster lies past
            the one asked for, which no documented answer does.
        bitmap_error: The error the bitmap call fails with, or ``None``.
        bitmap_stalls: Answer ``ERROR_MORE_DATA`` with no cluster bits at all.
        bitmap_short: Answer the last call with success and only half the
            cluster bits its header says are left.
        bitmap_returned: The byte count every bitmap answer reports, whatever
            it wrote, or ``None`` for the length it wrote.
        bitmap_grows: How many clusters larger each answer reports the volume
            than the answer before it.
        cluster_error: The error ``GetDiskFreeSpaceW`` fails with, or ``None``.
        ioctl_raises: Raise from the identity ioctls rather than answer, the way
            a ctypes call raises on an argument it cannot convert.
    """

    paths: tuple[str, ...] | None = ()
    drive_type: int = DRIVE_FIXED
    disks: tuple[int, ...] | None = (0,)
    openable: bool = True
    esp: bool | None = None
    space: tuple[int, int, int] | None = (100, 40, 30)
    fs_type: str | None = "NTFS"
    flags: int = 0
    bitmap: list[int] | None = None
    cluster_bytes: int = 4096
    bitmap_rounding: int = 1
    bitmap_lcn_ahead: int = 0
    bitmap_error: int | None = None
    bitmap_stalls: bool = False
    bitmap_short: bool = False
    bitmap_returned: int | None = None
    bitmap_grows: int = 0
    cluster_error: int | None = None
    ioctl_raises: bool = False


class RunawayWalkError(Exception):
    """The walk asked for more bitmap calls than the fake will answer: it would have looped."""


#: More bitmap calls than any walk in this file needs; the fake stops a walk past it.
BITMAP_CALL_LIMIT = 200


def _pack(bits: list[int]) -> bytes:
    """Cluster bits, least significant bit first, as a ``VOLUME_BITMAP_BUFFER`` carries them."""
    # Reversed so the last cluster is the most significant digit; linear, unlike summing shifted bits.
    value = int("".join(map(str, reversed(bits))) or "0", 2)
    return value.to_bytes((len(bits) + 7) // 8, "little")


def _referent(proxy: object) -> Any:
    """The object a ``ctypes.byref()`` proxy wraps, which a fake reads and writes in place of the caller's."""
    return getattr(proxy, "_obj")  # noqa: B009 - a CArgObject's referent is reached by name alone


@dataclass
class FakeVolumeKernel:
    """``kernel32`` as the volume reader sees it."""

    volumes: dict[str, FakeVolume]
    windows_volume: str | None = None
    #: Device paths other than a volume's that refuse to open, each with the error it leaves.
    refused_opens: dict[str, int] = field(default_factory=dict[str, int])
    first_error: int | None = None
    next_error: int | None = None
    error: int = 0
    path_calls: list[int] = field(default_factory=list[int])
    opened: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    bitmap_calls: int = 0
    closed: list[int] = field(default_factory=list[int])
    granted: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    find_closed: int = 0
    asked_roots: list[str] = field(default_factory=list[str])
    _order: list[str] = field(default_factory=list[str])
    _handles: dict[int, str] = field(default_factory=dict[int, str])

    def GetSystemWindowsDirectoryW(self, buffer: ctypes.Array[ctypes.c_wchar], size: int) -> int:  # noqa: N802 - Win32 name
        del size
        buffer.value = "C:\\Windows"
        return 1

    def GetVolumePathNameW(  # noqa: N802 - the Win32 entry point's own name
        self, path: str, buffer: ctypes.Array[ctypes.c_wchar], size: int
    ) -> int:
        del path, size
        buffer.value = "C:\\"
        return 1

    def GetVolumeNameForVolumeMountPointW(  # noqa: N802 - the Win32 entry point's own name
        self, mount_path: str, buffer: ctypes.Array[ctypes.c_wchar], size: int
    ) -> int:
        del mount_path, size
        if self.windows_volume is None:
            return 0
        buffer.value = self.windows_volume
        return 1

    def last_error(self) -> int:
        """The error the last failing call left, as ``GetLastError`` reports it."""
        return self.error

    def FindFirstVolumeW(self, buffer: ctypes.Array[ctypes.c_wchar], size: int) -> int:  # noqa: N802 - Win32 name
        del size
        self._order = list(self.volumes)
        if self.first_error is not None or not self._order:
            self.error = api.ERROR_NO_MORE_FILES if self.first_error is None else self.first_error
            return cast("int", api.INVALID_HANDLE_VALUE)
        buffer.value = self._order.pop(0)
        return 7

    def FindNextVolumeW(self, handle: int, buffer: ctypes.Array[ctypes.c_wchar], size: int) -> int:  # noqa: N802 - Win32 name
        del handle, size
        if self.next_error is not None or not self._order:
            self.error = api.ERROR_NO_MORE_FILES if self.next_error is None else self.next_error
            return 0
        buffer.value = self._order.pop(0)
        return 1

    def FindVolumeClose(self, handle: int) -> int:  # noqa: N802 - the Win32 entry point's own name
        del handle
        self.find_closed += 1
        return 1

    def GetVolumePathNamesForVolumeNameW(  # noqa: N802 - the Win32 entry point's own name
        self, volume: str, buffer: ctypes.Array[ctypes.c_wchar], size: int, needed: object
    ) -> int:
        self.path_calls.append(size)
        paths = self.volumes[volume].paths
        if paths is None:
            self.error = ERROR_INVALID_PARAMETER
            return 0
        multi = "".join(f"{path}\x00" for path in paths) + "\x00"
        _referent(needed).value = len(multi)
        if len(multi) > size:
            return 0
        for index, char in enumerate(multi):
            buffer[index] = char
        return 1

    def GetDriveTypeW(self, root: str) -> int:  # noqa: N802 - the Win32 entry point's own name
        return self._volume_at(root).drive_type

    def _volume_at(self, root: str) -> FakeVolume:
        """The volume a root-taking call names, recording the root it was asked of."""
        self.asked_roots.append(root)
        return next(volume for volume in self.volumes.values() if volume.paths and root in volume.paths)

    def GetDiskFreeSpaceExW(  # noqa: N802 - the Win32 entry point's own name
        self, root: str, available: object, total: object, free: object
    ) -> int:
        space = self._volume_at(root).space
        if space is None:
            self.error = ERROR_NOT_READY
            return 0
        _referent(total).value = space[0]
        _referent(free).value = space[1]
        _referent(available).value = space[2]
        return 1

    def GetDiskFreeSpaceW(  # noqa: N802 - the Win32 entry point's own name
        self, root: str, sectors: object, sector_bytes: object, free: object, total: object
    ) -> int:
        del free, total
        volume = self._volume_at(root)
        if volume.cluster_error is not None:
            self.error = volume.cluster_error
            return 0
        _referent(sectors).value = volume.cluster_bytes // 512
        _referent(sector_bytes).value = 512
        return 1

    def GetVolumeInformationW(  # noqa: N802 - the Win32 entry point's own name
        self,
        root: str,
        name: object,
        name_len: int,
        serial: object,
        max_component: object,
        flags: object,
        fs_name: ctypes.Array[ctypes.c_wchar],
        fs_len: int,
    ) -> int:
        del name, name_len, serial, max_component, fs_len
        volume = self._volume_at(root)
        if volume.fs_type is None:
            self.error = ERROR_ACCESS_DENIED
            return 0
        fs_name.value = volume.fs_type
        _referent(flags).value = volume.flags
        return 1

    def CreateFileW(  # noqa: N802 - the Win32 entry point's own name
        self, path: str, access: int, share: int, security: object, disposition: int, flags: int, template: object
    ) -> int:
        del share, security, disposition, flags, template
        if path in self.refused_opens:
            self.error = self.refused_opens[path]
            return cast("int", api.INVALID_HANDLE_VALUE)
        name = path + "\\"
        self.opened.append((path, access))
        volume = self.volumes[name]
        if not volume.openable:
            self.error = ERROR_SHARING_VIOLATION
            return cast("int", api.INVALID_HANDLE_VALUE)
        if access == api.GENERIC_READ and volume.bitmap is None:
            self.error = ERROR_ACCESS_DENIED
            return cast("int", api.INVALID_HANDLE_VALUE)
        handle = 100 + len(self._handles)
        self._handles[handle] = name
        self.granted.append((handle, access))
        return handle

    def CloseHandle(self, handle: int) -> int:  # noqa: N802 - the Win32 entry point's own name
        self.closed.append(handle)
        return 1

    def DeviceIoControl(  # noqa: N802 - the Win32 entry point's own name
        self,
        handle: int,
        code: int,
        in_buffer: object,
        in_size: int,
        out_buffer: ctypes.Array[ctypes.c_char],
        out_size: int,
        returned: object,
        overlapped: object,
    ) -> int:
        del in_size, overlapped
        volume = self.volumes[self._handles[handle]]
        if code == api.FSCTL_GET_VOLUME_BITMAP:
            return self._bitmap_answer(volume, in_buffer, out_buffer, out_size, returned)
        if volume.ioctl_raises:
            raise OSError(code)
        if code == api.IOCTL_DISK_GET_PARTITION_INFO_EX:
            answer = _gpt_partition(ESP_TYPE if volume.esp else BASIC_DATA_TYPE) if volume.esp is not None else None
        elif code == api.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS and volume.disks is not None:
            disks = volume.disks
            answer = struct.pack("<I4x", len(disks)) + b"".join(struct.pack("<I4xqq", d, 0, 1 << 30) for d in disks)
        else:
            answer = None
        if answer is None:
            if code == api.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS:
                self.error = ERROR_INVALID_FUNCTION
            return 0
        assert len(answer) <= out_size
        ctypes.memmove(out_buffer, answer, len(answer))
        _referent(returned).value = len(answer)
        return 1

    def _bitmap_answer(
        self,
        volume: FakeVolume,
        in_buffer: object,
        out_buffer: ctypes.Array[ctypes.c_char],
        out_size: int,
        returned: object,
    ) -> int:
        """``FSCTL_GET_VOLUME_BITMAP``: as many cluster bits as fit, ``ERROR_MORE_DATA`` when some do not."""
        assert volume.bitmap is not None
        self.bitmap_calls += 1
        if self.bitmap_calls > BITMAP_CALL_LIMIT:
            raise RunawayWalkError(self.bitmap_calls)
        if volume.bitmap_error is not None:
            self.error = volume.bitmap_error
            return 0
        asked = _referent(in_buffer).value
        start = asked - asked % volume.bitmap_rounding + volume.bitmap_lcn_ahead
        room_bits = 0 if volume.bitmap_stalls else (out_size - 16) * 8
        size = len(volume.bitmap) - start + volume.bitmap_grows * (self.bitmap_calls - 1)
        # Past the bitmap's own end a growing volume reads as in use, so only its size is wrong.
        bits = (volume.bitmap[start:] + [1] * size)[:size]
        header = start.to_bytes(8, "little", signed=True) + size.to_bytes(8, "little", signed=True)
        sent = bits[: len(bits) // 2] if volume.bitmap_short and len(bits) <= room_bits else bits[:room_bits]
        payload = header + _pack(sent)
        ctypes.memmove(out_buffer, payload, len(payload))
        _referent(returned).value = len(payload) if volume.bitmap_returned is None else volume.bitmap_returned
        if len(bits) > room_bits:
            self.error = api.ERROR_MORE_DATA
            return 0
        return 1


def _read(fake: FakeVolumeKernel, **options: Any) -> dict[str, dict[str, Any]]:
    by_guid, error = volumes.read_volumes(cast("api.WinLibrary", fake), last_error=fake.last_error, **options)
    assert by_guid is not None, error
    return by_guid


def _section(fake: FakeVolumeKernel) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    section = read_volumes_section(cast("api.WinLibrary", fake), last_error=fake.last_error)
    return section.volumes, section.windows_volume


def _usage(fake: FakeVolumeKernel, node: str) -> DiskUsage | None:
    by_ordinal, windows_ordinal = _section(fake)
    capture = WindowsCapture.model_validate(
        {
            "schema": 2,
            "platform": "win32",
            "hostname": "h",
            "kernel": "10.0",
            "pci": {},
            "disks": {"\\\\?\\p0": {"node": node}},
            "volumes": by_ordinal,
            "windows_volume": windows_ordinal,
        }
    )
    return resolve_usage(capture, Environment.BARE_METAL)[node]


VOLUME_C = "\\\\?\\Volume{c}\\"
#: The disk numbers the inventory lists, which an elevated read walks the volumes of.
INVENTORIED = frozenset({0, 1})
VOLUME_X = "\\\\?\\Volume{x}\\"


@pytest.mark.os_agnostic
def test_a_volume_records_its_paths_and_the_drive_type_of_the_first() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), disks=(1,))})
    entry = _read(fake)[VOLUME_C]
    assert entry["paths"] == ["C:\\"]
    assert entry["drive_type"] == DRIVE_FIXED
    assert entry["disks"] == [1]
    VolumeEntry.model_validate(entry)


@pytest.mark.os_agnostic
def test_a_secured_fixed_volume_that_refuses_to_open_still_names_its_drive_type() -> None:
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), openable=False)})
    entry = _read(fake)[VOLUME_X]
    assert entry["error"].startswith("could not open the volume")
    assert entry["drive_type"] == DRIVE_FIXED


@pytest.mark.os_agnostic
def test_a_path_list_longer_than_the_buffer_is_read_on_a_second_call_sized_as_asked() -> None:
    folders = tuple(f"C:\\mnt\\{'d' * 200}{index:03d}\\" for index in range(25))
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=folders)})
    entry = _read(fake)[VOLUME_C]
    assert entry["paths"] == list(folders)
    assert "paths_error" not in entry
    assert len(fake.path_calls) == 2
    assert fake.path_calls[1] > fake.path_calls[0]


@pytest.mark.os_agnostic
def test_a_path_query_that_fails_records_why_rather_than_no_paths() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=None)})
    entry = _read(fake)[VOLUME_C]
    assert entry["paths"] == []
    assert entry["paths_error"].startswith("could not read the volume's paths")
    assert "drive_type" not in entry


@pytest.mark.os_agnostic
def test_a_volume_with_no_path_asks_no_drive_type() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=())})
    entry = _read(fake)[VOLUME_C]
    assert entry["paths"] == []
    assert "drive_type" not in entry
    assert "paths_error" not in entry


@pytest.mark.os_agnostic
def test_the_volumes_section_carries_no_guid_path_and_keeps_the_windows_volume_join() -> None:
    fake = FakeVolumeKernel(
        {
            VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,)),
            VOLUME_X: FakeVolume(paths=("X:\\",), disks=(1,)),
        },
        windows_volume=VOLUME_C,
    )
    by_ordinal, windows_ordinal = _section(fake)
    recorded = json.dumps({"volumes": by_ordinal, "windows_volume": windows_ordinal})
    for guid_fragment in ("Volume{c}", "Volume{x}", "\\\\?\\"):
        assert guid_fragment not in recorded, guid_fragment
    assert by_ordinal is not None
    assert set(by_ordinal) == {"0", "1"}
    assert windows_ordinal is not None
    assert by_ordinal[windows_ordinal]["paths"] == ["C:\\"]


@pytest.mark.os_agnostic
def test_an_empty_volume_set_reads_as_an_empty_mapping() -> None:
    fake = FakeVolumeKernel({})
    by_ordinal, windows_ordinal = _section(fake)
    assert by_ordinal == {}
    assert windows_ordinal is None


@pytest.mark.os_agnostic
def test_a_volume_enumeration_refused_at_its_first_call_leaves_every_disk_undecided() -> None:
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), disks=(1,))}, first_error=ERROR_ACCESS_DENIED)
    assert _usage(fake, "PhysicalDrive1") is None
    assert _section(fake) == (None, None)


@pytest.mark.os_agnostic
def test_a_volume_enumeration_that_fails_part_way_leaves_every_disk_undecided() -> None:
    fake = FakeVolumeKernel(
        {
            VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,)),
            VOLUME_X: FakeVolume(paths=("X:\\",), disks=(1,)),
        },
        next_error=ERROR_NOT_READY,
    )
    assert _usage(fake, "PhysicalDrive1") is None
    assert _section(fake) == (None, None)


@pytest.mark.os_agnostic
def test_an_enumeration_that_ends_normally_still_decides_a_disk_on_no_volume() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,))})
    usage = _usage(fake, "PhysicalDrive1")
    assert usage is not None
    assert usage.uses == ()


@pytest.mark.os_agnostic
def test_a_windows_volume_not_among_the_enumerated_volumes_joins_to_none() -> None:
    fake = FakeVolumeKernel(
        {VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,))},
        windows_volume=VOLUME_X,
    )
    by_ordinal, windows_ordinal = _section(fake)
    assert by_ordinal is not None
    assert set(by_ordinal) == {"0"}
    assert windows_ordinal is None


@pytest.mark.os_agnostic
def test_the_boot_disk_is_still_found_through_the_ordinal_join() -> None:
    fake = FakeVolumeKernel(
        {VOLUME_C: FakeVolume(paths=("C:\\",), disks=(1,))},
        windows_volume=VOLUME_C,
    )
    usage = _usage(fake, "PhysicalDrive1")
    assert usage is not None
    assert usage.boot is True


@pytest.mark.os_agnostic
def test_an_efi_system_partition_marks_its_disk_boot_though_windows_lives_elsewhere() -> None:
    fake = FakeVolumeKernel(
        {
            VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,)),
            VOLUME_X: FakeVolume(paths=(), disks=(1,), esp=True),
        },
        windows_volume=VOLUME_C,
    )
    usage = _usage(fake, "PhysicalDrive1")
    assert usage is not None
    assert usage.boot is True


@pytest.mark.os_agnostic
def test_a_partition_of_another_type_does_not_mark_its_disk_boot() -> None:
    """The control: it is the ESP type that marks boot, not a partition answering at all."""
    fake = FakeVolumeKernel(
        {
            VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,)),
            VOLUME_X: FakeVolume(paths=(), disks=(1,), esp=False),
        },
        windows_volume=VOLUME_C,
    )
    usage = _usage(fake, "PhysicalDrive1")
    assert usage is not None
    assert usage.boot is False
    assert _read(fake)[VOLUME_X]["esp"] is False


@pytest.mark.os_agnostic
def test_a_volume_whose_extents_cannot_be_read_records_why_and_leaves_disks_undecided() -> None:
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), disks=None)})
    entry = _read(fake)[VOLUME_X]
    assert entry["disks"] == []
    assert entry["error"].startswith("could not read the volume's disk extents")
    assert _usage(fake, "PhysicalDrive1") is None


def _esp_beside_c(esp_volume: FakeVolume) -> FakeVolumeKernel:
    """Windows on disk 0 behind C:, and a second volume with no letter on disk 1."""
    return FakeVolumeKernel(
        {VOLUME_C: FakeVolume(paths=("C:\\",), disks=(0,)), VOLUME_X: esp_volume},
        windows_volume=VOLUME_C,
    )


@pytest.mark.os_agnostic
def test_an_esp_whose_disk_cannot_be_read_leaves_the_disks_undecided() -> None:
    """A known ESP on an unknown disk could be any disk's boot mark, letter or not."""
    fake = _esp_beside_c(FakeVolume(paths=(), disks=None, esp=True))
    entry = _read(fake)[VOLUME_X]
    assert entry["esp"] is True
    assert entry["error"].startswith("could not read the volume's disk extents")
    assert _usage(fake, "PhysicalDrive1") is None


@pytest.mark.os_agnostic
def test_a_letterless_volume_that_is_not_an_esp_still_does_not_block() -> None:
    """The control: a hidden recovery or reserved volume failing keeps "not mounted" decidable."""
    fake = _esp_beside_c(FakeVolume(paths=(), disks=None, esp=False))
    usage = _usage(fake, "PhysicalDrive1")
    assert usage is not None
    assert (usage.boot, usage.uses) == (False, ())


@pytest.mark.os_agnostic
def test_a_letterless_volume_that_refuses_to_open_still_does_not_block() -> None:
    """The control: a volume never opened carries no ESP reading, so it is not taken for one."""
    fake = _esp_beside_c(FakeVolume(paths=(), disks=None, openable=False))
    assert "esp" not in _read(fake)[VOLUME_X]
    usage = _usage(fake, "PhysicalDrive1")
    assert usage is not None
    assert (usage.boot, usage.uses) == (False, ())


def test_a_volume_with_a_path_carries_its_space_type_and_read_only_flag() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), flags=api.FILE_READ_ONLY_VOLUME)})
    entry = _read(fake)[VOLUME_C]
    assert (entry["total_bytes"], entry["free_bytes"], entry["available_bytes"]) == (100, 40, 30)
    assert entry["fs_type"] == "NTFS"
    assert entry["read_only"] is True
    VolumeEntry.model_validate(entry)


def test_a_volume_without_the_read_only_flag_is_read_write_whatever_other_flags_it_has() -> None:
    """The control: another flag set must not read as read-only (0x00040000 is FILE_NAMED_STREAMS)."""
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), flags=0x00040000)})
    assert _read(fake)[VOLUME_C]["read_only"] is False


def test_a_refused_space_call_is_recorded_with_its_error() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("D:\\",), space=None)}, error=999)
    entry = _read(fake)[VOLUME_C]
    # The injected getter answers what the fake's failing call left (21); a
    # read of the real api.last_error could not see the fake at all.
    assert entry["space_error"] == "could not read the volume's free space (error 21)"
    assert "total_bytes" not in entry
    assert entry["fs_type"] == "NTFS"
    assert "fs_info_error" not in entry
    VolumeEntry.model_validate(entry)


def test_a_refused_filesystem_query_is_recorded_and_keeps_the_space_figures() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("D:\\",), fs_type=None)})
    entry = _read(fake)[VOLUME_C]
    assert entry["fs_info_error"] == "could not read the volume's filesystem name (error 5)"
    assert (entry["total_bytes"], entry["free_bytes"], entry["available_bytes"]) == (100, 40, 30)
    assert "fs_type" not in entry
    assert "read_only" not in entry
    assert "space_error" not in entry
    VolumeEntry.model_validate(entry)


def test_a_volume_without_a_path_is_never_asked_for_its_space() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=())})
    entry = _read(fake)[VOLUME_C]
    for key in ("space_error", "fs_info_error", "total_bytes", "fs_type", "read_only"):
        assert key not in entry


def test_the_space_of_a_volume_that_refuses_to_open_is_still_read() -> None:
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), openable=False)})
    entry = _read(fake)[VOLUME_X]
    assert entry["error"].startswith("could not open the volume")
    assert entry["total_bytes"] == 100


def test_a_capture_taken_before_the_space_fields_existed_still_validates() -> None:
    entry = VolumeEntry.model_validate({"paths": ["C:\\"], "drive_type": DRIVE_FIXED})
    assert entry.total_bytes is None
    assert entry.fs_type is None
    assert entry.read_only is None
    assert entry.space_error is None
    assert entry.fs_info_error is None


@pytest.mark.parametrize("field", ["total_bytes", "free_bytes", "available_bytes"])
def test_a_space_figure_past_what_windows_can_answer_is_refused(field: str) -> None:
    VolumeEntry.model_validate({field: 2**64 - 1})
    with pytest.raises(ValueError, match="less than or equal to 18446744073709551615"):
        VolumeEntry.model_validate({field: 2**64})
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        VolumeEntry.model_validate({field: -1})


def test_an_elevated_run_walks_each_volume_bitmap_into_free_extents() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), bitmap=[1, 0, 0, 1, 0, 1, 1, 1])})
    entry = _read(fake, walk_on=INVENTORIED)[VOLUME_C]
    assert (entry["free_extents"]["count"], entry["free_extents"]["total"]) == (2, 3 * 4096)
    VolumeEntry.model_validate(entry)


def test_an_unelevated_run_never_opens_a_volume_for_reading() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), bitmap=[0] * 8)})
    entry = _read(fake)[VOLUME_C]
    assert "free_extents" not in entry
    assert fake.opened  # control: the volume was opened, just never for reading
    assert all(access != api.GENERIC_READ for _path, access in fake.opened)


def test_a_volume_that_refuses_a_read_open_records_the_error() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), bitmap=None)})
    assert "error 5" in _read(fake, walk_on=INVENTORIED)[VOLUME_C]["free_extents"]["error"]


def test_a_bitmap_larger_than_one_call_is_walked_in_chunks() -> None:
    # 24 runs of 3, then a run of 3 + 7 = 10 that is still open when the bitmap ends.
    bits = [1, 0, 0, 0] * 25 + [0] * 7
    whole = _read(FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), bitmap=bits)}), walk_on=INVENTORIED)
    chunked_fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), bitmap=bits)})
    chunked = _read(chunked_fake, walk_on=INVENTORIED, chunk_bytes=1)  # 8 clusters per call
    assert chunked_fake.bitmap_calls > 1
    assert chunked[VOLUME_C]["free_extents"] == whole[VOLUME_C]["free_extents"]
    assert (whole[VOLUME_C]["free_extents"]["count"], whole[VOLUME_C]["free_extents"]["largest"]) == (25, 10 * 4096)


DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


@pytest.mark.parametrize(
    "volume",
    [
        FakeVolume(paths=("E:\\",), drive_type=DRIVE_CDROM, disks=None, bitmap=[0] * 8),
        FakeVolume(paths=("R:\\",), drive_type=DRIVE_RAMDISK, disks=(0,), bitmap=[0] * 8),
        FakeVolume(paths=("X:\\",), disks=(), bitmap=[0] * 8),
        FakeVolume(paths=("Y:\\",), disks=None, bitmap=[0] * 8),
    ],
    ids=["optical-no-extents", "ram-disk", "no-disk", "extents-refused"],
)
def test_an_elevated_run_never_walks_a_volume_that_cannot_be_a_row(volume: FakeVolume) -> None:
    """A volume no inventoried disk can carry is dropped by the builder, so it is never opened for reading."""
    fake = FakeVolumeKernel({VOLUME_X: volume})
    entry = _read(fake, walk_on=INVENTORIED)[VOLUME_X]
    assert "free_extents" not in entry
    assert fake.opened  # control: the volume was opened, just never for reading
    assert all(access != api.GENERIC_READ for _path, access in fake.opened)
    assert fake.bitmap_calls == 0


def test_an_elevated_run_never_walks_a_pathless_esp_whose_extents_reach_a_disk() -> None:
    """An EFI system partition has disk extents and no path: nothing names a row, so it is never opened to read.

    Its extents reach disk 0 and it is a fixed volume, so the path is the only
    thing keeping it out of the walk, which would otherwise ask the cluster
    size of a first path it does not have.
    """
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=(), disks=(0,), esp=True, bitmap=[0] * 8)})
    entry = _read(fake, walk_on=INVENTORIED)[VOLUME_X]
    assert (entry["disks"], entry["esp"]) == ([0], True)  # control: the extents and the partition were read
    assert "free_extents" not in entry
    assert all(access != api.GENERIC_READ for _path, access in fake.opened)
    assert fake.bitmap_calls == 0


def test_an_elevated_run_walks_a_fixed_volume_on_a_disk_after_reading_its_extents() -> None:
    """The control: the same fake, a volume that will be a row, is walked."""
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), disks=(0,), bitmap=[0] * 8)})
    entry = _read(fake, walk_on=INVENTORIED)[VOLUME_X]
    assert entry["free_extents"]["count"] == 1
    assert [access for _path, access in fake.opened] == [0, api.GENERIC_READ]


#: 24 runs of 3 free clusters between used ones, then 7 free to the end: 107 clusters.
_RUNS_BITMAP = [1, 0, 0, 0] * 25 + [0] * 7


def _walked(volume: FakeVolume, *, chunk_bytes: int = 3) -> tuple[dict[str, Any], FakeVolumeKernel]:
    """Walk one fixed volume on disk 0 with ``chunk_bytes`` of cluster bits per call."""
    fake = FakeVolumeKernel({VOLUME_C: volume})
    entry = _read(fake, walk_on=INVENTORIED, chunk_bytes=chunk_bytes)[VOLUME_C]
    return entry, fake


def test_a_starting_cluster_rounded_down_by_the_filesystem_is_walked_on_from_where_it_was_asked() -> None:
    """FSCTL_GET_VOLUME_BITMAP may round the starting LCN down; the bits before it were already folded."""
    whole, _ = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP), chunk_bytes=64)
    rounded, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_rounding=16))
    assert rounded["free_extents"] == whole["free_extents"]
    assert fake.bitmap_calls > 2  # control: the walk restarted off a rounded cluster more than once
    assert (whole["free_extents"]["count"], whole["free_extents"]["largest"]) == (25, 10 * 4096)


def test_a_bitmap_answer_starting_past_the_cluster_asked_for_is_refused() -> None:
    """Rounding goes down only: an answer that skips clusters would fold a bitmap with a hole in it."""
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_lcn_ahead=8))
    assert entry["free_extents"] == {"error": "the volume bitmap restarted at cluster 8, past 0"}
    assert fake.bitmap_calls == 1


def _bitmap_handles(fake: FakeVolumeKernel) -> list[int]:
    """The handles the walk opened for reading, in the order they were opened."""
    return [handle for handle, access in fake.granted if access == api.GENERIC_READ]


def test_a_bitmap_answer_that_does_not_advance_is_refused_rather_than_asked_again() -> None:
    """ERROR_MORE_DATA with no cluster bits would ask the same question forever; the fake stops at a limit."""
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_stalls=True))
    assert entry["free_extents"] == {"error": "the volume bitmap did not advance"}
    assert fake.bitmap_calls == 1


def test_a_rounded_down_answer_that_ends_where_the_walk_already_was_is_refused() -> None:
    """An answer carrying cluster bits can still fail to advance, when its start was rounded down far enough.

    Asked from cluster 24 with a rounding of 32, the filesystem answers from 0
    with 24 bits: the run ends exactly where the walk already stood. The answer
    holds bits, so only the end of the run against the cluster asked for can
    tell it did not move; asking again would get the same answer forever.
    """
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_rounding=32), chunk_bytes=3)
    assert entry["free_extents"] == {"error": "the volume bitmap did not advance"}
    assert fake.bitmap_calls == 2


def test_a_failing_bitmap_call_is_refused_with_its_error() -> None:
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_error=ERROR_ACCESS_DENIED))
    assert entry["free_extents"] == {"error": "could not read the volume bitmap (error 5)"}
    assert fake.bitmap_calls == 1


def test_a_cluster_size_call_that_fails_is_refused_with_its_error_before_the_volume_is_opened() -> None:
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, cluster_error=ERROR_NOT_READY))
    assert entry["free_extents"] == {"error": f"could not read the cluster size (error {ERROR_NOT_READY})"}
    assert _bitmap_handles(fake) == []


def test_a_bitmap_answer_that_ends_before_the_clusters_its_header_promised_is_refused() -> None:
    """Success with half the bits left would fold half a volume and call it the whole one."""
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_short=True), chunk_bytes=64)
    # 53 bits arrive as 7 whole bytes, so the answer carries 56 clusters' worth.
    assert entry["free_extents"] == {"error": "the volume bitmap ended after 56 of 107 clusters"}
    assert fake.bitmap_calls == 1


def test_a_bitmap_answer_shorter_than_its_own_header_is_refused() -> None:
    """A header the call did not return is whatever the buffer held, which reads as an empty volume."""
    entry, _ = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_returned=8), chunk_bytes=64)
    assert entry["free_extents"] == {"error": "the volume bitmap answer is 8 bytes, shorter than its 16-byte header"}


def test_a_bitmap_whose_volume_grows_between_answers_is_refused_rather_than_walked_on() -> None:
    """Every answer must describe the volume the first one did; one that keeps growing would be walked forever."""
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_grows=8))
    assert entry["free_extents"] == {"error": "the volume bitmap changed from 107 to 115 clusters mid-walk"}
    assert fake.bitmap_calls == 2


def test_a_walk_over_answers_that_do_describe_one_volume_is_not_refused() -> None:
    """The control for the four refusals above: the same bitmap, answered plainly, chunk by chunk."""
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP))
    assert "error" not in entry["free_extents"]
    assert fake.bitmap_calls > 2
    assert (entry["free_extents"]["count"], entry["free_extents"]["largest"]) == (25, 10 * 4096)


def test_a_volume_with_more_free_runs_than_the_cap_is_refused_rather_than_failing_the_run() -> None:
    """The tally refuses past its cap by raising; the walk must turn that into this volume's refusal."""
    bitmap = [0, 1] * (MAX_FREE_EXTENTS + 1)
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=bitmap), chunk_bytes=1 << 20)
    refusal = f"more than {MAX_FREE_EXTENTS:,} free extents; stopped counting at the cap"
    assert entry["free_extents"] == {"error": refusal}
    assert _bitmap_handles(fake)[0] in fake.closed


def test_a_cluster_size_of_zero_is_refused_before_the_volume_is_opened_for_reading() -> None:
    entry, fake = _walked(FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, cluster_bytes=0))
    assert entry["free_extents"] == {"error": "the volume reported a cluster size of zero"}
    assert _bitmap_handles(fake) == []


@pytest.mark.parametrize(
    "volume",
    [
        FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP),
        FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_error=ERROR_ACCESS_DENIED),
        FakeVolume(paths=("C:\\",), bitmap=_RUNS_BITMAP, bitmap_stalls=True),
    ],
    ids=["walked", "refused", "stalled"],
)
def test_the_handle_opened_for_the_bitmap_is_closed_however_the_walk_ends(volume: FakeVolume) -> None:
    _, fake = _walked(volume)
    opened = _bitmap_handles(fake)
    assert len(opened) == 1  # control: the walk did open the volume for reading
    assert opened[0] in fake.closed


def _identity_handles(fake: FakeVolumeKernel) -> list[int]:
    """The handles opened to read a volume's identity, which asks for no access at all."""
    return [handle for handle, access in fake.granted if access == 0]


@pytest.mark.parametrize(
    "volume",
    [
        FakeVolume(paths=("C:\\",), disks=(0,), esp=False),
        FakeVolume(paths=("C:\\",), disks=None, esp=False),
        FakeVolume(paths=("C:\\",), disks=(0,), esp=None),
        FakeVolume(paths=(), disks=None, esp=None),
    ],
    ids=["answered", "extents-refused", "partition-refused", "both-refused"],
)
def test_the_handle_opened_for_a_volumes_identity_is_closed_however_its_ioctls_end(volume: FakeVolume) -> None:
    fake = FakeVolumeKernel({VOLUME_C: volume})
    _read(fake)
    opened = _identity_handles(fake)
    assert len(opened) == 1  # control: the volume was opened for its identity
    assert opened[0] in fake.closed


def test_an_identity_ioctl_that_raises_still_closes_the_volume_and_the_enumeration() -> None:
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), ioctl_raises=True)})
    with pytest.raises(OSError, match=str(api.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS)):
        volumes.read_volumes(cast("api.WinLibrary", fake), last_error=fake.last_error)
    opened = _identity_handles(fake)
    assert len(opened) == 1
    assert opened[0] in fake.closed
    assert fake.find_closed == 1


@pytest.mark.parametrize(
    "next_error",
    [None, ERROR_NOT_READY],
    ids=["ended-normally", "failed-part-way"],
)
def test_the_volume_enumeration_is_closed_however_it_ends(next_error: int | None) -> None:
    fake = FakeVolumeKernel(
        {VOLUME_C: FakeVolume(paths=("C:\\",)), VOLUME_X: FakeVolume(paths=("X:\\",))}, next_error=next_error
    )
    volumes.read_volumes(cast("api.WinLibrary", fake), last_error=fake.last_error)
    assert fake.find_closed == 1


def test_an_enumeration_that_never_started_closes_no_handle() -> None:
    """The control: a refused first call returns no handle, so there is nothing to close."""
    fake = FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",))}, first_error=ERROR_ACCESS_DENIED)
    assert volumes.read_volumes(cast("api.WinLibrary", fake), last_error=fake.last_error).volumes is None
    assert fake.find_closed == 0


#: A volume Windows lists by a folder mount first and its drive letter second.
_FOLDER_FIRST = ("C:\\mnt\\x\\", "X:\\")


def test_every_call_that_takes_a_root_is_asked_of_the_drive_letter_whatever_windows_listed_first() -> None:
    """The row is named by the letter, so the figures beside it are read from the letter too."""
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=_FOLDER_FIRST, disks=(0,), bitmap=[0] * 8)})
    entry = _read(fake, walk_on=INVENTORIED)[VOLUME_X]
    # GetDriveTypeW, GetDiskFreeSpaceExW, GetVolumeInformationW and GetDiskFreeSpaceW.
    assert fake.asked_roots == ["X:\\"] * 4
    assert entry["paths"] == ["X:\\", "C:\\mnt\\x\\"]


def test_the_used_by_cell_names_a_folder_first_volume_by_its_letter_like_its_row() -> None:
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=_FOLDER_FIRST, disks=(0,))})
    usage = _usage(fake, "PhysicalDrive0")
    assert usage is not None
    assert [use.mounts for use in usage.uses] == [("X:\\", "C:\\mnt\\x\\")]


def test_a_volume_listed_letter_first_is_asked_in_the_same_order() -> None:
    """The control: the order moves a letter forward and changes nothing for a volume already in it."""
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\", "C:\\mnt\\x\\"), disks=(0,))})
    entry = _read(fake)[VOLUME_X]
    assert fake.asked_roots == ["X:\\"] * 3
    assert entry["paths"] == ["X:\\", "C:\\mnt\\x\\"]


def test_an_elevated_read_never_walks_a_volume_on_a_disk_the_inventory_does_not_list() -> None:
    """Disk 7 is not inventoried, so the builder makes no row of X:, and the walk must not read it for one."""
    fake = FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), disks=(7,), bitmap=[0] * 8)})
    entry = _read(fake, walk_on=INVENTORIED)[VOLUME_X]
    assert entry["disks"] == [7]  # control: the extents were read
    assert "free_extents" not in entry
    assert fake.bitmap_calls == 0


def _row_or_not() -> dict[str, FakeVolume]:
    """A volume of every shape the row rule tells apart, on disk 0 unless it says otherwise."""
    return {
        "\\\\?\\Volume{row}\\": FakeVolume(paths=("C:\\",), disks=(0,), bitmap=[0] * 8),
        "\\\\?\\Volume{ram}\\": FakeVolume(paths=("R:\\",), drive_type=DRIVE_RAMDISK, disks=(0,), bitmap=[0] * 8),
        "\\\\?\\Volume{cd}\\": FakeVolume(paths=("E:\\",), drive_type=DRIVE_CDROM, disks=(0,), bitmap=[0] * 8),
        "\\\\?\\Volume{elsewhere}\\": FakeVolume(paths=("Y:\\",), disks=(7,), bitmap=[0] * 8),
        "\\\\?\\Volume{esp}\\": FakeVolume(paths=(), disks=(0,), esp=True, bitmap=[0] * 8),
        "\\\\?\\Volume{spanned}\\": FakeVolume(paths=("S:\\",), disks=(7, 0), bitmap=[0] * 8),
    }


def test_the_reader_walks_exactly_the_volumes_the_builder_makes_rows_of() -> None:
    """One rule decides both: a walked volume is a row, and a row's volume was walked."""
    fake = FakeVolumeKernel(_row_or_not())
    by_ordinal = read_volumes_section(
        cast("api.WinLibrary", fake), last_error=fake.last_error, walk_on=frozenset({0})
    ).volumes
    assert by_ordinal is not None
    capture = WindowsCapture.model_validate(
        {
            "schema": 2,
            "platform": "win32",
            "hostname": "h",
            "kernel": "10.0",
            "pci": {},
            "disks": {"\\\\?\\p0": {"node": "PhysicalDrive0"}},
            "volumes": by_ordinal,
            "volume_bitmaps_read": True,
        }
    )
    rows, _ = build_filesystems(capture, Environment.BARE_METAL)
    assert rows is not None
    walked = {entry["paths"][0] for entry in by_ordinal.values() if "free_extents" in entry}
    assert walked == {row.name for row in rows} == {"C:\\", "S:\\"}
    assert all(row.free_space is not None for row in rows)


def test_a_path_query_that_fails_names_the_error_windows_gave() -> None:
    # The injected getter answers what the fake's failing call left (87); a
    # read of the real api.last_error answers 0 off Windows.
    entry = _read(FakeVolumeKernel({VOLUME_C: FakeVolume(paths=None)}))[VOLUME_C]
    assert entry["paths_error"] == f"could not read the volume's paths (error {ERROR_INVALID_PARAMETER})"


def test_an_extents_ioctl_that_fails_names_the_error_windows_gave() -> None:
    entry = _read(FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), disks=None)}))[VOLUME_X]
    assert entry["error"] == f"could not read the volume's disk extents (error {ERROR_INVALID_FUNCTION})"


def test_a_volume_that_refuses_to_open_names_the_error_windows_gave() -> None:
    entry = _read(FakeVolumeKernel({VOLUME_X: FakeVolume(paths=("X:\\",), openable=False)}))[VOLUME_X]
    assert entry["error"] == f"could not open the volume (error {ERROR_SHARING_VIOLATION})"
