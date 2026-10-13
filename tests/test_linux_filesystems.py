"""The filesystems that live on a Linux disk, built from hand-made captures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from test_linux_free_extents import FakeFsmap
from workcount import work_to_run

from lsdsk.adapters.hw.linux import freespace
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.filesystems import build_filesystems
from lsdsk.adapters.hw.linux.usage import disks_by_device, resolve_usage
from lsdsk.adapters.render import theme
from lsdsk.adapters.render.tables import filesystem_table_row
from lsdsk.domain.enums import Environment, FilesystemKind
from lsdsk.domain.models import FreeSpace

if TYPE_CHECKING:
    from pathlib import Path

BARE_METAL = Environment.BARE_METAL


def _capture(block: dict[str, Any], **extra: Any) -> LinuxCapture:
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "linux",
        "hostname": "h",
        "kernel": "6.1",
        "pci": {},
        "block": block,
        "mounts": [],
        "swaps": [],
        "stacked": {},
        "signatures": {},
    }
    reading.update(extra)
    return LinuxCapture.model_validate(reading)


def _disk(dev: str, partitions: dict[str, Any] | None = None, holders: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"dev": dev, "holders": list(holders), "partitions": partitions or {}}


def _mount(dev: str, mountpoint: str, fstype: str = "ext4", source: str = "") -> dict[str, str]:
    return {"dev": dev, "mountpoint": mountpoint, "fstype": fstype, "source": source}


def test_a_filesystem_on_lvm_over_two_disks_names_both() -> None:
    capture = _capture(
        {
            "sda": _disk("8:0", {"sda1": {"dev": "8:1", "holders": ["dm-0"]}}),
            "sdb": _disk("8:16", {"sdb1": {"dev": "8:17", "holders": ["dm-0"]}}),
        },
        stacked={"dm-0": {"dev": "253:0", "dm_name": "vg0-var", "dm_uuid": "LVM-"}},
        mounts=[_mount("253:0", "/var")],
        filesystem_space={"253:0": {"frsize": 4096, "blocks": 100, "bfree": 10, "bavail": 5, "files": 9, "ffree": 3}},
    )
    rows, refused = build_filesystems(capture, BARE_METAL)
    assert refused == ()
    assert rows is not None
    (var,) = rows
    assert (var.name, var.disks, var.used_bytes, var.free_bytes) == ("/var", ("sda", "sdb"), 90 * 4096, 5 * 4096)


def test_bind_mounts_of_one_filesystem_are_one_row() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/"), _mount("8:1", "/srv/a"), _mount("8:1", "/srv/b")],
        filesystem_space={},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(row.name, row.other_mounts) for row in rows] == [("/", ("/srv/a", "/srv/b"))]


def test_a_pool_is_one_row_with_its_members_and_no_datasets() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}}), "sdb": _disk("8:16", {"sdb1": {"dev": "8:17"}})},
        signatures={
            "8:1": {"fs_type": "zfs_member", "fs_label": "tank"},
            "8:17": {"fs_type": "zfs_member", "fs_label": "tank"},
        },
        mounts=[_mount("0:50", "/tank", "zfs", "tank"), _mount("0:51", "/tank/a", "zfs", "tank")],
        zfs_pools={"tank": {"size": 1000, "alloc": 960}},
        filesystem_space={},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    (pool,) = rows
    assert (pool.kind, pool.name, pool.disks, pool.other_mounts, pool.used_percent) == (
        FilesystemKind.POOL,
        "tank",
        ("sda", "sdb"),
        (),
        96,
    )


def test_a_pool_allocated_beyond_its_size_never_reports_a_negative_figure() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"}},
        zfs_pools={"tank": {"size": 1000, "alloc": 1200}},
        filesystem_space={},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    (pool,) = rows
    assert (pool.size_bytes, pool.used_bytes, pool.free_bytes, pool.used_percent) == (1000, 1000, 0, 100)


def test_more_free_inodes_than_inodes_is_clamped_to_the_total() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/")],
        filesystem_space={"8:1": {"frsize": 4096, "blocks": 10, "bfree": 5, "bavail": 5, "files": 100, "ffree": 150}},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert (rows[0].inodes_total, rows[0].inodes_free) == (100, 100)
    assert rows[0].inodes_used_percent == 0


def test_more_free_blocks_than_blocks_never_reports_negative_use() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/")],
        filesystem_space={"8:1": {"frsize": 4096, "blocks": 10, "bfree": 15, "bavail": 15, "files": 1, "ffree": 1}},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert rows[0].used_bytes == 0


def test_a_missing_control_device_with_pool_members_is_a_refusal() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"}},
        zfs_pools=None,
        filesystem_space={},
    )
    _rows, refused = build_filesystems(capture, BARE_METAL)
    assert [(r.reading, r.reason) for r in refused] == [("zfs-pools", "the ZFS control device was not available")]


def test_no_control_device_and_no_pool_members_is_not_a_refusal() -> None:
    capture = _capture({"sda": _disk("8:0")}, zfs_pools=None, filesystem_space={})
    assert build_filesystems(capture, BARE_METAL)[1] == ()


def test_a_refused_statvfs_is_carried_on_its_row() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/data")],
        filesystem_space={"8:1": {"error": "Permission denied"}},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(r.reading, r.reason) for r in rows[0].readings_refused] == [("statvfs", "Permission denied")]
    assert rows[0].used_bytes is None


def test_a_mount_on_no_inventory_disk_is_not_a_row() -> None:
    capture = _capture({"sda": _disk("8:0")}, mounts=[_mount("7:0", "/snap/x", "squashfs")], filesystem_space={})
    assert build_filesystems(capture, BARE_METAL)[0] == ()


def test_a_container_and_an_unread_mount_table_read_nothing() -> None:
    assert build_filesystems(_capture({}, filesystem_space={}), Environment.CONTAINER) == (None, ())
    assert build_filesystems(_capture({}, mounts=None), BARE_METAL) == (None, ())


def test_a_capture_taken_before_space_was_read_reads_nothing() -> None:
    # A capture from before this reading: mounts and pool members, no
    # filesystem_space and no zfs_pools, which must not claim the control
    # device was missing nor draw rows of "-".
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}, "sda2": {"dev": "8:2"}})},
        signatures={"8:2": {"fs_type": "zfs_member", "fs_label": "tank"}},
        mounts=[_mount("8:1", "/boot")],
    )
    assert build_filesystems(capture, BARE_METAL) == (None, ())


def test_every_mount_the_used_by_cell_names_resolves_to_the_same_disk() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1", "holders": ["dm-0"]}})},
        stacked={
            "dm-0": {"dev": "253:0", "dm_name": "cr", "dm_uuid": "CRYPT-", "holders": ["dm-1"]},
            "dm-1": {"dev": "253:1", "dm_name": "vg-root", "dm_uuid": "LVM-"},
        },
        mounts=[_mount("253:1", "/")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    by_device = disks_by_device(capture)
    named = 0
    for node, disk_usage in usage.items():
        assert disk_usage is not None
        for use in disk_usage.uses:
            for mountpoint in use.mounts:
                (mount,) = [m for m in capture.mounts or () if m.mountpoint == mountpoint]
                assert node in by_device[mount.dev]
                named += 1
    assert named > 0


class _CountedText(str):
    """A mountpoint whose every comparison and hash is a Python call."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return str.__eq__(self, other)

    def __hash__(self) -> int:
        return str.__hash__(self)


def _binds(count: int) -> LinuxCapture:
    capture = _capture(
        {"sdc": _disk("8:32")},
        mounts=[_mount("8:32", f"/bind/{n}") for n in range(count)],
        filesystem_space={},
    )
    # Validation turns a str subclass back into str, so the counted text goes
    # in afterwards through copies, which do not validate.
    mounts = [mount.model_copy(update={"mountpoint": _CountedText(mount.mountpoint)}) for mount in capture.mounts or ()]
    return capture.model_copy(update={"mounts": mounts})


def _work(count: int) -> int:
    capture = _binds(count)
    rows, work = work_to_run(lambda: build_filesystems(capture, BARE_METAL)[0])
    # Control: all binds folded into the one row, so the counted work is the folding.
    assert rows is not None
    assert [len(row.other_mounts) for row in rows] == [count - 1]
    return work


def test_building_the_rows_of_many_bind_mounts_grows_linearly() -> None:
    ratio = _work(4_000) / _work(1_000)

    # Four times the mounts: linear reads about 4, a list scan about 14.
    assert ratio < 6, f"4000 mounts cost {ratio:.1f}x the work of 1000"


def test_a_refused_zfs_pool_listing_is_carried_with_its_message() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"}},
        zfs_pools_error="ioctl failed: EPERM",
        filesystem_space={},
    )
    _rows, refused = build_filesystems(capture, BARE_METAL)
    assert [(r.reading, r.reason) for r in refused] == [("zfs-pools", "ioctl failed: EPERM")]


def test_a_refused_zfs_pool_listing_with_no_pool_member_is_not_a_refusal() -> None:
    # /dev/zfs at mode 0600 on a host with no pool: the refused listing could
    # have missed no row, the same rule as a missing control device.
    capture = _capture({"sda": _disk("8:0")}, zfs_pools_error="Permission denied", filesystem_space={})
    assert build_filesystems(capture, BARE_METAL) == ((), ())


def _inode_row(**space: int) -> tuple[int | None, int | None]:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/")],
        filesystem_space={"8:1": {"frsize": 4096, "blocks": 10, "bfree": 5, "bavail": 5, **space}},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    return rows[0].inodes_total, rows[0].inodes_free


def test_the_inode_pair_is_emitted_whole_or_not_at_all() -> None:
    assert _inode_row(files=100, ffree=40) == (100, 40)
    assert _inode_row(ffree=40) == (None, None)
    assert _inode_row(files=100) == (None, None)


def _read_only_of(*flags: bool | None) -> bool:
    mounts = [
        {**_mount("8:1", f"/m{index}"), **({} if flag is None else {"read_only": flag})}
        for index, flag in enumerate(flags)
    ]
    capture = _capture({"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})}, mounts=mounts, filesystem_space={})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    (row,) = rows
    return row.read_only


def test_a_filesystem_is_read_only_only_when_every_mount_of_it_is() -> None:
    assert _read_only_of(True, True) is True
    assert _read_only_of(True, False) is False
    assert _read_only_of(False, True) is False
    assert _read_only_of(True, None) is False


def test_a_mount_whose_device_is_unknown_is_found_through_its_source_device() -> None:
    mount = {**_mount("0:99", "/data", "btrfs"), "source_dev": "8:1"}
    capture = _capture({"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})}, mounts=[mount], filesystem_space={})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(row.name, row.disks) for row in rows] == [("/data", ("sda",))]


def test_a_pool_with_no_member_disk_makes_no_row() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"}},
        zfs_pools={"tank": {"size": 1000, "alloc": 10}, "ghost": {"size": 500, "alloc": 5}},
        filesystem_space={},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [row.name for row in rows] == ["tank"]


def _pool_capture(**extra: Any) -> LinuxCapture:
    return _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"}},
        mounts=[_mount("0:50", "/tank", "zfs", "tank")],
        zfs_pools={"tank": {"size": 1000, "alloc": 500}},
        filesystem_space={},
        **extra,
    )


def test_a_pool_carries_its_own_fragmentation_and_nothing_finer() -> None:
    rows, refusals = build_filesystems(_pool_capture(zfs_fragmentation={"tank": 37}), BARE_METAL)
    assert rows is not None
    (pool,) = rows
    assert pool.free_space == FreeSpace(fragmentation_percent=37)
    assert refusals == ()


def test_a_capture_taken_before_fragmentation_was_read_has_no_free_space_and_no_refusal() -> None:
    rows, refusals = build_filesystems(_pool_capture(), BARE_METAL)
    assert rows is not None
    assert (rows[0].free_space, refusals) == (None, ())


def test_a_refused_fragmentation_reading_is_reported_once_for_the_inventory() -> None:
    rows, refusals = build_filesystems(
        _pool_capture(zfs_fragmentation={}, zfs_fragmentation_error="pool tank: boom"), BARE_METAL
    )
    assert rows is not None
    assert rows[0].free_space is None
    assert [(r.reading, r.reason) for r in refusals] == [("zfs-fragmentation", "pool tank: boom")]


def test_a_pool_listing_refusal_does_not_hide_the_fragmentation_refusal() -> None:
    _, refusals = build_filesystems(
        _pool_capture(
            zfs_pools_error="pool other: stats refused",
            zfs_fragmentation={},
            zfs_fragmentation_error="pool tank: get_props refused",
        ),
        BARE_METAL,
    )
    assert [(r.reading, r.reason) for r in refusals] == [
        ("zfs-pools", "pool other: stats refused"),
        ("zfs-fragmentation", "pool tank: get_props refused"),
    ]


def test_an_empty_filesystem_walk_reads_zero_extents_and_draws_not_applicable(tmp_path: Path) -> None:
    node = tmp_path / "mount"
    node.write_bytes(b"")  # a plain file: Windows refuses os.open on a directory
    rows = [{"dev": "8:1", "mountpoint": "/data", "fstype": "ext4"}]
    reading = freespace.read_free_extents(
        rows, ioctl=FakeFsmap([]), opener=lambda _mountpoint: os.open(node, os.O_RDONLY)
    )
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/data")],
        filesystem_space={
            "8:1": {"frsize": 4096, "blocks": 100, "bfree": 100, "bavail": 100, "files": 10, "ffree": 10}
        },
        free_extents=reading,
    )
    built, _ = build_filesystems(capture, BARE_METAL)
    assert built is not None
    (filesystem,) = built
    assert filesystem.free_space is not None
    assert filesystem.free_space.free_extent_count == 0
    assert filesystem_table_row(filesystem, both_scales=True)["frag"][0] == theme.NOT_APPLICABLE


def test_an_ext4_mount_carries_its_free_extents() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/data")],
        filesystem_space={"8:1": {"frsize": 4096, "blocks": 100, "bfree": 50, "bavail": 50, "files": 10, "ffree": 5}},
        free_extents={"8:1": {"count": 1, "total": 4096, "largest": 4096, "histogram": [0, 0, 0, 4096] + [0] * 17}},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert rows[0].free_space is not None
    assert rows[0].free_space.fragmentation_percent == 93


def test_a_refused_free_extent_walk_is_carried_on_its_row() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
        mounts=[_mount("8:1", "/data")],
        filesystem_space={},
        free_extents={"8:1": {"error": "Operation not permitted"}},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(r.reading, r.reason) for r in rows[0].readings_refused] == [("free-extents", "Operation not permitted")]
    assert rows[0].free_space is None
