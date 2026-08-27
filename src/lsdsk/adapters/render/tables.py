"""Focused tables for the controller, disk and health views.

Each answers a single question, so its columns can be more specific than the
combined tree affords.

They drop columns by priority as the terminal narrows, using the same fitting
rules as the tree. Twelve columns do not fit an eighty-column terminal by any
arrangement, and a table that squeezes every column down to two characters has
kept all of its data and lost all of its meaning.

System Role:
    Adapter layer, presentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

from ...domain.diagnostics import attached_demand_gbytes
from ...domain.enums import Align
from ...domain.history import CounterKind, identity_of, trend_for
from ..config.tunables import DEFAULT_PIPED_WIDTH
from . import theme
from .layout import Column, fit, natural_widths
from .report import disk_row, virtual_note, worst_severity

# A single character, because these columns are already the first to be dropped
# when the terminal narrows and a word would cost one of them.
RISING_MARK = "+"

# Which counters the legend looks at before deciding to appear. Wear is not
# among them and cannot be: it renders through `theme.format_wear` rather than
# `counter_cell`, so it is never marked, which is right because wear rises on
# every healthy drive and marking it would flag the whole fleet.
ANNOTATED_COUNTERS: tuple[CounterKind, ...] = (
    CounterKind.REALLOCATED_SECTORS,
    CounterKind.PENDING_SECTORS,
    CounterKind.UNCORRECTABLE_SECTORS,
    CounterKind.CRC_ERRORS,
    CounterKind.MEDIA_ERRORS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...domain.history import DiskSeries, History, Trend
    from ...domain.models import Disk, Finding, Inventory, PcieLink
    from .rows import Row
    from .theme import Cell

# Width assumed when the output is not going to a terminal.
DEFAULT_WIDTH = DEFAULT_PIPED_WIDTH


CONTROLLER_COLUMNS: tuple[Column, ...] = (
    Column("address", "address", priority=0),
    Column("controller", "controller", priority=0, flexible=True, min_width=16),
    Column("driver", "driver", priority=4),
    Column("firmware", "firmware", priority=5),
    Column("running", "running", priority=1),
    Column("capable", "capable", priority=2),
    Column("ports", "ports", align=Align.RIGHT, priority=3),
    Column("free", "free", align=Align.RIGHT, priority=3),
    Column("disks", "disks", align=Align.RIGHT, priority=1),
    Column("load", "load", align=Align.RIGHT, priority=2),
)

DISK_COLUMNS: tuple[Column, ...] = (
    Column("device", "device", priority=0),
    Column("model", "model", priority=0, flexible=True, min_width=14),
    Column("wwn", "wwn", priority=3, flexible=True, min_width=12),
    Column("serial", "serial", priority=5, flexible=True, min_width=8),
    Column("firmware", "firmware", priority=6),
    Column("size", "size", align=Align.RIGHT, priority=4),
    Column("kind", "kind", priority=7),
    Column("bus", "bus", priority=6),
    Column("port", "port", priority=1),
    Column("disk", "disk", priority=1),
    Column("link", "link", priority=0),
    Column("controller", "controller", priority=2, flexible=True, min_width=12),
)

HEALTH_COLUMNS: tuple[Column, ...] = (
    Column("device", "device", priority=0),
    Column("model", "model", priority=2, flexible=True, min_width=14),
    Column("temp", "temp", align=Align.RIGHT, priority=1),
    Column("worn", "worn", align=Align.RIGHT, priority=0),
    Column("hours", "hours", align=Align.RIGHT, priority=3),
    Column("written", "written", align=Align.RIGHT, priority=4),
    Column("realloc", "realloc", align=Align.RIGHT, priority=1),
    Column("pending", "pending", align=Align.RIGHT, priority=1),
    Column("uncorr", "uncorr", align=Align.RIGHT, priority=1),
    Column("crc", "crc", align=Align.RIGHT, priority=1),
    Column("media", "media", align=Align.RIGHT, priority=2),
)


def _render(title: str, columns: Sequence[Column], rows: Sequence[Row], width: int, caption: str = "") -> Table:
    """Build a table from styled rows, keeping only the columns that fit.

    A caption goes under the table rather than into a row: a row would claim to
    be a device and would carry a value in every column it does not have.
    """
    plain = [{key: value[0] for key, value in row.items()} for row in rows]
    widths = natural_widths(columns, plain)
    chosen = fit(columns, widths, width)

    table = Table(
        title=title,
        title_justify="left",
        title_style="bold",
        caption=caption or None,
        caption_justify="left",
        caption_style=theme.STYLE_UNKNOWN,
        box=None,
        header_style=theme.STYLE_HEADER,
        pad_edge=False,
        padding=(0, 1),
    )
    table.add_column("", width=2, no_wrap=True)
    for column in chosen:
        justify = Align.RIGHT.value if column.align is Align.RIGHT else Align.LEFT.value
        table.add_column(
            column.title,
            justify=justify,
            no_wrap=True,
            overflow="ellipsis",
            max_width=widths[column.key],
        )
    for row in rows:
        marker_text, marker_style = row.get("marker", ("", ""))
        cells = [Text(*row.get(column.key, ("-", theme.STYLE_UNKNOWN))) for column in chosen]
        table.add_row(Text(marker_text, style=marker_style), *cells)
    return table


def _pcie_text(link: PcieLink) -> str:
    """Render a running PCIe link as generation and width."""
    return theme.format_pcie_decimal(link.current_speed_gtps, link.current_width)


def _pcie_capability_text(link: PcieLink) -> str:
    """Render what a PCIe link could do at best."""
    return theme.format_pcie_decimal(link.max_speed_gtps, link.max_width)


def render_controllers(inventory: Inventory, findings: Sequence[Finding], width: int = DEFAULT_WIDTH) -> Table:
    """Render one row per storage controller.

    Args:
        inventory: The machine.
        findings: The findings, used to mark affected rows.
        width: Terminal width, which decides how many columns fit.

    Returns:
        A table of controllers.
    """
    rows: list[Row] = []
    for controller in inventory.controllers:
        severity = worst_severity(findings, controller.address)
        demand = attached_demand_gbytes(controller, inventory)
        running = _pcie_text(controller.link)
        capable = _pcie_capability_text(controller.link)
        rows.append(
            {
                "marker": (theme.marker_for(severity), theme.style_for(severity)),
                "address": (controller.address, theme.STYLE_IDENTIFIER),
                "controller": (controller.name, ""),
                "driver": (controller.driver or "-", "" if controller.driver else theme.STYLE_UNKNOWN),
                "firmware": (controller.firmware or "-", "" if controller.firmware else theme.STYLE_UNKNOWN),
                "running": (running, "" if running == capable else theme.STYLE_BELOW_CAPABILITY),
                "capable": (capable, ""),
                "ports": ("-" if controller.port_count is None else str(controller.port_count), ""),
                "free": ("-" if controller.ports_free is None else str(controller.ports_free), ""),
                "disks": (str(len(inventory.disks_on(controller.address))), ""),
                "load": (
                    "-" if demand is None else f"{demand:.2f} GB/s",
                    theme.STYLE_UNKNOWN if demand is None else "",
                ),
            }
        )
    return _render(f"Controllers on {inventory.hostname}", CONTROLLER_COLUMNS, rows, width)


def render_disks(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int = DEFAULT_WIDTH,
    *,
    expand_virtual: bool = False,
) -> Table:
    """Render one row per disk, with identity and interface speeds.

    Args:
        inventory: The machine.
        findings: The findings, used to mark affected rows.
        width: Terminal width, which decides how many columns fit.
        expand_virtual: Give every kernel-virtual device a row of its own.
            Folded into a caption otherwise, on the same rule the tree uses, so
            the two views cannot describe one machine differently.

    Returns:
        A table of disks.
    """
    rows: list[Row] = []
    listed = (*inventory.disks, *inventory.virtual_disks) if expand_virtual else inventory.disks
    for disk in listed:
        severity = worst_severity(findings, disk.path)
        # One rule for what a disk row says and how it is coloured, shared with
        # the tree. Three copies of it disagreed: this one called every NVMe
        # link healthy whatever it negotiated.
        port = inventory.port_link_for(disk)
        cells = disk_row(disk, port)
        rows.append(
            {
                "marker": (theme.marker_for(severity), theme.style_for(severity)),
                "device": cells["device"],
                "model": cells["model"],
                "wwn": (disk.wwn or "-", "" if disk.wwn else theme.STYLE_UNKNOWN),
                "serial": (disk.serial or "-", "" if disk.serial else theme.STYLE_UNKNOWN),
                "firmware": (disk.firmware or "-", "" if disk.firmware else theme.STYLE_UNKNOWN),
                "size": cells["size"],
                "kind": cells["kind"],
                "bus": cells["bus"],
                "port": cells["port"],
                "disk": cells["disk"],
                "link": cells["link"],
                "controller": (
                    disk.controller_address or "-",
                    theme.STYLE_IDENTIFIER if disk.controller_address else theme.STYLE_UNKNOWN,
                ),
            }
        )
    caption = "" if expand_virtual else virtual_note(inventory.virtual_disks)
    return _render(f"Disks on {inventory.hostname}", DISK_COLUMNS, rows, width, caption)


def render_health(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int = DEFAULT_WIDTH,
    history: History | None = None,
) -> Table:
    """Render wear, temperature and error counters for every disk.

    Args:
        inventory: The machine.
        findings: The findings, used to mark affected rows.
        width: Terminal width, which decides how many columns fit.
        history: Counter samples recorded earlier. With them each count says
            whether it is still moving, which is the difference between a live
            fault and one that ended years ago.

    Returns:
        A table of health readings.
    """
    rows: list[Row] = []
    for disk in inventory.disks:
        severity = worst_severity(findings, disk.path)
        health = disk.health
        series = series_for(disk, history)
        temperature = theme.format_temperature(
            None if health is None else health.temperature_c,
            None if health is None else health.temperature_warning_c,
            None if health is None else health.temperature_critical_c,
        )
        wear = theme.format_wear(None if health is None else health.percent_used)
        written = "-" if health is None or health.bytes_written is None else theme.format_size(health.bytes_written)
        rows.append(
            {
                "marker": (theme.marker_for(severity), theme.style_for(severity)),
                "device": (disk.path, "bold"),
                "model": (disk.model, ""),
                "temp": temperature,
                "worn": wear,
                "hours": (counter_text(None if health is None else health.power_on_hours), ""),
                "written": (written, ""),
                "realloc": counter_cell(
                    None if health is None else health.reallocated_sectors,
                    trend_of(series, CounterKind.REALLOCATED_SECTORS),
                ),
                "pending": counter_cell(
                    None if health is None else health.pending_sectors,
                    trend_of(series, CounterKind.PENDING_SECTORS),
                ),
                "uncorr": counter_cell(
                    None if health is None else health.uncorrectable_sectors,
                    trend_of(series, CounterKind.UNCORRECTABLE_SECTORS),
                ),
                "crc": counter_cell(
                    None if health is None else health.crc_errors,
                    trend_of(series, CounterKind.CRC_ERRORS),
                ),
                "media": counter_cell(
                    None if health is None else health.media_errors,
                    trend_of(series, CounterKind.MEDIA_ERRORS),
                ),
            }
        )
    return _render(f"Disk health on {inventory.hostname}", HEALTH_COLUMNS, rows, width)


def counter_text(value: int | None) -> str:
    """Render one health counter, or a dash when it was not read."""
    return "-" if value is None else str(value)


def series_for(disk: Disk, history: History | None) -> DiskSeries | None:
    """This drive's recorded samples, if any."""
    if history is None:
        return None
    identity = identity_of(disk)
    return None if identity is None else history.for_identity(identity)


def trend_of(series: DiskSeries | None, kind: CounterKind) -> Trend | None:
    """What the samples say about one counter, or nothing recorded."""
    return None if series is None else trend_for(series, kind)


def counter_cell(value: int | None, trend: Trend | None = None) -> Cell:
    """Render one error counter, coloured by what it is doing.

    A counter that could not be read is dim, never zero: those mean different
    things, and showing "0" for an unread value invents good news.

    Where history says the count is still climbing the cell carries a trailing
    ``+``; where history proves it has stopped the cell drops out of red,
    because a fault that ended is not a fault to act on and a red number that
    never changes teaches the reader to ignore red. Without history the cell
    reads exactly as it always did.

    Takes the value rather than an object and a field name, so a renamed field
    is a type error here instead of a silent dash at runtime.
    """
    if value is None:
        return "-", theme.STYLE_UNKNOWN
    if not value:
        return "0", theme.STYLE_AT_CAPABILITY
    if trend is not None and trend.is_rising:
        return f"{value}{RISING_MARK}", theme.STYLE_FAILING
    if trend is not None and trend.is_quiet:
        return str(value), theme.STYLE_UNKNOWN
    return str(value), theme.STYLE_FAILING


def counter_legend(inventory: Inventory, history: History | None) -> str:
    """Explain the markers, but only once they can appear.

    A legend for a mark nobody has on screen is noise, so this stays empty until
    some counter is actually annotated.

    Args:
        inventory: The machine.
        history: What has been recorded, if anything.

    Returns:
        The legend line, or an empty string when nothing is marked.
    """
    if history is None:
        return ""
    for disk in inventory.disks:
        series = series_for(disk, history)
        if series is None:
            continue
        for kind in ANNOTATED_COUNTERS:
            trend = trend_for(series, kind)
            if trend.is_rising or trend.is_quiet:
                return (
                    f"A trailing {RISING_MARK} marks a count still climbing; a count shown dim has "
                    "provably stopped. lsdsk trend gives the rates."
                )
    return ""


__all__ = [
    "CONTROLLER_COLUMNS",
    "DISK_COLUMNS",
    "HEALTH_COLUMNS",
    "counter_cell",
    "counter_text",
    "render_controllers",
    "render_disks",
    "render_health",
    "series_for",
    "trend_of",
]
