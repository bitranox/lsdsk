"""Which disks a ZFS pool sits on, traced from the pool's own config rather than from a label of the same name.

A capture carrying ``zfs_pool_layouts`` names each listed pool's leaf vdevs by device number, so a member is the
device the kernel's pool config names, and a udev label counts only where its pool GUID is the listed pool's. A
capture taken before that field keeps joining pools to disks by their members' labels alone, exactly as before.
"""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.filesystems import build_filesystems
from lsdsk.domain.enums import Environment
from lsdsk.domain.models import RefusedReading

BARE_METAL = Environment.BARE_METAL
_RPOOL_GUID = "2936817686098969225"
_GUEST_GUID = "16115888057062062835"
_UNTRACED_REASON = "no member was traced to a disk this scan inventoried"


def _host(**extra: Any) -> dict[str, Any]:
    """A host whose rpool sits on sda1 and whose nvme0n1p1 is an LVM PV carrying a guest's disk as an LV (dm-3)."""
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "linux",
        "hostname": "h",
        "kernel": "6.1",
        "pci": {},
        "block": {
            "sda": {"dev": "8:0", "holders": [], "partitions": {"sda1": {"dev": "8:1", "holders": []}}},
            "nvme0n1": {
                "dev": "259:0",
                "holders": [],
                "partitions": {"nvme0n1p1": {"dev": "259:1", "holders": ["dm-3"]}},
            },
            "loop0": {"dev": "7:0", "holders": [], "virtual": True, "partitions": {}},
        },
        "stacked": {"dm-3": {"dev": "253:3", "dm_name": "pve-vm--100--disk--0", "dm_uuid": "LVM-", "holders": []}},
        "mounts": [],
        "swaps": [],
        "signatures": {
            "8:1": {"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": _RPOOL_GUID},
            # The guest's own root pool runs to the end of its disk image, so the host's udev reads its label there.
            "253:3": {"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": _GUEST_GUID},
        },
        "zfs_pools": {"rpool": {"size": 1000, "alloc": 100}},
        "zfs_pool_layouts": {"rpool": {"guid": _RPOOL_GUID, "vdevs": [{"kind": "disk", "dev": "8:1"}]}},
        "filesystem_space": {},
    }
    reading.update(extra)
    return reading


def _built(reading: dict[str, Any]) -> tuple[list[tuple[str, tuple[str, ...]]], tuple[RefusedReading, ...]]:
    rows, refused = build_filesystems(LinuxCapture.model_validate(reading), BARE_METAL)
    assert rows is not None
    return [(row.name, row.disks) for row in rows], refused


def _untraced(*names: str) -> tuple[RefusedReading, ...]:
    return tuple(RefusedReading(reading="zfs-pool-disks", reason=f"pool {name}: {_UNTRACED_REASON}") for name in names)


def _without_layouts(reading: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in reading.items() if key != "zfs_pool_layouts"}


def test_a_guest_label_of_the_same_name_on_a_host_volume_does_not_join_the_host_pool() -> None:
    assert _built(_host()) == ([("rpool", ("sda",))], ())


def test_a_capture_without_pool_layouts_still_joins_by_label_as_before() -> None:
    # The same machine recorded before the layouts were read: nothing says which label is the listed pool's.
    assert _built(_without_layouts(_host())) == ([("rpool", ("nvme0n1", "sda"))], ())


def test_with_no_zfs_control_device_a_guest_label_draws_no_pool_and_refuses_nothing() -> None:
    # No /dev/zfs: nothing is imported here, so the label on the guest's volume is the guest's own pool.
    reading = _host(
        zfs_pools=None,
        zfs_pool_layouts={},
        signatures={"253:3": {"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": _GUEST_GUID}},
    )
    assert _built(reading) == ([], ())
    # Control: recorded before the layouts were read, the same machine draws the label as a pool and refuses it.
    rows, refused = _built(_without_layouts(reading))
    assert rows == [("rpool", ("nvme0n1",))]
    assert [entry.reading for entry in refused] == ["zfs-pools"]


def test_a_refused_pool_listing_with_a_member_on_a_disk_is_still_a_refusal() -> None:
    reading = _host(zfs_pools=None, zfs_pools_error="could not open /dev/zfs (Permission denied)", zfs_pool_layouts={})
    assert _built(reading) == (
        [],
        (RefusedReading(reading="zfs-pools", reason="could not open /dev/zfs (Permission denied)"),),
    )


@pytest.mark.parametrize(
    "vdevs",
    [
        pytest.param([{"kind": "file"}], id="file"),
        pytest.param([{"kind": "disk", "dev": "7:0"}], id="loop"),
        pytest.param([{"kind": "file"}, {"kind": "disk", "dev": "7:0"}], id="file-and-loop"),
    ],
)
def test_a_pool_on_files_or_virtual_devices_has_no_row_and_no_refusal(vdevs: list[dict[str, str]]) -> None:
    reading = _host(
        zfs_pools={"rpool": {"size": 1000, "alloc": 100}, "scratch": {"size": 10, "alloc": 9}},
        zfs_pool_layouts={
            "rpool": {"guid": _RPOOL_GUID, "vdevs": [{"kind": "disk", "dev": "8:1"}]},
            "scratch": {"guid": "7", "vdevs": vdevs},
        },
    )
    assert _built(reading) == ([("rpool", ("sda",))], ())


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param({"guid": "7", "vdevs": [{"kind": "disk", "dev": "253:99"}]}, id="device-nothing-reaches"),
        pytest.param({"guid": "7", "vdevs": [{"kind": "disk"}]}, id="path-that-did-not-resolve"),
        pytest.param({"guid": "7"}, id="config-without-a-vdev-tree"),
        pytest.param(None, id="no-layout-at-all"),
    ],
)
def test_a_pool_whose_member_could_not_be_followed_is_a_refusal(layout: dict[str, Any] | None) -> None:
    layouts: dict[str, Any] = {"rpool": {"guid": _RPOOL_GUID, "vdevs": [{"kind": "disk", "dev": "8:1"}]}}
    if layout is not None:
        layouts["ghost"] = layout
    reading = _host(
        zfs_pools={"rpool": {"size": 1000, "alloc": 100}, "ghost": {"size": 10, "alloc": 9}}, zfs_pool_layouts=layouts
    )
    assert _built(reading) == ([("rpool", ("sda",))], _untraced("ghost"))


def test_a_member_whose_path_did_not_resolve_is_found_by_its_pool_guid_and_only_by_it() -> None:
    reading = _host(
        zfs_pools={"tank": {"size": 1000, "alloc": 100}},
        zfs_pool_layouts={"tank": {"guid": "42", "vdevs": [{"kind": "disk"}, {"kind": "disk"}]}},
        signatures={
            "8:1": {"fs_type": "zfs_member", "fs_label": "tank", "fs_uuid": "42"},
            # Same name, another pool: a guest's, or one exported from elsewhere.
            "253:3": {"fs_type": "zfs_member", "fs_label": "tank", "fs_uuid": "43"},
        },
    )
    assert _built(reading) == ([("tank", ("sda",))], ())


def test_a_member_on_dm_crypt_is_traced_through_the_device_the_config_names() -> None:
    reading = _host(
        zfs_pools={"tank": {"size": 1000, "alloc": 980}},
        zfs_pool_layouts={"tank": {"guid": "42", "vdevs": [{"kind": "disk", "dev": "253:3"}]}},
        stacked={"dm-3": {"dev": "253:3", "dm_name": "cryptank", "dm_uuid": "CRYPT-", "holders": []}},
        signatures={"259:1": {"fs_type": "crypto_LUKS"}},
    )
    assert _built(reading) == ([("tank", ("nvme0n1",))], ())


def test_a_pool_the_config_lists_whose_space_could_not_be_read_keeps_its_row_and_the_refusal() -> None:
    reading = _host(zfs_pools={}, zfs_pools_error="pool rpool: no vdev_tree")
    rows, refused = _built(reading)
    assert rows == [("rpool", ("sda",))]
    assert refused == (RefusedReading(reading="zfs-pools", reason="pool rpool: no vdev_tree"),)


@pytest.mark.parametrize("layouts", [True, False], ids=["with-layouts", "without-layouts"])
def test_untraced_pools_are_refused_in_name_order_whatever_order_the_kernel_listed_them(layouts: bool) -> None:
    pools = {"rpool": {"size": 1000, "alloc": 100}, "zulu": {"size": 1, "alloc": 0}, "alpha": {"size": 1, "alloc": 0}}
    reading = _host(
        zfs_pools=pools,
        zfs_pool_layouts={
            "rpool": {"guid": _RPOOL_GUID, "vdevs": [{"kind": "disk", "dev": "8:1"}]},
            "zulu": {"guid": "1"},
            "alpha": {"guid": "2"},
        },
    )
    if not layouts:
        reading = _without_layouts(reading)
    assert list(LinuxCapture.model_validate(reading).zfs_pools or {}) == ["rpool", "zulu", "alpha"]  # control
    _, refused = _built(reading)
    assert refused == _untraced("alpha", "zulu")


@pytest.mark.parametrize(
    "found_there",
    [
        pytest.param({"fs_type": "ext4"}, id="another-filesystem"),
        pytest.param({"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": "5"}, id="another-pool-s-member"),
    ],
)
def test_a_member_path_now_naming_a_device_udev_reads_as_something_else_is_found_by_the_pool_guid(
    found_there: dict[str, str],
) -> None:
    # rpool's config still names sda1, as it does for a faulted member, while a replacement disk now sits at the
    # path; the member itself is on nvme0n1p1, where udev reads rpool's own GUID.
    reading = _host(
        stacked={},
        signatures={
            "8:1": found_there,
            "259:1": {"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": _RPOOL_GUID},
        },
    )
    assert _built(reading) == ([("rpool", ("nvme0n1",))], ())
    # With nothing carrying the pool's GUID, the pool is no row and says so, rather than a row on the wrong disk.
    alone = _host(stacked={}, signatures={"8:1": found_there})
    assert _built(alone) == ([], _untraced("rpool"))


def test_a_member_path_whose_device_udev_did_not_read_or_reads_as_this_pool_s_is_followed() -> None:
    # Controls for the test above: no signature, or one without a GUID, contradicts nothing.
    for signatures in ({}, {"8:1": {"fs_type": "zfs_member", "fs_label": "rpool"}}):
        assert _built(_host(stacked={}, signatures=signatures)) == ([("rpool", ("sda",))], ())


def test_a_former_member_still_labelled_with_the_pool_s_guid_does_not_join_a_pool_whose_members_all_followed() -> None:
    # A disk replaced out of rpool keeps its label, GUID and all; the config names only the member in use.
    reading = _host(
        signatures={
            "8:1": {"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": _RPOOL_GUID},
            "253:3": {"fs_type": "zfs_member", "fs_label": "rpool", "fs_uuid": _RPOOL_GUID},
        }
    )
    assert _built(reading) == ([("rpool", ("sda",))], ())
