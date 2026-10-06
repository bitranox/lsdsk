"""What a Windows disk is used for, resolved from hand-built captures."""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.windows.capture import WindowsCapture
from lsdsk.adapters.hw.windows.usage import resolve_usage
from lsdsk.domain.enums import Environment, UseKind
from lsdsk.domain.models import DiskUse

BARE_METAL = Environment.BARE_METAL
CONTAINER = Environment.CONTAINER


def _disk(node: str) -> dict[str, Any]:
    return {"node": node}


def _volume(*, paths: tuple[str, ...] = (), disks: tuple[int, ...] = (), **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"paths": list(paths), "disks": list(disks)}
    entry.update(extra)
    return entry


def _capture(disks: dict[str, Any], **extra: Any) -> WindowsCapture:
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "win32",
        "hostname": "h",
        "kernel": "10.0",
        "pci": {},
        "disks": disks,
    }
    reading.update(extra)
    return WindowsCapture.model_validate(reading)


@pytest.mark.os_agnostic
def test_the_windows_volume_marks_its_disk_boot_and_names_the_letter() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive1")},
        volumes={"\\\\?\\Volume{c}\\": _volume(paths=("C:\\",), disks=(1,))},
        windows_volume="\\\\?\\Volume{c}\\",
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive1"] is not None
    assert usage["PhysicalDrive1"].boot is True
    assert usage["PhysicalDrive1"].uses == (DiskUse(kind=UseKind.LETTER, mounts=("C:\\",)),)


@pytest.mark.os_agnostic
def test_the_esp_marks_its_own_disk_boot_with_no_letter_while_c_marks_another() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0"), "\\\\?\\p1": _disk("PhysicalDrive1")},
        volumes={
            "\\\\?\\Volume{esp}\\": _volume(disks=(0,), esp=True),
            "\\\\?\\Volume{c}\\": _volume(paths=("C:\\",), disks=(1,)),
        },
        windows_volume="\\\\?\\Volume{c}\\",
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive0"] is not None
    assert usage["PhysicalDrive0"].boot is True
    assert usage["PhysicalDrive0"].uses == ()
    assert usage["PhysicalDrive1"] is not None
    assert usage["PhysicalDrive1"].boot is True


@pytest.mark.os_agnostic
def test_a_letter_and_a_folder_mount_on_the_same_disk_merge_into_one_use() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0")},
        volumes={
            "\\\\?\\Volume{d}\\": _volume(paths=("D:\\",), disks=(0,)),
            "\\\\?\\Volume{mnt}\\": _volume(paths=("C:\\mnt\\x\\",), disks=(0,)),
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive0"] is not None
    assert usage["PhysicalDrive0"].uses == (DiskUse(kind=UseKind.LETTER, mounts=("D:\\", "C:\\mnt\\x\\")),)


@pytest.mark.os_agnostic
def test_a_spanned_volume_marks_both_of_its_disks() -> None:
    capture = _capture(
        disks={"\\\\?\\p2": _disk("PhysicalDrive2"), "\\\\?\\p3": _disk("PhysicalDrive3")},
        volumes={"\\\\?\\Volume{e}\\": _volume(paths=("E:\\",), disks=(2, 3))},
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive2"] is not None
    assert usage["PhysicalDrive2"].uses == (DiskUse(kind=UseKind.LETTER, mounts=("E:\\",)),)
    assert usage["PhysicalDrive3"] is not None
    assert usage["PhysicalDrive3"].uses == (DiskUse(kind=UseKind.LETTER, mounts=("E:\\",)),)


#: What the reader records for a volume whose extents ioctl failed: no disks,
#: and the reason. GetDriveTypeW's answers, as winapi names them.
_EXTENTS_FAILED = "could not read the volume's disk extents (error 1)"
DRIVE_FIXED = 3
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


@pytest.mark.os_agnostic
@pytest.mark.parametrize("drive_type", [DRIVE_CDROM, DRIVE_RAMDISK], ids=["cdrom", "ramdisk"])
def test_a_volume_that_names_no_disk_reaches_no_disk(drive_type: int) -> None:
    # A RAM disk or an optical drive has no disk extents to report, so its
    # failure says nothing about any physical disk and blocks nothing.
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0")},
        volumes={
            "\\\\?\\Volume{x}\\": _volume(paths=("X:\\",), disks=(), error=_EXTENTS_FAILED, drive_type=drive_type)
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive0"] is not None
    assert usage["PhysicalDrive0"].uses == ()
    assert usage["PhysicalDrive0"].boot is False


@pytest.mark.os_agnostic
def test_a_failing_volume_with_no_path_at_all_blocks_nothing() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0")},
        volumes={"\\\\?\\Volume{x}\\": _volume(error="could not open the volume (error 5)")},
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive0"] is not None
    assert usage["PhysicalDrive0"].uses == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("drive_type", [DRIVE_FIXED, None], ids=["fixed", "type-unread"])
def test_a_failing_fixed_volume_with_a_path_still_blocks_not_mounted(drive_type: int | None) -> None:
    # The measured case: a separately secured fixed volume refuses to open even
    # elevated, and it could sit on any disk.
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0")},
        volumes={
            "\\\\?\\Volume{x}\\": _volume(
                paths=("X:\\",), error="could not open the volume (error 5)", drive_type=drive_type
            )
        },
    )
    assert resolve_usage(capture, BARE_METAL) == {"PhysicalDrive0": None}


@pytest.mark.os_agnostic
def test_a_volume_whose_paths_were_not_read_blocks_not_mounted() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0")},
        volumes={"\\\\?\\Volume{x}\\": _volume(disks=(0,), paths_error="could not read the volume's paths (error 1)")},
    )
    assert resolve_usage(capture, BARE_METAL) == {"PhysicalDrive0": None}


@pytest.mark.os_agnostic
def test_a_volume_that_could_not_open_leaves_an_unresolved_disk_undecided() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0"), "\\\\?\\p1": _disk("PhysicalDrive1")},
        volumes={
            "\\\\?\\Volume{bad}\\": _volume(
                paths=("X:\\",), error="could not open the volume (error 5)", drive_type=DRIVE_FIXED
            ),
            "\\\\?\\Volume{c}\\": _volume(paths=("C:\\",), disks=(1,)),
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive0"] is None
    assert usage["PhysicalDrive1"] is not None
    assert usage["PhysicalDrive1"].uses == (DiskUse(kind=UseKind.LETTER, mounts=("C:\\",)),)


@pytest.mark.os_agnostic
def test_no_volumes_section_leaves_every_disk_undecided() -> None:
    capture = _capture(disks={"\\\\?\\p0": _disk("PhysicalDrive0")})
    usage = resolve_usage(capture, BARE_METAL)
    assert usage == {"PhysicalDrive0": None}


@pytest.mark.os_agnostic
def test_a_disk_with_no_volume_and_no_error_anywhere_is_not_mounted() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0"), "\\\\?\\p1": _disk("PhysicalDrive1")},
        volumes={"\\\\?\\Volume{c}\\": _volume(paths=("C:\\",), disks=(1,))},
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["PhysicalDrive0"] is not None
    assert usage["PhysicalDrive0"].boot is False
    assert usage["PhysicalDrive0"].uses == ()


@pytest.mark.os_agnostic
def test_a_container_resolves_every_disk_to_none() -> None:
    capture = _capture(
        disks={"\\\\?\\p0": _disk("PhysicalDrive0")},
        volumes={"\\\\?\\Volume{c}\\": _volume(paths=("C:\\",), disks=(0,))},
    )
    usage = resolve_usage(capture, CONTAINER)
    assert usage == {"PhysicalDrive0": None}
