"""The Filesystem value: how full it is, the way df computes it."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    ("used", "free", "mount", "pool"),
    [(1, 199, 1, 0), (0, 100, 0, 0), (50, 50, 50, 50), (951, 49, 96, 95), (199, 1, 100, 99), (100, 0, 100, 100)],
)
def test_a_pool_rounds_down_like_zpool_where_a_mount_rounds_up_like_df(
    used: int, free: int, mount: int, pool: int
) -> None:
    same_bytes = {"used_bytes": used, "free_bytes": free}
    assert _fs(kind=FilesystemKind.MOUNT, **same_bytes).used_percent == mount
    assert _fs(kind=FilesystemKind.VOLUME, **same_bytes).used_percent == mount
    assert _fs(kind=FilesystemKind.POOL, **same_bytes).used_percent == pool


def test_a_pool_is_floored_in_exact_integers_where_a_float_would_round_it_up() -> None:
    # 100 * used / total is 50 minus 2**-56 here: float division rounds it to
    # exactly 50, so a float floor answers 50 where zpool, in integers, says 49.
    quantum = 2**56
    total = 100 * quantum + 1
    used = 50 * quantum
    assert math.floor(100 * used / total) == 50
    assert _fs(kind=FilesystemKind.POOL, used_bytes=used, free_bytes=total - used).used_percent == 49


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


@pytest.mark.parametrize("field", ["size_bytes", "used_bytes", "free_bytes", "inodes_total", "inodes_free"])
def test_a_negative_figure_is_refused_where_the_filesystem_is_built(field: str) -> None:
    """A count of bytes or inodes cannot be negative, so the value refuses one.

    Every builder clamps its figures, so this is the domain's own guard: a
    builder that stopped clamping would be refused at the construction that
    made the figure, rather than printing a negative percentage in a table.
    """
    with pytest.raises(ValidationError, match=rf"{field}\n  Input should be greater than or equal to 0"):
        _fs(**{field: -1})


@pytest.mark.parametrize("field", ["size_bytes", "used_bytes", "free_bytes", "inodes_total", "inodes_free"])
def test_a_zero_figure_is_a_reading(field: str) -> None:
    """The control: the bound refuses below zero, not zero itself."""
    assert getattr(_fs(**{field: 0}), field) == 0
