"""The Filesystem value: how full it is, the way df computes it."""

from __future__ import annotations

import math

import pytest

from lsdsk.domain.enums import FilesystemKind, InodeTable
from lsdsk.domain.models import Filesystem, Inventory


def _fs(**fields: object) -> Filesystem:
    return Filesystem.model_validate({"kind": FilesystemKind.MOUNT, "name": "/", **fields})


@pytest.mark.parametrize(
    ("used", "free", "expected"),
    [(0, 100, 0), (1, 199, 1), (50, 50, 50), (951, 49, 96), (100, 0, 100), (None, 5, None), (0, 0, None)],
)
def test_used_percent_rounds_up_the_way_df_does(used: int | None, free: int | None, expected: int | None) -> None:
    assert _fs(used_bytes=used, free_bytes=free).used_percent == expected


def test_used_percent_is_exact_for_byte_counts_a_float_cannot_hold() -> None:
    # 100 * used / total is 50 plus 2**-56 here: float division rounds it to
    # exactly 50, so a float ceiling answers 50 where df, in integers, says 51.
    quantum = 2**56
    total = 100 * quantum - 1
    used = 50 * quantum
    assert math.ceil(100 * used / total) == 50
    assert _fs(used_bytes=used, free_bytes=total - used).used_percent == 51


def test_only_a_fixed_inode_table_reports_inode_usage() -> None:
    ext4 = _fs(fs_type="ext4", inodes_total=200, inodes_free=50)
    zfs = _fs(fs_type="zfs", inodes_total=200, inodes_free=50)
    assert (ext4.inode_table, ext4.inodes_used_percent) == (InodeTable.FIXED, 75)
    assert (zfs.inode_table, zfs.inodes_used_percent) == (InodeTable.DYNAMIC, None)


@pytest.mark.parametrize(
    "figures",
    [{}, {"inodes_total": 200}, {"inodes_free": 5}, {"inodes_total": 0, "inodes_free": 0}],
)
def test_a_fixed_table_without_usable_figures_reports_no_inode_usage(figures: dict[str, int]) -> None:
    fs = _fs(fs_type="ext4", **figures)
    assert fs.inode_table is InodeTable.FIXED
    assert fs.inodes_used_percent is None


def test_inode_usage_rounds_up() -> None:
    assert _fs(fs_type="ext4", inodes_total=1000, inodes_free=999).inodes_used_percent == 1


def test_an_inventory_older_than_the_field_has_not_read_filesystems() -> None:
    inventory = Inventory(hostname="h")
    assert inventory.filesystems is None
    assert inventory.unread_filesystems == ()
