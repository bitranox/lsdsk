"""In-memory elevation: never asks, never starts anything.

The testing container answers the elevation port with this, so a test built on
:func:`~lsdsk.composition.build_testing` can open the interactive view without a
Windows prompt and without a second process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.elevation import Elevation, ElevationState

if TYPE_CHECKING:
    from pathlib import Path


def elevate_in_memory(replay: Path | None, *, wanted: bool) -> Elevation:
    """Answer that nothing was asked, whatever the run wanted.

    Args:
        replay: Ignored; there is no machine to ask about.
        wanted: Ignored; nothing is ever asked here.

    Returns:
        NOT_ASKED, always.

    Example:
        >>> elevate_in_memory(None, wanted=True).state.value
        'not-asked'
    """
    del replay, wanted
    return Elevation(state=ElevationState.NOT_ASKED)


__all__ = ["elevate_in_memory"]
