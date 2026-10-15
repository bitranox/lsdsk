"""The row shapes every table renderer builds.

One name for the map a renderer hands to its table builder, so the three modules
that build rows cannot each spell the shape differently and drift apart. And one
name for a row's severity marker paired with its cells, so the marker is never
smuggled through that map as a magic key a real column could collide with.

System Role:
    Adapter-layer presentation type.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from .theme import Cell

if TYPE_CHECKING:
    from collections.abc import Mapping

#: A rendered row: column key to styled cell.
Row = dict[str, Cell]


class MarkedRow(NamedTuple):
    """A table row: its severity marker, and its column-keyed cells.

    ``tables._render`` draws the marker as its own leading column, outside the
    ones a :class:`~lsdsk.adapters.render.layout.Column` describes, so it is
    carried as a named field here rather than as a ``"marker"`` entry inside
    :data:`Row` - a magic key that three producers had to remember to add and
    ``_render`` had to pull back out.

    Attributes:
        marker: The severity marker's text and style.
        cells: The row's data, keyed by column key.
        kept_tails: Per column key, the END of that cell's text which a clip
            keeps whole, cutting what stands before it instead. Carried beside
            the cell rather than recognised in its text, because the builder
            knows which characters are a count it appended and the text cannot
            say so: a mountpoint may itself end in `` +2``.
    """

    marker: Cell
    cells: Row
    kept_tails: Mapping[str, str] = MappingProxyType({})


__all__ = ["MarkedRow", "Row"]
