"""A two-disk btrfs and a zoned xfs, held against a real machine.

``linux-btrfs-raid1-zoned-xfs`` was taken by the production reader on a Linux 7.0 virtual machine
with a btrfs RAID1 over sdb and sdc mounted, and an xfs whose realtime section lives inside sdd
and is zoned (``mkfs.xfs -r zoned=1``). The figures below are what that machine's own tools
reported at the capture, not what lsdsk computed: ``df -B1`` for size, used and available,
``btrfs filesystem show`` for the members and ``xfs_io -c "fsmap -v"`` for the xfs free records.

Two claims rest on this capture alone. mountinfo names only sdb as the btrfs's source, so sdc is
joined to the mount by the kernel's member list under ``/sys/fs/btrfs`` and by nothing else. And
the zoned xfs answers GETFSMAP with section cookies rather than device numbers (``xfs_io`` prints
devices 0x1 and 0x3), the case where the free-space walk counts every record instead of filtering
by device: its realtime free space has to be in the summary, and only the walk can put it there,
because ``xfs_spaceman -c "freesp -r"`` prints nothing for a zoned section.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.enums import UseKind

if TYPE_CHECKING:
    from lsdsk.domain.models import Filesystem, Inventory

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-btrfs-raid1-zoned-xfs.json"

#: df -B1 at the capture: size, used, available.
DF = {"/mnt/b": (8589934592, 215842816, 8102346752), "/mnt/zx": (15569256448, 209715200, 15359541248)}

#: xfs_io fsmap -v on /mnt/zx, free-space records in 512-byte sectors: six on the data section
#: (device 0x1) and two on the realtime section (device 0x3).
DATA_FREE_SECTORS = (32, 261952, 262048, 24, 130880, 262048)
REALTIME_FREE_SECTORS = (524288, 30523392)


def _machine() -> Inventory:
    payload: dict[str, Any] = json.loads(CAPTURE.read_text(encoding="utf-8"))
    return build_from(payload)


def _mount(machine: Inventory, name: str) -> Filesystem:
    assert machine.filesystems is not None, "the capture carries no filesystem reading at all"
    found = [fs for fs in machine.filesystems if fs.name == name]
    assert len(found) == 1, f"{name}: {len(found)} rows - the capture no longer holds this mount"
    return found[0]


@pytest.mark.os_agnostic
def test_mountinfo_names_one_btrfs_member_and_the_capture_lists_both() -> None:
    """The premise: without the member list, sdc could not be joined."""
    payload: dict[str, Any] = json.loads(CAPTURE.read_text(encoding="utf-8"))
    (btrfs,) = [mount for mount in payload["mounts"] if mount["fstype"] == "btrfs"]

    sdb, sdc = payload["block"]["sdb"]["dev"], payload["block"]["sdc"]["dev"]
    assert btrfs["source_dev"] == sdb
    assert payload["btrfs_devices"] == [[sdb, sdc]]


@pytest.mark.os_agnostic
def test_both_btrfs_members_carry_the_mount_and_the_row_names_both() -> None:
    machine = _machine()
    disks = {disk.node: disk for disk in machine.disks}

    for node in ("sdb", "sdc"):
        usage = disks[node].usage
        assert usage is not None, f"{node} reads as not read"
        assert [(use.kind, use.mounts) for use in usage.uses] == [(UseKind.MOUNT, ("/mnt/b",))], node
    assert _mount(machine, "/mnt/b").disks == ("sdb", "sdc")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["/mnt/b", "/mnt/zx"])
def test_the_fill_is_what_df_reported(name: str) -> None:
    fs = _mount(_machine(), name)

    assert (fs.size_bytes, fs.used_bytes, fs.free_bytes) == DF[name]


@pytest.mark.os_agnostic
def test_the_zoned_xfs_free_space_counts_both_sections() -> None:
    """Every free record, data and realtime, as xfs_io listed them."""
    free_space = _mount(_machine(), "/mnt/zx").free_space
    sectors = DATA_FREE_SECTORS + REALTIME_FREE_SECTORS

    assert free_space is not None, "the zoned xfs's free space was not read"
    assert free_space.free_extent_count == len(sectors)
    assert free_space.free_extent_bytes == sum(sectors) * 512
    assert free_space.largest_free_extent_bytes == max(REALTIME_FREE_SECTORS) * 512
