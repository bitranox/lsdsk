"""What a Linux disk is used for, resolved from hand-built captures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.usage import resolve_usage
from lsdsk.domain.enums import Environment, UseKind
from lsdsk.domain.models import DiskUse


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


BARE_METAL = Environment.BARE_METAL


@pytest.mark.os_agnostic
def test_a_plain_root_partition_marks_its_disk_boot_and_names_the_mount() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}})},
        mounts=[_mount("8:1", "/")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["sda"] is not None
    assert usage["sda"].boot is True
    assert usage["sda"].uses == (DiskUse(kind=UseKind.MOUNT, mounts=("/",)),)


@pytest.mark.os_agnostic
def test_a_separate_efi_disk_is_boot_too() -> None:
    capture = _capture(
        block={
            "sdb": _disk("8:16", partitions={"sdb1": {"dev": "8:17", "holders": []}}),
            "sdc": _disk("8:32"),
        },
        mounts=[_mount("8:17", "/boot/efi", fstype="vfat")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["sdb"] is not None
    assert usage["sdb"].boot is True
    assert usage["sdc"] is not None
    assert usage["sdc"].boot is False
    assert usage["sdc"].uses == ()


@pytest.mark.os_agnostic
def test_a_zfs_mirror_marks_every_member_and_shows_the_pool_not_its_datasets() -> None:
    capture = _capture(
        block={
            "sde": _disk("8:64", partitions={"sde3": {"dev": "8:67", "holders": []}}),
            "sdf": _disk("8:80", partitions={"sdf3": {"dev": "8:83", "holders": []}}),
        },
        mounts=[
            _mount("0:28", "/", fstype="zfs", source="rpool/ROOT/pve-1"),
            _mount("0:29", "/rpool/data", fstype="zfs", source="rpool/data"),
        ],
        signatures={
            "8:67": {"fs_type": "zfs_member", "fs_label": "rpool"},
            "8:83": {"fs_type": "zfs_member", "fs_label": "rpool"},
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    for node in ("sde", "sdf"):
        resolved = usage[node]
        assert resolved is not None
        assert resolved.boot is True
        assert len(resolved.uses) == 1
        assert resolved.uses[0].kind == UseKind.ZFS
        assert resolved.uses[0].name == "rpool"
        assert resolved.uses[0].mounts == ()


@pytest.mark.os_agnostic
def test_a_whole_disk_signature_loses_to_the_partition_one() -> None:
    capture = _capture(
        block={
            "sdb": _disk(
                "8:16",
                partitions={"sdb3": {"dev": "8:19", "holders": []}},
            ),
            "sdg": _disk(
                "8:96",
                partitions={"sdg1": {"dev": "8:97", "holders": []}},
            ),
        },
        signatures={
            "8:16": {"fs_type": "zfs_member", "fs_label": "rpool"},
            "8:19": {"fs_type": "zfs_member", "fs_label": "rpool"},
            "8:96": {"fs_type": "ddf_raid_member", "fs_label": None},
            "8:97": {"fs_type": "zfs_member", "fs_label": "tank"},
        },
    )
    usage = resolve_usage(capture, BARE_METAL)

    sdb = usage["sdb"]
    assert sdb is not None
    zfs_uses = [use for use in sdb.uses if use.kind == UseKind.ZFS]
    assert len(zfs_uses) == 1
    assert zfs_uses[0].name == "rpool"

    sdg = usage["sdg"]
    assert sdg is not None
    for use in sdg.uses:
        assert use.name != "ddf"
        assert "ddf" not in use.kind.value
    # sdg1's own signature (zfs_member/tank) must still surface: the disk's
    # unrelated whole-disk signature (ddf_raid_member) must not shadow it.
    sdg_zfs = [use for use in sdg.uses if use.kind == UseKind.ZFS]
    assert len(sdg_zfs) == 1
    assert sdg_zfs[0].name == "tank"


@pytest.mark.os_agnostic
def test_lvm_under_a_partition_names_the_group_and_its_mounts() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda2": {"dev": "8:2", "holders": ["dm-0"]}})},
        mounts=[_mount("253:0", "/var")],
        stacked={"dm-0": {"dev": "253:0", "dm_name": "vg0-var", "dm_uuid": "LVM-abc", "holders": []}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert len(sda.uses) == 1
    assert sda.uses[0].kind == UseKind.LVM
    assert sda.uses[0].name == "vg0"
    assert sda.uses[0].mounts == ("/var",)


@pytest.mark.os_agnostic
def test_an_escaped_lvm_dash_is_unescaped_in_the_group_name() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda2": {"dev": "8:2", "holders": ["dm-0"]}})},
        mounts=[_mount("253:0", "/data")],
        stacked={"dm-0": {"dev": "253:0", "dm_name": "my--vg-lv", "dm_uuid": "LVM-abc", "holders": []}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert sda.uses[0].name == "my-vg"


@pytest.mark.os_agnostic
def test_crypt_over_lvm_reports_the_first_layer_with_every_mount_below_it() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda2": {"dev": "8:2", "holders": ["dm-0"]}})},
        mounts=[_mount("253:1", "/")],
        stacked={
            "dm-0": {"dev": "253:0", "dm_name": "cryptroot", "dm_uuid": "CRYPT-LUKS2-abc", "holders": ["dm-1"]},
            "dm-1": {"dev": "253:1", "dm_name": "vg0-root", "dm_uuid": "LVM-def", "holders": []},
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert sda.boot is True
    assert len(sda.uses) == 1
    assert sda.uses[0].kind == UseKind.CRYPT
    assert sda.uses[0].name == "cryptroot"
    assert sda.uses[0].mounts == ("/",)


@pytest.mark.os_agnostic
def test_an_md_member_names_its_array() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": ["md0"]}})},
        mounts=[_mount("9:0", "/srv")],
        stacked={"md0": {"dev": "9:0", "dm_name": None, "dm_uuid": None, "holders": []}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert len(sda.uses) == 1
    assert sda.uses[0].kind == UseKind.MD
    assert sda.uses[0].name == "md0"
    assert sda.uses[0].mounts == ("/srv",)


@pytest.mark.os_agnostic
def test_swap_on_a_partition_reads_swap() -> None:
    capture = _capture(
        block={"nvme4n1": _disk("259:12", partitions={"nvme4n1p1": {"dev": "259:13", "holders": []}})},
        swaps=[{"path": "/dev/nvme4n1p1", "dev": "259:13"}],
    )
    usage = resolve_usage(capture, BARE_METAL)
    nvme4n1 = usage["nvme4n1"]
    assert nvme4n1 is not None
    assert len(nvme4n1.uses) == 1
    assert nvme4n1.uses[0].kind == UseKind.SWAP


@pytest.mark.os_agnostic
def test_btrfs_is_found_through_its_source_device() -> None:
    capture = _capture(
        block={"sdb": _disk("8:16", partitions={"sdb1": {"dev": "8:17", "holders": []}})},
        mounts=[
            {
                "dev": "0:40",
                "mountpoint": "/srv",
                "fstype": "btrfs",
                "source": "/dev/sdb1",
                "source_dev": "8:17",
            }
        ],
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdb = usage["sdb"]
    assert sdb is not None
    assert len(sdb.uses) == 1
    assert sdb.uses[0].kind == UseKind.MOUNT
    assert sdb.uses[0].mounts == ("/srv",)


@pytest.mark.os_agnostic
def test_a_container_reads_every_disk_as_not_read() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}})},
        mounts=[_mount("8:1", "/")],
    )
    usage = resolve_usage(capture, Environment.CONTAINER)
    assert usage == {"sda": None}


@pytest.mark.os_agnostic
def test_a_capture_without_the_new_fields_reads_not_read() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}})},
        mounts=None,
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage == {"sda": None}


@pytest.mark.os_agnostic
def test_without_udev_a_disk_with_nothing_found_is_not_read_but_a_found_one_stays() -> None:
    capture = _capture(
        block={
            "sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}}),
            "sdb": _disk("8:16"),
        },
        mounts=[_mount("8:1", "/")],
        signatures=None,
    )
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["sda"] is not None
    assert usage["sda"].boot is True
    assert usage["sdb"] is None


@pytest.mark.os_agnostic
def test_a_disk_with_no_partitions_is_its_own_leaf() -> None:
    capture = _capture(
        block={"sdc": _disk("8:32")},
        mounts=[_mount("8:32", "/data")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdc = usage["sdc"]
    assert sdc is not None
    assert len(sdc.uses) == 1
    assert sdc.uses[0].kind == UseKind.MOUNT
    assert sdc.uses[0].mounts == ("/data",)


@pytest.mark.os_agnostic
def test_one_mount_seen_twice_is_listed_once() -> None:
    capture = _capture(
        block={"sdc": _disk("8:32")},
        mounts=[_mount("8:32", "/var"), _mount("8:32", "/var")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdc = usage["sdc"]
    assert sdc is not None
    assert len(sdc.uses) == 1
    assert sdc.uses[0].mounts == ("/var",)


@pytest.mark.os_agnostic
def test_build_inventory_carries_the_usage_on_its_disks() -> None:
    from lsdsk.adapters.hw.linux.builder import build_inventory

    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}})},
        mounts=[_mount("8:1", "/")],
    )
    inventory = build_inventory(capture)
    disks_by_node = {disk.node: disk for disk in inventory.disks}
    assert disks_by_node["sda"].usage is not None
    assert disks_by_node["sda"].usage.boot is True


FIXTURES = Path(__file__).parent / "fixtures" / "hw"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture_path", sorted(FIXTURES.glob("linux-*.json")), ids=lambda p: p.name)
def test_every_committed_linux_fixture_predates_usage_capture(fixture_path: Path) -> None:
    from lsdsk.adapters.hw.snapshot import build_from

    reading = json.loads(fixture_path.read_text(encoding="utf-8"))
    inventory = build_from(reading)
    for disk in inventory.disks:
        assert disk.usage is None


@pytest.mark.os_agnostic
def test_a_holder_on_the_whole_disk_is_found_even_when_a_partition_table_is_left_over() -> None:
    # A multipath path disk: device-mapper holds the raw disk, and the stale
    # partition table it still carries must not hide that.
    capture = _capture(
        block={"sdh": _disk("8:112", partitions={"sdh1": {"dev": "8:113", "holders": []}}, holders=("dm-3",))},
        mounts=[_mount("253:3", "/data")],
        stacked={"dm-3": {"dev": "253:3", "dm_name": "mpatha", "dm_uuid": "mpath-", "holders": []}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdh = usage["sdh"]
    assert sdh is not None
    assert sdh.uses == (DiskUse(kind=UseKind.STACK, name="mpatha", mounts=("/data",)),)


@pytest.mark.os_agnostic
def test_a_mount_on_the_whole_disk_is_found_beside_its_partitions() -> None:
    capture = _capture(
        block={"sdi": _disk("8:128", partitions={"sdi1": {"dev": "8:129", "holders": []}})},
        mounts=[_mount("8:128", "/raw"), _mount("8:129", "/part")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdi = usage["sdi"]
    assert sdi is not None
    assert set(sdi.uses[0].mounts) == {"/raw", "/part"}
    assert len(sdi.uses) == 1


@pytest.mark.os_agnostic
def test_swap_on_the_whole_disk_is_found_beside_its_partitions() -> None:
    capture = _capture(
        block={"sdi": _disk("8:128", partitions={"sdi1": {"dev": "8:129", "holders": []}})},
        swaps=[{"path": "/dev/sdi", "dev": "8:128"}],
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdi = usage["sdi"]
    assert sdi is not None
    assert sdi.uses == (DiskUse(kind=UseKind.SWAP),)


@pytest.mark.os_agnostic
def test_a_whole_disk_signature_counts_when_no_partition_carries_one() -> None:
    capture = _capture(
        block={"sdj": _disk("8:144", partitions={"sdj1": {"dev": "8:145", "holders": []}})},
        signatures={"8:144": {"fs_type": "zfs_member", "fs_label": "tank"}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdj = usage["sdj"]
    assert sdj is not None
    assert sdj.uses == (DiskUse(kind=UseKind.ZFS, name="tank"),)


@pytest.mark.os_agnostic
def test_a_whole_disk_signature_is_ignored_when_a_partition_carries_one() -> None:
    capture = _capture(
        block={"sdk": _disk("8:160", partitions={"sdk1": {"dev": "8:161", "holders": []}})},
        signatures={
            "8:160": {"fs_type": "zfs_member", "fs_label": "old"},
            "8:161": {"fs_type": "ext4"},
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdk = usage["sdk"]
    assert sdk is not None
    assert sdk.uses == ()


@pytest.mark.os_agnostic
def test_a_partitioned_md_array_reaches_the_mount_on_its_own_partition() -> None:
    # IMSM/DDF fake RAID: md126 holds the whole member disks and is itself
    # partitioned; md126p1 is a partition of md126, not one of its holders.
    capture = _capture(
        block={
            "sda": _disk("8:0", holders=("md126",)),
            "sdb": _disk("8:16", holders=("md126",)),
        },
        mounts=[_mount("259:1", "/")],
        stacked={
            "md126": {"dev": "9:126", "holders": [], "partitions": {"md126p1": {"dev": "259:1", "holders": []}}},
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    for node in ("sda", "sdb"):
        resolved = usage[node]
        assert resolved is not None
        assert resolved.boot is True
        assert resolved.uses == (DiskUse(kind=UseKind.MD, name="md126", mounts=("/",)),)


@pytest.mark.os_agnostic
def test_a_stacked_device_s_partition_holders_are_followed_too() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", holders=("md126",))},
        mounts=[_mount("253:0", "/var")],
        stacked={
            "md126": {"dev": "9:126", "holders": [], "partitions": {"md126p2": {"dev": "259:2", "holders": ["dm-0"]}}},
            "dm-0": {"dev": "253:0", "dm_name": "vg0-var", "dm_uuid": "LVM-", "holders": []},
        },
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert sda.uses == (DiskUse(kind=UseKind.MD, name="md126", mounts=("/var",)),)


@pytest.mark.os_agnostic
def test_a_btrfs_member_that_resolved_nothing_is_not_read_rather_than_not_mounted() -> None:
    # mountinfo names one source device of a multi-device btrfs filesystem;
    # the other member cannot be joined to that mount, so it must not read as
    # unused.
    capture = _capture(
        block={
            "sdb": _disk("8:16", partitions={"sdb1": {"dev": "8:17", "holders": []}}),
            "sdc": _disk("8:32", partitions={"sdc1": {"dev": "8:33", "holders": []}}),
        },
        mounts=[{"dev": "0:40", "mountpoint": "/", "fstype": "btrfs", "source_dev": "8:17"}],
        signatures={"8:17": {"fs_type": "btrfs"}, "8:33": {"fs_type": "btrfs"}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sdb = usage["sdb"]
    assert sdb is not None
    assert sdb.boot is True
    assert usage["sdc"] is None


@pytest.mark.os_agnostic
def test_lvm_without_a_mapping_name_falls_back_to_the_kernel_name() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda2": {"dev": "8:2", "holders": ["dm-0"]}})},
        stacked={"dm-0": {"dev": "253:0", "dm_uuid": "LVM-", "holders": []}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert sda.uses == (DiskUse(kind=UseKind.LVM, name="dm-0"),)


@pytest.mark.os_agnostic
def test_swap_on_a_crypt_mapping_reads_swap_below_the_crypt_layer() -> None:
    capture = _capture(
        block={"sda": _disk("8:0", partitions={"sda3": {"dev": "8:3", "holders": ["dm-1"]}})},
        swaps=[{"path": "/dev/dm-1", "dev": "253:1"}],
        stacked={"dm-1": {"dev": "253:1", "dm_name": "cryptswap", "dm_uuid": "CRYPT-", "holders": []}},
    )
    usage = resolve_usage(capture, BARE_METAL)
    sda = usage["sda"]
    assert sda is not None
    assert sda.uses == (DiskUse(kind=UseKind.CRYPT, name="cryptswap", mounts=("swap",)),)


@pytest.mark.os_agnostic
def test_an_esp_mounted_at_efi_marks_its_disk_boot() -> None:
    capture = _capture(
        block={"nvme0n1": _disk("259:0", partitions={"nvme0n1p1": {"dev": "259:1", "holders": []}})},
        mounts=[_mount("259:1", "/efi", fstype="vfat")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    nvme0n1 = usage["nvme0n1"]
    assert nvme0n1 is not None
    assert nvme0n1.boot is True


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("block", "extra"),
    [
        pytest.param(_disk("8:0", partitions={"sda1": {"holders": []}}), {}, id="partition-dev-unread"),
        pytest.param({"holders": [], "partitions": {}}, {}, id="disk-dev-unread"),
        pytest.param({"dev": "8:0", "holders": []}, {}, id="partitions-unread"),
        pytest.param(_disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}}), {"swaps": None}, id="swaps"),
    ],
)
def test_an_unread_source_makes_an_otherwise_empty_disk_not_read(block: dict[str, Any], extra: dict[str, Any]) -> None:
    capture = _capture(block={"sda": block}, **extra)
    assert resolve_usage(capture, BARE_METAL) == {"sda": None}


@pytest.mark.os_agnostic
def test_the_control_for_the_unread_sources_is_not_mounted() -> None:
    capture = _capture(block={"sda": _disk("8:0", partitions={"sda1": {"dev": "8:1", "holders": []}})})
    usage = resolve_usage(capture, BARE_METAL)
    assert usage["sda"] is not None
    assert usage["sda"].uses == ()
