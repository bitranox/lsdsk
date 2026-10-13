"""Turn a recorded free-extent summary into the domain's free-space value.

Shared by the Linux and Windows builders, which record the same summary.

System Role:
    Adapter layer, part of the pure mapping from a typed capture to the domain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.fragmentation import fragmentation_from_histogram
from ...domain.models import FreeSpace

if TYPE_CHECKING:
    from .capture import FreeExtentsEntry


def free_space_of(entry: FreeExtentsEntry | None) -> FreeSpace | None:
    """The free-space value a summary describes, ``None`` where nothing was read.

    Args:
        entry: The recorded summary, or ``None`` when the walk did not run.

    Returns:
        ``None`` when the entry is absent, refused, or carries no count; else
        the value, scored from its histogram (``None`` score where the
        histogram holds no free space).
    """
    if entry is None or entry.error is not None or entry.count is None:
        return None
    histogram = tuple(entry.histogram)
    return FreeSpace(
        fragmentation_percent=fragmentation_from_histogram(histogram) if histogram else None,
        free_extent_count=entry.count,
        free_extent_bytes=entry.total,
        largest_free_extent_bytes=entry.largest,
        histogram=histogram,
    )


__all__ = ["free_space_of"]
