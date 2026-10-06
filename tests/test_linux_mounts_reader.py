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
    # A ZFS source is narrowed to its pool: the dataset path below it is not
    # something the resolver reads.
    assert rows[0] == {"dev": "0:28", "mountpoint": "/", "fstype": "zfs", "source": "rpool"}
    # The kernel octal-escapes a space; the reader hands back the real path.
    assert rows[1]["mountpoint"] == "/boot efi"


@pytest.mark.os_agnostic
def test_an_unreadable_mountinfo_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    assert mounts.read_mounts(tmp_path / "absent") is None


@pytest.mark.os_agnostic
def test_a_mountinfo_over_the_limit_is_refused_like_an_unreadable_one(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(MOUNTINFO, encoding="utf-8")
    # RED proof the limit is actually consulted: a limit this file is well
    # under passes, the same tiny limit set one byte below the file's own
    # encoded size does not.
    assert mounts.read_mounts(path, limit=len(MOUNTINFO.encode("utf-8"))) is not None
    assert mounts.read_mounts(path, limit=len(MOUNTINFO.encode("utf-8")) - 1) is None


@pytest.mark.os_agnostic
def test_swap_rows_skip_the_header(tmp_path: Path) -> None:
    path = tmp_path / "swaps"
    path.write_text(
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/nvme4n1p1   partition\t8388604\t\t0\t\t-2\n",
        encoding="utf-8",
    )
    rows = mounts.read_swaps(path)
    assert rows is not None
    assert [row["path"] for row in rows] == ["/dev/nvme4n1p1"]


@pytest.mark.os_agnostic
def test_an_absent_swap_list_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    assert mounts.read_swaps(tmp_path / "absent") is None


@pytest.mark.os_agnostic
def test_an_unreadable_swap_list_is_not_read_rather_than_empty(tmp_path: Path) -> None:
    unreadable = tmp_path / "swaps"
    unreadable.mkdir()  # a directory cannot be read as the swap list
    assert mounts.read_swaps(unreadable) is None


@pytest.mark.os_agnostic
def test_a_swap_list_with_only_its_header_is_empty_not_unread(tmp_path: Path) -> None:
    path = tmp_path / "swaps"
    path.write_text("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n", encoding="utf-8")
    assert mounts.read_swaps(path) == []


@pytest.mark.os_agnostic
def test_a_swap_list_over_the_limit_is_refused_like_an_unreadable_one(tmp_path: Path) -> None:
    content = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/nvme4n1p1   partition\t8388604\t\t0\t\t-2\n"
    path = tmp_path / "swaps"
    path.write_text(content, encoding="utf-8")
    assert mounts.read_swaps(path, limit=len(content.encode("utf-8"))) not in ([], None)
    assert mounts.read_swaps(path, limit=len(content.encode("utf-8")) - 1) is None


@pytest.mark.os_agnostic
def test_read_partitions_on_an_absent_disk_directory_is_empty(tmp_path: Path) -> None:
    assert mounts.read_partitions(tmp_path / "absent") == {}


@pytest.mark.os_agnostic
def test_read_partitions_on_a_path_that_is_not_a_directory_is_empty(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "sda"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    assert mounts.read_partitions(not_a_dir) == {}


@pytest.mark.os_agnostic
def test_an_oversized_attribute_is_not_read_as_a_partition_s_device_number(tmp_path: Path) -> None:
    disk = tmp_path / "sda"
    part = disk / "sda2"
    part.mkdir(parents=True)
    (part / "partition").write_text("2\n", encoding="utf-8")
    # One character past MAX_ATTRIBUTE_CHARS: the attribute is refused as
    # unreadable, which read_partitions reflects by leaving "dev" out rather
    # than reporting a truncated value.
    (part / "dev").write_text("8" * (mounts.MAX_ATTRIBUTE_CHARS + 1), encoding="utf-8")
    assert mounts.read_partitions(disk) == {"sda2": {"holders": []}}


@pytest.mark.os_agnostic
def test_read_stacked_on_an_absent_root_is_empty(tmp_path: Path) -> None:
    assert mounts.read_stacked(["dm-0"], tmp_path / "absent") == {}


@pytest.mark.os_agnostic
def test_read_stacked_on_a_root_that_is_not_a_directory_is_empty(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "block"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    assert mounts.read_stacked(["dm-0"], not_a_dir) == {}


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
        "dm-0": {"dev": "253:0", "dm_name": "cryptroot", "dm_uuid": "CRYPT-", "holders": ["dm-1"], "partitions": {}}
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
    assert capture.swaps is not None and capture.swaps[0].dev == "259:1"
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


# What a desktop's mountinfo also carries: a network share, a FUSE mount in a
# home directory, a tmpfs, and a bind of a home directory onto a disk mount.
PRIVATE_MOUNTINFO = (
    "22 1 0:28 / / rw,relatime shared:1 - zfs rpool/ROOT/pve-1 rw,xattr\n"
    "40 22 0:60 / /mnt/share rw - nfs4 fileserver.example:/export/alice rw\n"
    "41 22 0:55 / /home/alice/remote rw - fuse.sshfs alice@fileserver.example:/export rw\n"
    "42 22 0:30 / /run/user/1000 rw - tmpfs tmpfs rw\n"
    "43 22 8:1 /home/alice/projects /srv/projects rw - ext4 /dev/sda1 rw\n"
)


@pytest.mark.os_agnostic
def test_mountinfo_keeps_no_identifier_the_resolver_does_not_read(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(PRIVATE_MOUNTINFO, encoding="utf-8")
    rows = mounts.read_mounts(path)
    assert rows is not None
    recorded = json.dumps(rows)
    for identifier in ("alice", "fileserver", "/export", "/run/user", "/mnt/share", "ROOT/pve-1"):
        assert identifier not in recorded, identifier
    # The block-backed rows survive, the ZFS one with its pool only.
    assert rows == [
        {"dev": "0:28", "mountpoint": "/", "fstype": "zfs", "source": "rpool"},
        {"dev": "8:1", "mountpoint": "/srv/projects", "fstype": "ext4"},
    ]


@pytest.mark.os_agnostic
def test_a_mapping_uuid_keeps_only_its_type_prefix(tmp_path: Path) -> None:
    dm = tmp_path / "dm-1"
    (dm / "dm").mkdir(parents=True)
    (dm / "dev").write_text("253:1\n", encoding="utf-8")
    (dm / "dm" / "name").write_text("vg0-root\n", encoding="utf-8")
    uuid = "LVM-Kq8d3J0sYb1fGvT2xWc5nR7mL9pA4hE6uZ0iO3tQ1yX8wV2bN5cM7kJ4gF6dS9aP"
    (dm / "dm" / "uuid").write_text(uuid + "\n", encoding="utf-8")
    found = mounts.read_stacked(["dm-1"], tmp_path)
    assert found["dm-1"]["dm_uuid"] == "LVM-"
    assert uuid[4:] not in json.dumps(found)


@pytest.mark.os_agnostic
def test_a_stacked_device_s_partitions_are_recorded_and_their_holders_followed(tmp_path: Path) -> None:
    md = tmp_path / "md126"
    part = md / "md126p2"
    (part / "holders" / "dm-0").mkdir(parents=True)
    (part / "partition").write_text("2\n", encoding="utf-8")
    (part / "dev").write_text("259:2\n", encoding="utf-8")
    (md / "dev").write_text("9:126\n", encoding="utf-8")
    (tmp_path / "dm-0").mkdir()
    (tmp_path / "dm-0" / "dev").write_text("253:0\n", encoding="utf-8")
    found = mounts.read_stacked(["md126"], tmp_path)
    assert found["md126"]["partitions"] == {"md126p2": {"dev": "259:2", "holders": ["dm-0"]}}
    assert found["dm-0"]["dev"] == "253:0"


@pytest.mark.os_linux
def test_a_disk_directory_that_cannot_be_listed_reads_its_partitions_as_not_read(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root lists a directory whatever its mode, so the refusal cannot be planted")
    disk = tmp_path / "sda"
    disk.mkdir()
    disk.chmod(0o300)  # searchable, so it still reads as a directory, but not listable
    try:
        assert mounts.read_partitions(disk) is None
    finally:
        disk.chmod(0o700)


@pytest.mark.os_linux
def test_a_stacked_device_whose_directory_cannot_be_listed_reads_its_partitions_as_not_read(tmp_path: Path) -> None:
    """An unlistable stacked device must still name its own layer, not read as partitionless."""
    if os.geteuid() == 0:
        pytest.skip("root lists a directory whatever its mode, so the refusal cannot be planted")
    dm = tmp_path / "dm-0"
    (dm / "dm").mkdir(parents=True)
    (dm / "holders" / "dm-1").mkdir(parents=True)
    (dm / "dev").write_text("253:0\n", encoding="utf-8")
    (dm / "dm" / "name").write_text("cryptroot\n", encoding="utf-8")
    (dm / "dm" / "uuid").write_text("CRYPT-LUKS2-abc-cryptroot\n", encoding="utf-8")
    dm.chmod(0o300)  # still searchable, so every attribute reads; only the listing fails
    try:
        found = mounts.read_stacked(["dm-0"], tmp_path)
    finally:
        dm.chmod(0o700)
    assert "partitions" not in found["dm-0"], "an unlisted stacked device was recorded as having no partitions"
    assert found["dm-0"]["dev"] == "253:0"
    assert found["dm-0"]["dm_name"] == "cryptroot"
    assert found["dm-0"]["holders"] == ["dm-1"]


@pytest.mark.os_agnostic
def test_a_stacked_device_without_a_partitions_key_reads_as_not_read() -> None:
    """A capture older than this field, or one holding an unlistable device, still validates."""
    capture = LinuxCapture.model_validate(
        {
            "schema": 1,
            "platform": "linux",
            "hostname": "example",
            "kernel": "6.1.0",
            "pci": {},
            "stacked": {"dm-0": {"dev": "253:0", "dm_name": "cryptroot", "holders": []}},
        }
    )
    assert capture.stacked["dm-0"].partitions is None
