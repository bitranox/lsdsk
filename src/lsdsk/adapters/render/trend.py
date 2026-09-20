"""Render what each watched counter is doing over time.

The totals a drive publishes are lifetime figures held in its own non-volatile
table, so the interesting column here is not the number but whether it moved,
how fast, and over how much of the drive's own running time.

System Role:
    Adapter-layer presentation. Every judgement shown comes from
    ``lsdsk.domain.history``; nothing is decided here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ...domain.enums import Align
from ...domain.history import CounterKind, Trend, TrendVerdict, identity_of, trend_for
from ..config.tunables import DEFAULT_PIPED_WIDTH, DEFAULT_WEAR_ROW_FLOOR_PERCENT
from . import theme
from .layout import Column, fit, natural_widths

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...domain.history import DiskSeries, History
    from ...domain.models import Disk, Inventory
    from .rows import Row

DEFAULT_WIDTH = DEFAULT_PIPED_WIDTH

WEAR_WORTH_PLANNING_PERCENT = DEFAULT_WEAR_ROW_FLOOR_PERCENT

# Counters worth a row. Wear and bytes written are shown because their rate is
# how a replacement gets planned; the rest are faults.
WATCHED: tuple[CounterKind, ...] = (
    CounterKind.CRC_ERRORS,
    CounterKind.REALLOCATED_SECTORS,
    CounterKind.PENDING_SECTORS,
    CounterKind.UNCORRECTABLE_SECTORS,
    CounterKind.MEDIA_ERRORS,
    CounterKind.ERROR_LOG_ENTRIES,
    CounterKind.PERCENT_USED,
)

COUNTER_LABELS: dict[CounterKind, str] = {
    CounterKind.CRC_ERRORS: "interface CRC",
    CounterKind.REALLOCATED_SECTORS: "reallocated",
    CounterKind.PENDING_SECTORS: "pending",
    CounterKind.UNCORRECTABLE_SECTORS: "uncorrectable",
    CounterKind.MEDIA_ERRORS: "media errors",
    CounterKind.ERROR_LOG_ENTRIES: "nvme error log",
    CounterKind.PERCENT_USED: "wear",
}

VERDICT_WORDS: dict[TrendVerdict, str] = {
    TrendVerdict.RISING: "rising",
    TrendVerdict.QUIET: "no new",
    TrendVerdict.TOO_CLOSE: "too soon to say",
    TrendVerdict.FIRST_SAMPLE: "first sample",
    TrendVerdict.RESET: "counter reset",
}

TREND_COLUMNS: tuple[Column, ...] = (
    Column("device", "device", priority=0),
    Column("counter", "counter", priority=0),
    Column("total", "total", align=Align.RIGHT, priority=0),
    Column("change", "change", align=Align.RIGHT, priority=1),
    # "span" rather than "over": the figure is the window the change covers,
    # measured per counter, so one drive shows a different one on every row.
    # Headed "over" it was read as the drive's total power-on hours, which made
    # three rows for one NVMe drive look like three answers to one question.
    Column("span", "span", align=Align.RIGHT, priority=1),
    Column("rate", "per hour", align=Align.RIGHT, priority=2),
    Column("verdict", "verdict", priority=0, flexible=True, min_width=14),
)


def format_rate(per_hour: float) -> str:
    """Render a rate, dropping a decimal that would be noise.

    Args:
        per_hour: The rate against the drive's own power-on hours.

    Returns:
        The figure as a reader would quote it.
    """
    if per_hour >= 10:  # noqa: PLR2004 - a tenth of an error an hour is meaningless above ten
        return f"{per_hour:.0f}"
    if per_hour >= 0.1:  # noqa: PLR2004 - below this a fixed decimal reads as zero
        return f"{per_hour:.1f}"
    return f"{per_hour:.3f}"


def verdict_style(verdict: TrendVerdict) -> str:
    """Colour a verdict by what it means for the reader.

    Args:
        verdict: What the samples supported.

    Returns:
        The style, blank where the verdict carries no weight.
    """
    if verdict is TrendVerdict.RISING:
        return theme.STYLE_FAILING
    if verdict is TrendVerdict.QUIET:
        return theme.STYLE_AT_CAPABILITY
    return theme.STYLE_UNKNOWN


def verdict_text(trend: Trend) -> str:
    """Say what the samples support, in words rather than a code.

    A refusal says why it is refusing. "Too soon to say" with nothing after it
    reads as a shrug, when the actual reason is that this drive errors slowly
    enough that the span so far could not have shown anything.

    Args:
        trend: The verdict and the span it rests on.

    Returns:
        The sentence, carrying its reason when it is a refusal.
    """
    word = VERDICT_WORDS[trend.verdict]
    if trend.verdict is TrendVerdict.QUIET and trend.expected_from_lifetime is not None:
        return f"{word} in {trend.span_hours}h, {trend.expected_from_lifetime:.0f} were due"
    if trend.verdict is TrendVerdict.TOO_CLOSE and trend.expected_from_lifetime is not None:
        return f"{word}, {_why_too_close(trend.expected_from_lifetime, trend.span_hours)}"
    return word


def _why_too_close(expected: float, span_hours: int | None) -> str:
    """Say why the span proves nothing, in terms that survive rounding.

    The expectation is a rate times a span, so it is legitimately fractional and
    is usually far below one - a drive with a single lifetime CRC error in 42278
    hours predicts 0.000024 of one across an hour. Printed to a decimal place
    that becomes "only 0.0 were due", which states that none were due and makes
    "only" contradict its own number. The two cases also differ: no elapsed time
    is not a statement about the drive's rate at all.
    """
    if not span_hours:
        return "no power-on hours have passed since this counter last moved"
    if expected < 1:
        return f"this drive's rate would not have produced even one in {span_hours}h"
    return f"only {expected:.1f} were due"


def worth_showing(kind: CounterKind, trend: Trend, wear_floor: int = WEAR_WORTH_PLANNING_PERCENT) -> bool:
    """Whether a counter has anything to say worth a line of the reader's time.

    A counter that has never fired is not news, and wear is not a fault: every
    healthy drive wears, so a young one sitting at 1% with nothing measurable
    yet produced a row per drive that said "too soon to say" and pushed the
    drive actually failing off the top of the screen. Wear earns its row once
    there is a rate to project from, or once it is far enough along to plan a
    replacement around.

    Args:
        kind: Which counter this is.
        trend: What the samples support saying about it.
        wear_floor: The wear percentage above which a drive earns a row on its own.

    Returns:
        Whether to render the row.

    Example:
        >>> quiet_wear = Trend(
        ...     kind=CounterKind.PERCENT_USED,
        ...     verdict=TrendVerdict.TOO_CLOSE,
        ...     latest=1,
        ...     delta=0,
        ...     span_hours=16,
        ...     per_hour=None,
        ...     expected_from_lifetime=0.0,
        ... )
        >>> worth_showing(CounterKind.PERCENT_USED, quiet_wear)
        False
    """
    if trend.latest is None:
        return False
    if kind is CounterKind.PERCENT_USED:
        return trend.is_rising or trend.latest >= wear_floor
    return trend.latest > 0


def trend_row(device: str, kind: CounterKind, trend: Trend) -> Row:
    """One rendered row.

    Args:
        device: The drive the row is about.
        kind: Which counter it reports.
        trend: What the samples say about it.

    Returns:
        One cell per column, in the table's own order.
    """
    style = verdict_style(trend.verdict)
    return {
        "device": (device, "bold"),
        "counter": (COUNTER_LABELS[kind], ""),
        "total": ("-" if trend.latest is None else str(trend.latest), ""),
        "change": ("-" if trend.delta is None else f"+{trend.delta}", style if trend.delta else ""),
        "span": ("-" if trend.span_hours is None else f"{trend.span_hours}h", ""),
        "rate": ("-" if trend.per_hour is None else format_rate(trend.per_hour), style),
        "verdict": (verdict_text(trend), style),
    }


class TrendRow(NamedTuple):
    """One counter of one drive, its verdict, and the cells that render it.

    The drive is carried, not only its path: the interactive page has to answer
    "what is this row about" for the detail panel, and a path parsed back out of
    a rendered cell is a second way of knowing which drive a row is.
    """

    disk: Disk
    kind: CounterKind
    trend: Trend
    cells: Row


def trend_rows(
    inventory: Inventory,
    history: History,
    wear_floor: int = WEAR_WORTH_PLANNING_PERCENT,
) -> tuple[TrendRow, ...]:
    """Every counter worth a line, in the order both views draw them.

    One place the rows are decided, so the printed table and the interactive
    page cannot come to show different counters of the same machine.

    Args:
        inventory: The machine.
        history: What has been recorded on earlier runs.
        wear_floor: Wear below which a quiet drive earns no row.

    Returns:
        The rows, empty when nothing has been recorded yet.
    """
    rows: list[TrendRow] = []
    for disk in inventory.disks:
        series = _series_for(disk, history)
        if series is None:
            continue
        for kind in WATCHED:
            trend = trend_for(series, kind)
            if worth_showing(kind, trend, wear_floor):
                rows.append(TrendRow(disk, kind, trend, trend_row(disk.path, kind, trend)))
    return tuple(rows)


def _table(rows: Sequence[Row], title: str, width: int) -> Table:
    """Build the table, keeping only the columns that fit."""
    plain = [{key: value[0] for key, value in row.items()} for row in rows]
    widths = natural_widths(TREND_COLUMNS, plain)
    chosen = fit(TREND_COLUMNS, widths, width)
    table = Table(
        title=title,
        title_justify="left",
        title_style="bold",
        box=None,
        header_style=theme.STYLE_HEADER,
        pad_edge=False,
        padding=(0, 1),
    )
    for column in chosen:
        table.add_column(
            column.title,
            justify=Align.RIGHT.value if column.align is Align.RIGHT else Align.LEFT.value,
            no_wrap=not column.flexible,
        )
    for row in rows:
        table.add_row(*(Text(row[column.key][0], style=row[column.key][1]) for column in chosen))
    return table


def render_trend(
    inventory: Inventory,
    history: History,
    width: int = DEFAULT_WIDTH,
    wear_floor: int = WEAR_WORTH_PLANNING_PERCENT,
    store_refusal: str | None = None,
) -> Group:
    """Render every counter that has moved, or provably has not.

    Args:
        inventory: The machine.
        history: What has been recorded on earlier runs.
        width: Terminal width, which decides how many columns fit.
        wear_floor: The wear percentage above which a drive earns a row on its own.
        store_refusal: Why the counter store could not be read, when it could
            not. An unreadable store yields an empty history, which is
            indistinguishable from a machine nobody has recorded yet, so
            without this the page tells a reader the reassuring one of two
            opposite answers.

    Returns:
        The table, or an explanation of why there is nothing to show yet.
    """
    rows = [row.cells for row in trend_rows(inventory, history, wear_floor)]
    if not rows:
        return Group(Text(_nothing_yet(inventory, history, store_refusal), style=theme.STYLE_UNKNOWN))
    return Group(
        _table(rows, f"Counter trends on {inventory.hostname}", width),
        Text(""),
        Text(
            "Rates are per power-on hour of the drive itself, so a machine that "
            "spends most of its time switched off still reports a meaningful figure.",
            style=theme.STYLE_UNKNOWN,
        ),
    )


def _nothing_yet(inventory: Inventory, history: History, store_refusal: str | None = None) -> str:
    """Explain an empty trend view, saying which kind of empty it is.

    Four different situations produce no rows and they mean opposite things. A
    machine with nothing recorded yet, a machine whose store could not be read,
    a machine nobody was allowed to read, and a machine whose drives are simply
    all healthy must not share a sentence: the last one is good news and the
    rest are the absence of news.

    The refusal is answered first and quoted whole. It is the only one of the
    four that names something the reader can go and fix, and a paraphrase would
    leave them without the reader's own words to act on.
    """
    if store_refusal is not None:
        return (
            f"The counter history could not be read, so nothing here is a comparison with the past: {store_refusal}. "
            "Whatever was recorded before is still on disk and is left untouched; move it aside or point "
            "--history-file elsewhere to start a new record."
        )
    if not inventory.privileged:
        return "Counters need root or Administrator, so this run had nothing to compare."
    if not history.series:
        return (
            "No counter history recorded yet on this machine. Every run stores one reading "
            "when the drives' own clocks have advanced, so a second run an hour from now is "
            "the first that can report a rate. `lsdsk record` from a timer keeps it fed."
        )
    tracked = sum(1 for disk in inventory.disks if _series_for(disk, history) is not None)
    if not tracked:
        recorded = len(history.series)
        on_record = (
            f"{recorded} drives are on record, but none of them is attached now."
            if recorded > 1
            else "1 drive is on record, but it is not attached now."
        )
        return f"{on_record} A drive is tracked by its world-wide name, so this is a different set of disks."
    drives = "drive" if tracked == 1 else "drives"
    return (
        f"{tracked} {drives} on record and no error counter has moved: nothing to report, "
        "which is the answer you want. Wear appears here once there is a rate to project from."
    )


def _series_for(disk: Disk, history: History) -> DiskSeries | None:
    """This drive's recorded samples, if it has any."""
    identity = identity_of(disk)
    return None if identity is None else history.for_identity(identity)


__all__ = [
    "COUNTER_LABELS",
    "TREND_COLUMNS",
    "VERDICT_WORDS",
    "WATCHED",
    "WEAR_WORTH_PLANNING_PERCENT",
    "TrendRow",
    "format_rate",
    "render_trend",
    "trend_row",
    "trend_rows",
    "verdict_style",
    "verdict_text",
    "worth_showing",
]
