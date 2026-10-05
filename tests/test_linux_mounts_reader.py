"""The Linux sources that say what uses a disk, read from fake trees."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lsdsk.adapters.hw.linux import mounts
from lsdsk.adapters.hw.linux.capture import LinuxCapture

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

MOUNTINFO = (
    "22 1 0:28 / / rw,relatime shared:1 - zfs rpool/ROOT/pve-1 rw,xattr\n"
    "30 22 8:2 / /boot\\040efi rw - vfat /dev/sda2 rw\n"
    "31 22 0:40 / /srv rw - btrfs /dev/sdb1 rw\n"
)


@pytest.mark.os_agnostic
def test_mountinfo_rows_keep_device_mountpoint_type_and_source(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(MOUNTINFO, encoding="utf-8")
    rows = mounts.read_mounts(path)
    assert rows is not None
    assert rows[0] == {"dev": "0:28", "mountpoint": "/", "fstype": "zfs", "source": "rpool/ROOT/pve-1"}
    # The kernel octal-escapes a space; the reader hands back the real path.
    assert rows[1]["mountpoint"] == "/boot efi"


@pytest.mark.os_agnostic
def test_an_unreadable_mountinfo_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    assert mounts.read_mounts(tmp_path / "absent") is None


@pytest.mark.os_agnostic
def test_swap_rows_skip_the_header(tmp_path: Path) -> None:
    path = tmp_path / "swaps"
    path.write_text(
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/nvme4n1p1   partition\t8388604\t\t0\t\t-2\n",
        encoding="utf-8",
    )
    assert [row["path"] for row in mounts.read_swaps(path)] == ["/dev/nvme4n1p1"]


@pytest.mark.os_agnostic
def test_partitions_carry_their_device_number_and_holders(tmp_path: Path) -> None:
    disk = tmp_path / "sda"
    part = disk / "sda2"
    (part / "holders" / "dm-0").mkdir(parents=True)
    (part / "partition").write_text("2\n", encoding="utf-8")
    (part / "dev").write_text("8:2\n", encoding="utf-8")
    (disk / "queue").mkdir()  # a non-partition child must not be listed
    assert mounts.read_partitions(disk) == {"sda2": {"dev": "8:2", "holders": ["dm-0"]}}


@pytest.mark.os_agnostic
def test_a_stacked_device_names_its_mapping_and_what_sits_on_it(tmp_path: Path) -> None:
    dm = tmp_path / "dm-0"
    (dm / "dm").mkdir(parents=True)
    (dm / "holders" / "dm-1").mkdir(parents=True)
    (dm / "dev").write_text("253:0\n", encoding="utf-8")
    (dm / "dm" / "name").write_text("cryptroot\n", encoding="utf-8")
    (dm / "dm" / "uuid").write_text("CRYPT-LUKS2-abc-cryptroot\n", encoding="utf-8")
    found = mounts.read_stacked(["dm-0"], tmp_path)
    assert found == {
        "dm-0": {"dev": "253:0", "dm_name": "cryptroot", "dm_uuid": "CRYPT-LUKS2-abc-cryptroot", "holders": ["dm-1"]}
    }


@pytest.mark.os_agnostic
def test_udev_signatures_keep_only_the_filesystem_type_and_label(tmp_path: Path) -> None:
    (tmp_path / "b8:67").write_text(
        "S:disk/by-id/x\nE:ID_FS_TYPE=zfs_member\nE:ID_FS_LABEL=rpool\nE:ID_SERIAL=SECRET\n", encoding="utf-8"
    )
    assert mounts.read_signatures(["8:67", "8:1"], tmp_path) == {"8:67": {"fs_type": "zfs_member", "fs_label": "rpool"}}


@pytest.mark.os_agnostic
def test_an_absent_udev_database_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    assert mounts.read_signatures(["8:1"], tmp_path / "absent") is None


@pytest.mark.os_linux
def test_a_dev_source_is_resolved_to_its_device_number(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text("31 22 0:40 / /x rw - tmpfs /dev/null rw\n", encoding="utf-8")
    rows = mounts.read_mounts(path)
    assert rows is not None
    rdev = Path("/dev/null").stat().st_rdev
    assert rows[0]["source_dev"] == f"{os.major(rdev)}:{os.minor(rdev)}"


@pytest.mark.os_agnostic
def test_a_capture_accepts_a_minimal_reading_carrying_every_new_field() -> None:
    capture = LinuxCapture.model_validate(
        {
            "schema": 1,
            "platform": "linux",
            "hostname": "example",
            "kernel": "6.1.0",
            "pci": {},
            "mounts": [{"dev": "0:28", "mountpoint": "/", "fstype": "zfs", "source": "rpool/ROOT/pve-1"}],
            "swaps": [{"path": "/dev/nvme4n1p1", "dev": "259:1"}],
            "stacked": {"dm-0": {"dev": "253:0", "dm_name": "cryptroot", "holders": []}},
            "signatures": {"8:67": {"fs_type": "zfs_member", "fs_label": "rpool"}},
        }
    )
    assert capture.mounts is not None and capture.mounts[0].fstype == "zfs"
    assert capture.swaps[0].dev == "259:1"
    assert capture.stacked["dm-0"].dm_name == "cryptroot"
    assert capture.signatures is not None and capture.signatures["8:67"].fs_type == "zfs_member"


@pytest.mark.os_agnostic
def test_every_committed_linux_fixture_still_validates_with_the_new_fields_unread() -> None:
    fixtures = sorted(FIXTURES.glob("linux-*.json"))
    assert fixtures
    for fixture in fixtures:
        reading = json.loads(fixture.read_text(encoding="utf-8"))
        capture = LinuxCapture.model_validate(reading)
        assert capture.mounts is None, fixture
        assert capture.signatures is None, fixture
