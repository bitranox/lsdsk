"""Fold free extents into the bounded summary a capture records.

Both platforms walk a filesystem's free space as a stream of runs - Linux
through FS_IOC_GETFSMAP, Windows through the volume's cluster bitmap - and
both record the same small summary rather than the runs themselves, so a
capture stays small and carries nothing that identifies a file.

System Role:
    Adapter layer, pure decoding shared by both platform readers.
"""

from __future__ import annotations

from typing import Final

from ....domain.fragmentation import FREE_SPACE_BUCKETS, bucket_of

#: How many free extents one filesystem may contribute before the reading
#: stops. A reading budget, not a judged figure: a filesystem past it is
#: refused with this number in the reason rather than walked for minutes.
MAX_FREE_EXTENTS: Final = 1_000_000
#: The largest total a capture records: its byte figures are unsigned 64-bit.
_MAX_TOTAL: Final = 2**64 - 1


class FreeExtentCapError(ValueError):
    """The walk met more free extents than :data:`MAX_FREE_EXTENTS` allows."""

    def __init__(self, cap: int) -> None:
        """Name the cap in the message, which becomes the refusal's reason."""
        super().__init__(f"more than {cap:,} free extents; stopped counting at the cap")


class FreeExtentTally:
    """Count, sum and bucket free extents, refusing to grow past a cap.

    Example:
        >>> tally = FreeExtentTally()
        >>> tally.add(4096)
        >>> tally.summary()["count"]
        1
    """

    def __init__(self, cap: int = MAX_FREE_EXTENTS) -> None:
        """Start empty, with ``cap`` extents allowed."""
        self._cap = cap
        self._count = 0
        self._total = 0
        self._largest = 0
        self._histogram = [0] * FREE_SPACE_BUCKETS

    def add(self, length: int) -> None:
        """Fold in one free extent of ``length`` bytes; an empty one is skipped.

        Raises:
            FreeExtentCapError: This extent would be one more than the cap.
            ValueError: This extent would take the total past what a capture
                records, which would refuse the whole run rather than this
                filesystem.
        """
        if length <= 0:
            return
        if self._count >= self._cap:
            raise FreeExtentCapError(self._cap)
        if self._total + length > _MAX_TOTAL:
            message = f"the free extents add up to more than {_MAX_TOTAL:,} bytes; stopped counting"
            raise ValueError(message)
        self._count += 1
        self._total += length
        self._largest = max(self._largest, length)
        self._histogram[bucket_of(length)] += length

    def summary(self) -> dict[str, int | list[int]]:
        """The capture's record of the walk: count, total, largest and histogram."""
        return {
            "count": self._count,
            "total": self._total,
            "largest": self._largest,
            "histogram": list(self._histogram),
        }


__all__ = ["MAX_FREE_EXTENTS", "FreeExtentCapError", "FreeExtentTally"]
