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

from lsdsk.adapters.hw.windows import volumes
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.capture import VolumeEntry, WindowsCapture
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
    """

    paths: tuple[str, ...] | None = ()
    drive_type: int = DRIVE_FIXED
    disks: tuple[int, ...] | None = (0,)
    openable: bool = True
    esp: bool | None = None


@dataclass
class FakeVolumeKernel:
    """``kernel32`` as the volume reader sees it."""

    volumes: dict[str, FakeVolume]
    windows_volume: str | None = None
    first_error: int | None = None
    next_error: int | None = None
    error: int = 0
    path_calls: list[int] = field(default_factory=list[int])
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
        return 1

    def GetVolumePathNamesForVolumeNameW(  # noqa: N802 - the Win32 entry point's own name
        self, volume: str, buffer: ctypes.Array[ctypes.c_wchar], size: int, needed: object
    ) -> int:
        self.path_calls.append(size)
        paths = self.volumes[volume].paths
        if paths is None:
            return 0
        multi = "".join(f"{path}\x00" for path in paths) + "\x00"
        getattr(needed, "_obj").value = len(multi)  # noqa: B009 - a CArgObject's referent
        if len(multi) > size:
            return 0
        for index, char in enumerate(multi):
            buffer[index] = char
        return 1

    def GetDriveTypeW(self, root: str) -> int:  # noqa: N802 - the Win32 entry point's own name
        return next(volume.drive_type for volume in self.volumes.values() if volume.paths and volume.paths[0] == root)

    def CreateFileW(  # noqa: N802 - the Win32 entry point's own name
        self, path: str, access: int, share: int, security: object, disposition: int, flags: int, template: object
    ) -> int:
        del access, share, security, disposition, flags, template
        name = path + "\\"
        if not self.volumes[name].openable:
            return cast("int", api.INVALID_HANDLE_VALUE)
        handle = 100 + len(self._handles)
        self._handles[handle] = name
        return handle

    def CloseHandle(self, handle: int) -> int:  # noqa: N802 - the Win32 entry point's own name
        del handle
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
        del in_buffer, in_size, overlapped
        volume = self.volumes[self._handles[handle]]
        if code == api.IOCTL_DISK_GET_PARTITION_INFO_EX:
            answer = _gpt_partition(ESP_TYPE if volume.esp else BASIC_DATA_TYPE) if volume.esp is not None else None
        elif code == api.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS and volume.disks is not None:
            disks = volume.disks
            answer = struct.pack("<I4x", len(disks)) + b"".join(struct.pack("<I4xqq", d, 0, 1 << 30) for d in disks)
        else:
            answer = None
        if answer is None:
            return 0
        assert len(answer) <= out_size
        ctypes.memmove(out_buffer, answer, len(answer))
        getattr(returned, "_obj").value = len(answer)  # noqa: B009 - a CArgObject's referent
        return 1


def _read(fake: FakeVolumeKernel) -> dict[str, dict[str, Any]]:
    by_guid = volumes.read_volumes(cast("api.WinLibrary", fake), last_error=fake.last_error)
    assert by_guid is not None
    return by_guid


def _section(fake: FakeVolumeKernel) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    return read_volumes_section(cast("api.WinLibrary", fake), last_error=fake.last_error)


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
