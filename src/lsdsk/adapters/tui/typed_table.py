"""A typed view of the table operations this app uses.

Textual's ``DataTable.add_row`` and ``add_columns`` take ``*cells`` without a
declared element type, so under a strict type checker every call site becomes
partially unknown.  Declaring the shape actually used gives those call sites
real types without silencing the checker or pinning a stub to one Textual
release, and it documents that a cell here is Rich text: styled, because a plain
string reaches the terminal with every severity colour the render layer computed
already thrown away.

System Role:
    Adapter layer, a typing seam over a third-party widget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.text import Text
    from textual.widget import Widget
    from textual.widgets import DataTable


class RowTable(Protocol):
    """The subset of ``DataTable`` this application calls."""

    row_count: int

    def add_columns(self, *labels: str) -> object: ...

    def add_row(self, *cells: Text, key: str | None = None) -> object: ...

    def clear(self, *, columns: bool = False) -> object: ...

    # Reading rows back is how a test proves what actually reached a cell,
    # including the style, which a plain-string cell silently loses.
    @property
    def rows(self) -> Mapping[object, object]: ...

    def get_row(self, row_key: object) -> list[Text]: ...

    # By position rather than by key, for a test that walks the whole table
    # without needing to know how the rows were keyed.
    def get_row_at(self, row_index: int) -> list[Text]: ...


def rows_of(table: Widget) -> RowTable:
    """View a Textual table through the typed subset used here.

    A widget is accepted rather than ``DataTable[str]`` because a subscripted
    generic cannot be used for the runtime class check ``query_one`` performs,
    so call sites look the widget up by selector alone and narrow it here.

    Args:
        table: The widget to wrap.

    Returns:
        The same object, typed to the operations this app performs.
    """
    return cast("RowTable", table)


class RowEvent(Protocol):
    """The part of a ``DataTable`` row event this application reads."""

    @property
    def data_table(self) -> Widget: ...


def raising_table_id(event: DataTable.RowHighlighted) -> str | None:
    """Which table raised a row event.

    ``RowHighlighted.data_table`` is declared unsubscripted, so reading it
    yields ``DataTable[Unknown]`` and a strict checker rejects the access
    itself. Casting the attribute cannot help, because reading it to cast it is
    the unknown access; the EVENT is cast instead, to a protocol that declares
    the one member wanted, and only the widget identity is taken off it.

    Args:
        event: The row event.

    Returns:
        The ``id`` of the table that raised it, or ``None`` if it has none.
    """
    return cast("RowEvent", event).data_table.id


__all__ = ["RowEvent", "RowTable", "raising_table_id", "rows_of"]
