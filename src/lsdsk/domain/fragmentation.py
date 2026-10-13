"""Score how scattered free space is, the way ZFS scores a metaslab.

A filesystem whose free space lies in a few long runs can place a large file
in one piece; one whose free space is many short runs cannot. ZFS turns that
into one number per pool (``zpool list`` FRAG) by weighting the free space in
each power-of-two size bucket with a fixed table. lsdsk reads that number for
a pool and computes the same number, from the same table, for every other
filesystem whose free extents it can count, so one column means one thing.

System Role:
    Domain layer. Pure: a histogram in, a percentage out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

#: OpenZFS ``zfs_frag_table`` (module/zfs/metaslab.c, zfs-2.4.0), one entry per
#: power-of-two free-segment size from 512 B to 512 MiB. Copied, not derived:
#: ZFS calls it a tuning choice it may change, and agreeing with ``zpool list``
#: is the point.
ZFS_FRAG_TABLE: Final[tuple[int, ...]] = (
    100,
    99,
    97,
    93,
    88,
    83,
    77,
    71,
    64,
    57,
    50,
    43,
    36,
    29,
    23,
    17,
    12,
    7,
    3,
    1,
    0,
)

#: How many size buckets a free-space histogram has: one per table entry.
FREE_SPACE_BUCKETS: Final = len(ZFS_FRAG_TABLE)

#: ``bit_length`` of the smallest size the first bucket names (512 B is 2**9).
_FIRST_BUCKET_BITS: Final = 10


def bucket_of(length: int) -> int:
    """The histogram bucket a free extent of ``length`` bytes falls in.

    Args:
        length: The extent's size in bytes.

    Returns:
        ``0`` for anything under 1 KiB, ``20`` for anything from 512 MiB up,
        otherwise the power of two the size lies above.

    Example:
        >>> bucket_of(4096), bucket_of(1), bucket_of(2**40)
        (3, 0, 20)
    """
    return min(max(length.bit_length() - _FIRST_BUCKET_BITS, 0), FREE_SPACE_BUCKETS - 1)


def fragmentation_from_histogram(histogram: Sequence[int]) -> int | None:
    """The free-space weighted mean of the ZFS table, rounded down as ZFS rounds it.

    Args:
        histogram: Free bytes per bucket, :data:`FREE_SPACE_BUCKETS` of them.

    Returns:
        A percentage from 0 (all free space in runs of 512 MiB or more) to 100
        (all of it in runs under 1 KiB); ``None`` when there is no free space,
        which has no layout to score.

    Raises:
        ValueError: The histogram does not have one entry per table entry.

    Example:
        >>> fragmentation_from_histogram([0, 0, 0, 4096] + [0] * 17)
        93
    """
    if len(histogram) != FREE_SPACE_BUCKETS:
        message = f"a free-space histogram has {FREE_SPACE_BUCKETS} buckets, not {len(histogram)}"
        raise ValueError(message)
    total = sum(histogram)
    if total <= 0:
        return None
    return sum(free * score for free, score in zip(histogram, ZFS_FRAG_TABLE, strict=True)) // total


__all__ = ["FREE_SPACE_BUCKETS", "ZFS_FRAG_TABLE", "bucket_of", "fragmentation_from_histogram"]
