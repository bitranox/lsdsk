"""How ``read_system`` wires one reading into the next, driven through its readers seam with no machine behind it.

Each later reading is asked about devices the readings before it named: the udev lookup about the disks and every
stacked layer above them, the statvfs and free-space walks about the filesystems an inventoried disk carries. A
reader test cannot see that wiring, because each reader is handed its input by the test; only ``read_system`` itself
decides what the next one is asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from lsdsk.adapters.hw.linux import reader
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.snapshot import build_from

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_BLOCK: dict[str, dict[str, Any]] = {
    "sda": {
        "dev": "8:0",
        "holders": [],
        "partitions": {"sda1": {"dev": "8:1", "holders": ["dm-0"]}, "sda2": {"dev": "8:2", "holders": []}},
    },
    "loop0": {"dev": "7:0", "holders": [], "virtual": True, "partitions": {}},
}
_STACKED: dict[str, dict[str, Any]] = {"dm-0": {"dev": "253:0", "dm_uuid": "CRYPT-", "holders": [], "partitions": {}}}
_MOUNTS: list[dict[str, str | bool]] = [
    {"dev": "253:0", "mountpoint": "/", "fstype": "xfs", "read_only": False},
    {"dev": "8:2", "mountpoint": "/boot", "fstype": "ext4", "read_only": False},
    {"dev": "7:0", "mountpoint": "/image", "fstype": "ext4", "read_only": False},
    {"dev": "0:77", "mountpoint": "/elsewhere", "fstype": "ext4", "read_only": False, "source_dev": "8:48"},
]


class _Recorded:
    """The fakes' record of what ``read_system`` asked each reader about."""

    def __init__(self) -> None:
        self.stacked: list[list[str]] = []
        self.signatures: list[list[str]] = []
        self.space: list[list[str]] = []
        self.free_extents: list[list[str]] = []


def _mountpoints(rows: Sequence[Mapping[str, str | bool]]) -> list[str]:
    return [str(row["mountpoint"]) for row in rows]


def _readers(
    recorded: _Recorded,
    *,
    mounts: list[dict[str, str | bool]] | None = _MOUNTS,
    environment: dict[str, Any] | None = None,
    block: dict[str, dict[str, Any]] = _BLOCK,
    btrfs: list[list[str]] | None = None,
) -> reader.SystemReaders:
    def stacked(names: list[str]) -> dict[str, dict[str, Any]]:
        recorded.stacked.append(list(names))
        return {name: entry for name, entry in _STACKED.items() if name in names}

    def signatures(devnums: list[str]) -> dict[str, dict[str, str]]:
        recorded.signatures.append(list(devnums))
        return {}

    def space(rows: Sequence[Mapping[str, str | bool]]) -> dict[str, dict[str, int | str]]:
        recorded.space.append(_mountpoints(rows))
        return {}

    def free_extents(rows: Sequence[Mapping[str, str | bool]]) -> dict[str, dict[str, Any]]:
        recorded.free_extents.append(_mountpoints(rows))
        return {}

    return reader.SystemReaders(
        block=lambda: block,
        pci=dict,
        pci_names=lambda _pci: {},
        classes=dict,
        environment=lambda: environment or {"hypervisor_flag": False},
        host=lambda: reader.HostIdentity("host", "6.12.0", 0),
        devices_accessible=lambda _nodes: True,
        ata=lambda _nodes: {},
        nvme=lambda _nodes: {},
        usb=lambda _block: {},
        stacked=stacked,
        signatures=signatures,
        pools=lambda: (None, None),
        fragmentation=lambda: (None, None),
        pool_layouts=lambda: {"tank": {"guid": "42", "vdevs": [{"kind": "disk", "dev": "8:1"}]}},
        mount_table=lambda: mounts,
        swaps=list,
        # Never this machine's own /sys/fs/btrfs: a test reads only what it was handed.
        btrfs_devices=lambda: btrfs,
        space=space,
        free_extents=free_extents,
    )


def test_a_reading_composed_from_the_readers_is_a_capture_the_builder_takes() -> None:
    # Liveness for the tests below: the seam reaches every reader and its result parses as a real capture.
    reading = reader.read_system(_readers(_Recorded()))
    capture = LinuxCapture.model_validate(reading)
    assert (capture.hostname, reading["kernel"], capture.euid) == ("host", "6.12.0", 0)
    assert set(capture.block) == {"sda", "loop0"}


def test_the_udev_lookup_is_asked_about_every_stacked_device_as_well_as_the_disks() -> None:
    recorded = _Recorded()
    reader.read_system(_readers(recorded))
    assert recorded.stacked == [["dm-0"]]
    # A pool or filesystem on dm-crypt, LVM or md carries its signature on the stacked device, not the partition.
    # The loop's own number is not asked: nothing reads a virtual device's signature, as before it was recorded.
    assert recorded.signatures == [["8:0", "8:1", "8:2", "253:0"]]


def test_the_free_space_walk_is_asked_only_about_filesystems_an_inventoried_disk_carries() -> None:
    recorded = _Recorded()
    reader.read_system(_readers(recorded))
    # The loop image and the filesystem whose source names a disk this run never saw are left out.
    assert recorded.free_extents == [["/", "/boot"]]


def test_with_no_mount_table_nothing_is_stat_ed_or_walked() -> None:
    recorded = _Recorded()
    reading = reader.read_system(_readers(recorded, mounts=None))
    assert (reading["filesystem_space"], reading["free_extents"]) == (None, None)
    assert (recorded.space, recorded.free_extents) == ([], [])


def test_statvfs_is_asked_only_about_filesystems_an_inventoried_disk_carries() -> None:
    recorded = _Recorded()
    reader.read_system(_readers(recorded))
    # Every mount was stat-ed once, a loop image and a mount on an unknown device among them, though only a
    # filesystem on an inventoried disk can be a row (ADR 0005).
    assert recorded.space == [["/", "/boot"]]


def test_inside_a_container_no_filesystem_is_stat_ed_or_walked() -> None:
    # The filesystems section is never built in a container, where the host's own use of its disks is invisible,
    # so a walk there costs real time per filesystem for a reading nothing shows.
    recorded = _Recorded()
    reading = reader.read_system(_readers(recorded, environment={"container_marker": "lxc"}))
    assert (recorded.space, recorded.free_extents) == ([[]], [[]])
    assert (reading["filesystem_space"], reading["free_extents"]) == ({}, {})


def test_each_listed_pool_s_layout_reaches_the_capture() -> None:
    reading = reader.read_system(_readers(_Recorded()))
    layouts = LinuxCapture.model_validate(reading).zfs_pool_layouts
    assert layouts is not None
    assert [(vdev.kind, vdev.dev) for vdev in layouts["tank"].vdevs or ()] == [("disk", "8:1")]


def test_a_reading_that_will_be_refused_stats_and_walks_no_filesystem() -> None:
    # A reading that does not type is refused as a whole once it is parsed, so a statvfs or a free-space walk
    # of every mount - a network one that hangs among them - would only delay that refusal.
    recorded = _Recorded()
    reading = reader.read_system(_readers(recorded, environment={"hypervisor_flag": []}))

    # control: the run is refused for this reading
    with pytest.raises(ValidationError, match=r"hypervisor_flag\n  Input should be a valid boolean"):
        LinuxCapture.model_validate(reading)
    assert (recorded.space, recorded.free_extents) == ([[]], [[]])


def test_a_btrfs_whose_source_is_a_loop_member_is_stat_ed_when_another_member_is_on_a_disk() -> None:
    # mountinfo names the loop member as the source; the kernel's member list names a disk partition beside it.
    sdb: dict[str, Any] = {"dev": "8:16", "holders": [], "partitions": {"sdb1": {"dev": "8:17", "holders": []}}}
    block = {**_BLOCK, "sdb": sdb}
    btrfs_mount: dict[str, str | bool] = {
        "dev": "0:55",
        "mountpoint": "/mnt/b",
        "fstype": "btrfs",
        "read_only": False,
        "source_dev": "7:0",
    }
    recorded = _Recorded()
    reading = reader.read_system(
        _readers(recorded, mounts=[*_MOUNTS, btrfs_mount], block=block, btrfs=[["7:0", "8:17"]])
    )
    assert recorded.space == [["/", "/boot", "/mnt/b"]]
    assert recorded.free_extents == [["/", "/boot", "/mnt/b"]]
    # Control: the filesystems section draws that very row, on the disk the partition member sits on.
    inventory = build_from(reading)
    assert {each.name: each.disks for each in inventory.filesystems or ()}["/mnt/b"] == ("sdb",)
