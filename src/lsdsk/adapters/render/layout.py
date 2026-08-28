"""Column layout for the topology tree.

Rich tables cannot span a row across columns, and a tree of independent tables
cannot align columns across branches.  The report needs both: a controller line
that runs the full width, and disk columns that line up down the entire machine
so two disks on different controllers can be compared by reading straight down.

So the widths are computed here, once, from every disk on the machine, and the
rows are emitted as pre-padded text.  That also makes truncation deliberate: the
model name gives up space first, and a row is never wrapped onto a second line.

System Role:
    Adapter layer, presentation mechanics.  Knows about widths and padding,
    nothing about hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.enums import Align

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# The gutter holds the tree glyph plus one space, so the glyph never runs
# straight into the device name. Same width on every row, including headers.
GUTTER = "   "
GAP = "  "
# ASCII on purpose: this survives a cp1252 Windows console, where a real
# ellipsis character does not.
ELLIPSIS = ">"


@dataclass(frozen=True, slots=True)
class Column:
    """One column in the aligned tree.

    Attributes:
        key: Identifier used to look a value up in a row.
        title: Header text.
        align: Which way the column's text is set.
        priority: Lower numbers survive longer as the terminal narrows.
        flexible: Whether this column gives up space before others are dropped.
        min_width: Smallest useful width for a flexible column.
        max_width: Most characters this column is ever given, however wide the
            terminal is and however long its longest value.  ``None`` lets the
            content decide.  A single NVMe WWN is five times the length of every
            SATA one beside it, so without a ceiling that one drive sets the
            width of the whole column and pushes the rest of the row off the
            page.
    """

    key: str
    title: str
    align: Align = Align.LEFT
    priority: int = 0
    flexible: bool = False
    min_width: int = 8
    max_width: int | None = None


@dataclass(frozen=True, slots=True)
class Layout:
    """The columns that fit, and the width each was given.

    The two are computed together and every renderer carries both, so they are
    one value rather than two parameters that could be passed out of step.

    Attributes:
        columns: The columns that survived fitting, in display order.
        widths: Width per column key. Keyed by whatever columns exist, so a
            mapping rather than a field per column.
    """

    columns: tuple[Column, ...]
    widths: dict[str, int]

    @classmethod
    def for_rows(cls, columns: Sequence[Column], rows: Iterable[dict[str, str]], available: int) -> Layout:
        """Measure the rows and drop the columns that do not fit.

        Args:
            columns: Every column the view could show.
            rows: The rows to measure.
            available: Terminal width.

        Returns:
            The layout to render with.
        """
        widths = natural_widths(columns, rows)
        return cls(tuple(fit(columns, widths, available)), widths)


def natural_widths(columns: Sequence[Column], rows: Iterable[dict[str, str]]) -> dict[str, int]:
    """Measure how wide each column wants to be.

    Args:
        columns: The columns to measure.
        rows: Every row that will be rendered, so widths hold for all of them.

    Returns:
        The widest content per column, header included, with any column that
        declares a ``max_width`` held at that ceiling.

    Example:
        >>> cols = [Column("a", "aa"), Column("b", "b")]
        >>> natural_widths(cols, [{"a": "x", "b": "yyyy"}])
        {'a': 2, 'b': 4}
        >>> natural_widths([Column("b", "b", max_width=2)], [{"b": "yyyy"}])
        {'b': 2}
    """
    widths = {column.key: len(column.title) for column in columns}
    for row in rows:
        for column in columns:
            widths[column.key] = max(widths[column.key], len(row.get(column.key, "")))
    for column in columns:
        if column.max_width is not None:
            widths[column.key] = min(widths[column.key], column.max_width)
    return widths


def fit(columns: Sequence[Column], widths: dict[str, int], available: int) -> list[Column]:
    """Choose which columns fit, dropping the least important first.

    Flexible columns shrink to their minimum before anything is dropped,
    because a slightly truncated model name is more useful than losing the
    temperature entirely.

    Args:
        columns: Candidate columns, in display order.
        widths: Natural widths from :func:`natural_widths`.
        available: Total width to fill.

    Returns:
        The columns that fit, still in display order.

    Example:
        >>> cols = [Column("a", "a", priority=0), Column("b", "b", priority=9)]
        >>> [c.key for c in fit(cols, {"a": 5, "b": 5}, 100)]
        ['a', 'b']
        >>> [c.key for c in fit(cols, {"a": 5, "b": 5}, 8)]
        ['a']
    """
    chosen = list(columns)
    while chosen and _required(chosen, widths) > available:
        if _shrink_one(chosen, widths):
            continue
        droppable = [column for column in chosen if column.priority > 0]
        if not droppable:
            break
        victim = max(droppable, key=lambda column: column.priority)
        chosen.remove(victim)
    return chosen


def _required(columns: Sequence[Column], widths: dict[str, int]) -> int:
    """Total width these columns need, gutter and gaps included."""
    return len(GUTTER) + sum(widths[column.key] for column in columns) + len(GAP) * len(columns)


def _shrink_one(columns: Sequence[Column], widths: dict[str, int]) -> bool:
    """Take one character from the widest flexible column, if any has slack."""
    flexible = [column for column in columns if column.flexible and widths[column.key] > column.min_width]
    if not flexible:
        return False
    widest = max(flexible, key=lambda column: widths[column.key])
    widths[widest.key] -= 1
    return True


def clip(value: str, width: int) -> str:
    """Shorten a value to a width, marking that something was cut.

    Kept apart from :func:`pad` because a caller that lays its own cells out
    needs the truncation without the padding.  Two callers, one rule: a value
    that reads as complete when it is not sends somebody looking for a drive by
    an identifier that is missing its tail.

    Args:
        value: The text to fit.
        width: The most characters to keep, marker included.

    Returns:
        ``value`` unchanged when it fits, otherwise ``width`` characters ending
        in :data:`ELLIPSIS`.

    Example:
        >>> clip("abc", 5)
        'abc'
        >>> clip("abcde", 5)
        'abcde'
        >>> clip("abcdefgh", 5)
        'abcd>'
        >>> clip("abc", 0)
        ''
    """
    if len(value) <= width:
        return value
    return value[: max(width - 1, 0)] + ELLIPSIS if width else ""


def pad(value: str, width: int, align: Align) -> str:
    """Fit one cell to a width, truncating with an ellipsis when it overflows.

    Args:
        value: The cell text.
        width: Target width.
        align: Which way the column's text is set.

    Returns:
        Text of exactly ``width`` characters.

    Example:
        >>> pad("abc", 5, Align.LEFT)
        'abc  '
        >>> pad("abc", 5, Align.RIGHT)
        '  abc'
        >>> pad("abcdefgh", 5, Align.LEFT)
        'abcd>'
    """
    value = clip(value, width)
    return value.rjust(width) if align is Align.RIGHT else value.ljust(width)


__all__ = ["ELLIPSIS", "GAP", "GUTTER", "Column", "clip", "fit", "natural_widths", "pad"]
