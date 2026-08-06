"""The row shape every table renderer builds.

One name for the map a renderer hands to its table builder, so the three modules
that build rows cannot each spell the shape differently and drift apart.

System Role:
    Adapter-layer presentation type.
"""

from __future__ import annotations

from .theme import Cell

#: A rendered row: column key to styled cell.
Row = dict[str, Cell]

__all__ = ["Row"]
