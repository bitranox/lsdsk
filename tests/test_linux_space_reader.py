"""statvfs readings per mounted filesystem, through the reader's statvfs seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.linux.capture import LinuxCapture, SpaceEntry
from lsdsk.adapters.hw.linux.mounts import read_mounts, read_space
from lsdsk.adapters.hw.snapshot import SCHEMA_VERSION

if TYPE_CHECKING:
    from pathlib import Path

_MINIMAL: dict[str, object] = {
    "schema": SCHEMA_VERSION,
    "platform": "linux",
    "hostname": "example",
    "kernel": "6.1.0",
    "pci": dict[str, object](),
}


@dataclass(frozen=True)
class Stat:
    """The six statvfs figures, as a result the reader's seam returns."""

    f_frsize: int = 4096
    f_blocks: int = 1000
    f_bfree: int = 400
    f_bavail: int = 350
    f_files: int = 64
    f_ffree: int = 16


def test_one_reading_per_filesystem_from_its_first_mountpoint() -> None:
    asked: list[str] = []

    def statvfs(path: str) -> Stat:
        asked.append(path)
        return Stat()

    rows = [
        {"dev": "8:1", "mountpoint": "/", "fstype": "ext4"},
        {"dev": "8:1", "mountpoint": "/srv/bind", "fstype": "ext4"},
        {"dev": "8:2", "mountpoint": "/boot/efi", "fstype": "vfat"},
    ]
    space = read_space(rows, statvfs=statvfs)
    assert asked == ["/", "/boot/efi"]
    assert space["8:1"] == {"frsize": 4096, "blocks": 1000, "bfree": 400, "bavail": 350, "files": 64, "ffree": 16}


def test_a_zfs_dataset_is_never_stat_ed() -> None:
    def statvfs(path: str) -> Stat:
        raise AssertionError(path)

    assert read_space([{"dev": "0:51", "mountpoint": "/", "fstype": "zfs", "source": "rpool"}], statvfs=statvfs) == {}


def test_a_refused_statvfs_is_recorded_with_its_reason() -> None:
    def statvfs(_path: str) -> Stat:
        raise PermissionError(13, "Permission denied")

    assert read_space([{"dev": "8:3", "mountpoint": "/root/x", "fstype": "xfs"}], statvfs=statvfs) == {
        "8:3": {"error": "Permission denied"}
    }


def test_the_readings_reach_a_validated_capture(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("30 1 8:1 / / ro - ext4 /dev/lsdsk-test-absent1 rw\n", encoding="utf-8")
    rows = read_mounts(mountinfo, device_number=lambda _path: "8:1")
    assert rows is not None
    space = read_space(rows, statvfs=lambda _path: Stat())
    capture = LinuxCapture.model_validate({**_MINIMAL, "mounts": rows, "filesystem_space": space})
    assert capture.mounts is not None
    assert capture.mounts[0].read_only is True
    assert capture.filesystem_space is not None
    assert capture.filesystem_space["8:1"].bavail == 350
    assert capture.filesystem_space["8:1"].error is None


def test_a_refused_reading_validates_with_its_reason() -> None:
    entry = SpaceEntry.model_validate({"error": "Permission denied"})
    assert (entry.error, entry.blocks) == ("Permission denied", None)


@pytest.mark.parametrize(("value", "fragment"), [(-1, "greater than or equal to 0"), (2**64, "less than or equal to")])
def test_a_space_figure_outside_unsigned_64_bits_is_refused(value: int, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        SpaceEntry.model_validate({"blocks": value})
