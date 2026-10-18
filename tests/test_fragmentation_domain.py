"""The free-space fragmentation score, held against OpenZFS's own table."""

from __future__ import annotations

import pytest

from lsdsk.domain.enums import FilesystemKind
from lsdsk.domain.fragmentation import (
    FREE_SPACE_BUCKETS,
    ZFS_FRAG_TABLE,
    bucket_of,
    fragmentation_from_histogram,
)
from lsdsk.domain.models import Filesystem, FreeSpace

_KIB, _MIB = 1024, 1024 * 1024


def _only(bucket: int, free_bytes: int) -> list[int]:
    histogram = [0] * FREE_SPACE_BUCKETS
    histogram[bucket] = free_bytes
    return histogram


def test_the_table_is_openzfs_2_4_verbatim() -> None:
    assert ZFS_FRAG_TABLE == (100, 99, 97, 93, 88, 83, 77, 71, 64, 57, 50, 43, 36, 29, 23, 17, 12, 7, 3, 1, 0)
    assert FREE_SPACE_BUCKETS == len(ZFS_FRAG_TABLE) == 21


@pytest.mark.parametrize(
    ("length", "bucket"),
    [
        (1, 0),
        (511, 0),
        (512, 0),
        (1023, 0),
        (1024, 1),
        (4 * _KIB, 3),
        (8 * _KIB - 1, 3),
        (512 * _MIB - 1, 19),
        (512 * _MIB, 20),
        (2**40, 20),
    ],
)
def test_an_extent_falls_in_the_bucket_of_its_power_of_two(length: int, bucket: int) -> None:
    assert bucket_of(length) == bucket


def test_free_space_all_in_4k_pieces_scores_93() -> None:
    assert fragmentation_from_histogram(_only(bucket_of(4 * _KIB), 10 * _MIB)) == 93


def test_free_space_all_in_512m_pieces_scores_0() -> None:
    assert fragmentation_from_histogram(_only(20, 4 * 512 * _MIB)) == 0


def test_a_mixture_is_the_free_space_weighted_mean_rounded_down() -> None:
    histogram = _only(bucket_of(4 * _KIB), 1024 * _MIB)
    histogram[20] = 1024 * _MIB
    # (93 + 0) / 2 = 46.5, floored like metaslab_set_fragmentation's integer division.
    assert fragmentation_from_histogram(histogram) == 46


def test_no_free_space_has_no_score() -> None:
    assert fragmentation_from_histogram([0] * FREE_SPACE_BUCKETS) is None


def test_a_histogram_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError, match="21 buckets"):
        fragmentation_from_histogram([1, 2, 3])


def test_the_average_free_extent_is_the_total_over_the_count() -> None:
    space = FreeSpace(
        fragmentation_percent=10, free_extent_count=4, free_extent_bytes=4096, largest_free_extent_bytes=2048
    )
    assert space.average_free_extent_bytes == 1024
    assert FreeSpace(fragmentation_percent=10).average_free_extent_bytes is None


@pytest.mark.parametrize(
    ("kind", "fs_type", "applies"),
    [
        (FilesystemKind.MOUNT, "ext4", True),
        (FilesystemKind.MOUNT, "xfs", True),
        (FilesystemKind.MOUNT, "ext3", True),  # always served by the ext4 driver, which has GETFSMAP
        (FilesystemKind.MOUNT, "ext2", False),  # served by fs/ext2, which has none, wherever that driver is built
        (FilesystemKind.MOUNT, "vfat", False),
        (FilesystemKind.MOUNT, "btrfs", False),
        (FilesystemKind.POOL, "zfs", True),
        (FilesystemKind.VOLUME, "NTFS", True),
    ],
)
def test_a_free_space_figure_applies_only_where_a_source_exists(
    kind: FilesystemKind, fs_type: str, applies: bool
) -> None:
    assert Filesystem(kind=kind, name="x", fs_type=fs_type).free_space_applies is applies


def test_a_percent_outside_0_to_100_is_refused() -> None:
    with pytest.raises(ValueError, match="less than or equal to 100"):
        FreeSpace(fragmentation_percent=101)


def test_no_free_extents_have_no_average_rather_than_a_division_by_zero() -> None:
    assert FreeSpace(free_extent_count=0, free_extent_bytes=0).average_free_extent_bytes is None


def test_an_unequal_mixture_is_weighted_by_free_space_not_by_bucket() -> None:
    # Three parts in 4 KiB pieces to one part in 512 MiB pieces: (3 * 93 + 0) / 4 = 69.75, floored to 69. An
    # unweighted mean of the two occupied buckets would say 46, which the equal-weight mixture cannot tell apart.
    histogram = _only(bucket_of(4 * _KIB), 3 * 512 * _MIB)
    histogram[20] = 512 * _MIB
    assert fragmentation_from_histogram(histogram) == 69


@pytest.mark.parametrize("histogram", [(1, 2, 3), (0,) * (FREE_SPACE_BUCKETS + 1)])
def test_a_free_space_value_refuses_a_histogram_of_another_length(histogram: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match=f"{FREE_SPACE_BUCKETS} buckets, not {len(histogram)}"):
        FreeSpace(histogram=histogram)


def test_a_free_space_value_refuses_a_negative_bucket() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        FreeSpace(histogram=(-1,) + (0,) * (FREE_SPACE_BUCKETS - 1))


@pytest.mark.parametrize("histogram", [(), (0,) * FREE_SPACE_BUCKETS, (4096,) + (0,) * (FREE_SPACE_BUCKETS - 1)])
def test_a_free_space_value_keeps_an_empty_or_a_full_length_histogram(histogram: tuple[int, ...]) -> None:
    assert FreeSpace(histogram=histogram).histogram == histogram
