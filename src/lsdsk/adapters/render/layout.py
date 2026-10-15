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

#: The tree's rules, in one place for both trees that draw them. Box drawing
#: rather than ASCII because a rule should look like one, and safe on a legacy
#: console because `safe_console.ASCII_FALLBACKS` maps each glyph to the ASCII
#: character it replaced - ONE for one, so the spine keeps its width there.
#: A branch and a last turn are two characters; a continuing rule and the dead
#: space where a rule stops are the same width, which is what lets a row's legs
#: be sliced level by level.
TREE_BRANCH = "\u251c\u2500"
TREE_LAST = "\u2514\u2500"
TREE_PIPE = "\u2502 "
TREE_STOP = "  "
#: The turn where a row's own rule goes DOWN to the block it carries, and the
#: leader that runs from any row's glyph to the columns after the spine. The
#: turn sits exactly above the first child's glyph, so a parent says on its own
#: line that something hangs below it rather than leaving it to be read off the
#: next line's indentation.
TREE_DOWN = "\u252c"
TREE_LEAD = "\u2500"


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
        shrinks_last: Whether a flexible column gives up space only once no
            droppable column is left. For a cell whose every part is a fact of
            its own - the list of disks a filesystem lives on - cutting it
            hides one of them, which costs more than any column that could
            still be dropped instead.
        never_dropped: Whether the column outlives even the one-line reduction
            of :meth:`Layout.on_one_line`, which otherwise drops whole columns
            from the right once the undroppable ones overrun. For a column a row
            answers nothing without - how full a filesystem is, and which disks
            it lives on - a cut value still answers part of the question and a
            missing one answers none of it, so a flexible one is cut instead.
    """

    key: str
    title: str
    align: Align = Align.LEFT
    priority: int = 0
    flexible: bool = False
    min_width: int = 8
    max_width: int | None = None
    shrinks_last: bool = False
    never_dropped: bool = False


@dataclass(frozen=True, slots=True)
class Layout:
    """The columns that fit, and the width each was given.

    The two are computed together and every renderer carries both, so they are
    one value rather than two parameters that could be passed out of step.

    Attributes:
        columns: The columns that survived fitting, in display order.
        widths: Width per column key. Keyed by whatever columns exist, so a
            mapping rather than a field per column.
        bandwidth: Whether the widths were measured for rows whose link figures
            carry what they are worth. Carried HERE, with the widths it was
            measured against, because the row builder has to produce the same
            form: asked twice - once to measure and once to draw - the two
            answers can differ, and the difference is a clipped link figure,
            which is a different figure rather than a shorter one.
    """

    columns: tuple[Column, ...]
    widths: dict[str, int]
    bandwidth: bool = False

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

    @classmethod
    def preferring(
        cls,
        columns: Sequence[Column],
        rich: Sequence[dict[str, str]],
        plain: Sequence[dict[str, str]],
        available: int,
    ) -> Layout:
        """Take the fuller rows where the width affords them, the plain ones where it does not.

        A link figure may carry what it is worth, and that detail is the FIRST
        thing a narrow terminal gives up - before a column is dropped. It is an
        embellishment on a figure; a column is a fact the tool was asked for.
        A flexible column still narrows to its ``min_width`` to make room for
        it, as it would for any other column; a value that must stay whole
        while anything droppable stands is marked ``shrinks_last``, and then
        the fuller rows cost a column first and are refused.

        Two conditions, and the second is the one that is easy to miss: the
        fuller rows are taken only if they cost no column AND if they actually
        fit. :func:`fit` cannot drop a column of priority 0, so when the
        undroppable columns alone overrun the width it stops and hands back a
        set that does NOT fit - which looks like "nothing was lost" while the
        row runs off the side. ``link`` is priority 0 in every disk table here,
        so that is not a hypothetical.

        Args:
            columns: Every column the view could show.
            rich: The rows with the detail.
            plain: The same rows without it.
            available: Terminal width.

        Returns:
            The layout, carrying on :attr:`bandwidth` which form it chose.

        Example:
            >>> cols = [Column("a", "a", priority=0), Column("b", "b", priority=0)]
            >>> full = [{"a": "x", "b": "yyyyyyyyyy"}]
            >>> bare = [{"a": "x", "b": "y"}]
            >>> Layout.preferring(cols, full, bare, 100).bandwidth
            True
            >>> Layout.preferring(cols, full, bare, 8).bandwidth
            False
        """
        plain_layout = cls.for_rows(columns, plain, available)
        rich_layout = cls.for_rows(columns, rich, available)
        kept_the_same = [column.key for column in rich_layout.columns] == [
            column.key for column in plain_layout.columns
        ]
        if kept_the_same and rich_layout.required() <= available:
            return cls(rich_layout.columns, rich_layout.widths, bandwidth=True)
        return plain_layout

    def required(self) -> int:
        """Total width this layout needs, gutter and gaps included."""
        return _required(self.columns, self.widths)

    def on_one_line(self, available: int) -> Layout:
        """The same layout cut down until a row fits on one line.

        :func:`fit` stops at the columns it may not drop, so below some width
        those alone overrun and the row wraps, leaving its severity marker on a
        line of its own. A row is one line at every width instead. :func:`fit`
        has already shrunk each flexible column to its ``min_width``, the
        smallest width it declares useful, so below that it goes whole rather
        than survive as a stub; then whole columns go from the right, because a
        figure cut short is a different figure rather than a shorter one; and
        the first column, which names the row, stays and is clipped to
        whatever is left. A column marked ``never_dropped`` survives both
        steps, and where the clipped first column still leaves the row too
        wide, a flexible one of those gives up characters next, down to the
        clip mark and one character.

        Args:
            available: Width the row may take.

        Returns:
            This layout when it already fits, otherwise the reduced one.

        Example:
            >>> cols = [Column("a", "a"), Column("b", "b", flexible=True, min_width=6), Column("c", "c")]
            >>> layout = Layout(tuple(cols), {"a": 8, "b": 6, "c": 6})
            >>> layout.on_one_line(100) is layout
            True
            >>> fitted = layout.on_one_line(21)
            >>> [column.key for column in fitted.columns], fitted.required() <= 21
            (['a', 'c'], True)
            >>> narrowest = layout.on_one_line(8)
            >>> [column.key for column in narrowest.columns], narrowest.widths["a"] < 8, narrowest.required() <= 8
            (['a'], True, True)
            >>> kept = [Column("a", "a"), Column("b", "b"), Column("c", "c", flexible=True, never_dropped=True)]
            >>> pinned = Layout(tuple(kept), {"a": 8, "b": 6, "c": 9}).on_one_line(16)
            >>> [column.key for column in pinned.columns], pinned.widths["a"], pinned.widths["c"], pinned.required()
            (['a', 'c'], 2, 7, 16)
        """
        if self.required() <= available:
            return self
        columns = list(self.columns)
        widths = dict(self.widths)
        for column in reversed(self.columns[1:]):
            if column.flexible and not column.never_dropped and _required(columns, widths) > available:
                columns.remove(column)
        droppable = [column for column in columns[1:] if not column.never_dropped]
        while droppable and _required(columns, widths) > available:
            columns.remove(droppable.pop())
        first = columns[0].key
        widths[first] = max(widths[first] - max(_required(columns, widths) - available, 0), _CLIPPED)
        for column in reversed(columns[1:]):
            if column.flexible and column.never_dropped:
                excess = max(_required(columns, widths) - available, 0)
                widths[column.key] = max(widths[column.key] - excess, _CLIPPED)
        return Layout(tuple(columns), widths, bandwidth=self.bandwidth)


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
    temperature entirely. A column marked ``shrinks_last`` is the exception: it
    shrinks only once every droppable column is gone.

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
        >>> last = [Column("a", "a", flexible=True, min_width=2, shrinks_last=True), Column("b", "b", priority=9)]
        >>> narrowed = {"a": 9, "b": 5}
        >>> [c.key for c in fit(last, narrowed, 16)], narrowed["a"]
        (['a'], 9)
        >>> [c.key for c in fit(last, narrowed, 10)], narrowed["a"]
        (['a'], 5)
    """
    chosen = list(columns)
    while chosen and _required(chosen, widths) > available:
        if _shrink_one(chosen, widths):
            continue
        droppable = [column for column in chosen if column.priority > 0]
        if not droppable:
            if _shrink_one(chosen, widths, last=True):
                continue
            break
        victim = max(droppable, key=lambda column: column.priority)
        chosen.remove(victim)
    return chosen


#: The narrowest a clipped cell gets: one character and the clip mark.
_CLIPPED = 2


def _required(columns: Sequence[Column], widths: dict[str, int]) -> int:
    """Total width these columns need, gutter and gaps included."""
    return len(GUTTER) + sum(widths[column.key] for column in columns) + len(GAP) * len(columns)


def _shrink_one(columns: Sequence[Column], widths: dict[str, int], *, last: bool = False) -> bool:
    """Take one character from the widest flexible column, if any has slack.

    ``last`` picks which flexible columns may give it: the ordinary ones, or
    those that shrink only once nothing droppable is left.
    """
    flexible = [
        column
        for column in columns
        if column.flexible and column.shrinks_last is last and widths[column.key] > column.min_width
    ]
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


def clip_keeping(value: str, width: int, tail: str) -> str:
    """Shorten a value to a width, cutting what stands before ``tail`` and keeping ``tail`` whole.

    For a cell that is a name followed by a count of something else, such as
    a mountpoint and how many other mountpoints the filesystem has: cut from
    the end, the count is the first thing to go, and it goes exactly when the
    name is long. Where the width leaves no room for even one character of the
    rest and its mark, or the value does not end in ``tail``, it is clipped as
    :func:`clip` would.

    Args:
        value: The text to fit, ending in ``tail``.
        width: The most characters to keep, marker included.
        tail: The end of ``value`` to keep whole; empty keeps nothing.

    Returns:
        ``value`` unchanged when it fits, otherwise ``width`` characters with
        the clip mark before ``tail``.

    Example:
        >>> clip_keeping("/srv/long-name +2", 12, " +2")
        '/srv/lon> +2'
        >>> clip_keeping("/srv +2", 12, " +2")
        '/srv +2'
        >>> clip_keeping("/srv/long-name +2", 4, " +2")
        '/sr>'
        >>> clip_keeping("/srv/long-name", 8, "")
        '/srv/lo>'
    """
    if len(value) <= width or not tail or not value.endswith(tail) or width - len(tail) < _CLIPPED:
        return clip(value, width)
    return clip(value[: -len(tail)], width - len(tail)) + tail


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


__all__ = [
    "ELLIPSIS",
    "GAP",
    "GUTTER",
    "TREE_BRANCH",
    "TREE_DOWN",
    "TREE_LAST",
    "TREE_LEAD",
    "TREE_PIPE",
    "TREE_STOP",
    "Column",
    "Layout",
    "clip",
    "clip_keeping",
    "fit",
    "natural_widths",
    "pad",
]
