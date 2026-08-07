"""The default view: a problem summary above an aligned topology tree.

Columns are measured once across every disk on the machine, so the eye can
compare the link speed of a disk on one controller against a disk on another by
reading straight down.  That comparison is the reason the tool exists, and a
per-controller table would break it.

Columns give up space and then drop out as the terminal narrows, least important
first, so a disk row is never wrapped onto a second line.

System Role:
    Adapter layer, presentation.  Consumes domain objects and findings, produces
    Rich renderables.  It decides nothing; the diagnosis is already made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ... import __init__conf__
from ...domain.diagnostics import count_by_severity
from ...domain.enums import Align, BusType, Environment, Severity
from ...domain.models import pcie_bandwidth_gbps, pcie_generation
from ..config.tunables import DEFAULT_PIPED_WIDTH, DEFAULT_SUMMARY_LIMIT
from . import theme
from .layout import GAP, GUTTER, Column, Layout, fit, natural_widths, pad

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from rich.console import RenderableType

    from ...domain.models import Controller, Disk, Finding, Inventory, PcieLink, PcieSlot, SmartAttribute
    from .rows import Row

# What is lost when the SMART path is unavailable, as the reader sees it named in
# the output, paired with the model field it comes from. Kept as pairs so a test
# can prove the prose still matches the model rather than drifting into a list of
# things the tool no longer reports.
HEALTH_NEEDING_SMART: tuple[tuple[str, str], ...] = (
    ("temperature", "temperature_c"),
    ("wear", "percent_used"),
    ("power-on hours", "power_on_hours"),
    ("reallocated, pending and uncorrectable sectors", "reallocated_sectors"),
    ("CRC errors", "crc_errors"),
    ("lifetime bytes written", "bytes_written"),
)

# What the slots view loses without root, named the way the reader sees it. The
# form factor is absent for a different reason and is not listed here: no source
# on any platform reports it, so no privilege recovers it.
SLOTS_NEEDING_ROOT: tuple[str, ...] = ("physical slot numbers", "whether a port ends in a real connector")

# How many findings the summary shows before pointing at the full list.
SUMMARY_LIMIT = DEFAULT_SUMMARY_LIMIT

# Width assumed when nothing better is known.
DEFAULT_WIDTH = DEFAULT_PIPED_WIDTH

_TREE_BRANCH = "|-"
_TREE_LAST = "'-"

# Display order, and the order columns are given up in as space runs out. The
# device and its link never go: they are the two things the tool is for.
DISK_COLUMNS: tuple[Column, ...] = (
    Column("device", "device", priority=0),
    Column("model", "model", priority=0, flexible=True, min_width=14),
    Column("size", "size", align=Align.RIGHT, priority=3),
    Column("kind", "kind", priority=6),
    Column("bus", "bus", priority=5),
    Column("port", "port", priority=1),
    Column("disk", "disk", priority=1),
    Column("link", "link", priority=0),
    Column("temp", "temp", align=Align.RIGHT, priority=2),
    Column("wear", "worn", align=Align.RIGHT, priority=4),
)


def worst_severity(findings: Iterable[Finding], subject: str) -> Severity | None:
    """Return the most urgent severity recorded against one subject.

    Args:
        findings: All findings.
        subject: The subject to look for.

    Returns:
        The worst severity, or ``None`` when the subject is clean.
    """
    matching = {finding.severity for finding in findings if finding.subject == subject}
    for severity in (Severity.CRITICAL, Severity.WARNING, Severity.HINT):
        if severity in matching:
            return severity
    return None


def render_header(inventory: Inventory) -> RenderableType:
    """Render the banner above the report, including any caveat on the readings.

    The caveat is not decoration. In a container the hardware shown belongs to
    the host, and in a guest the disks are the hypervisor's invention, so a
    reader who does not know which they are looking at will act on the wrong
    thing.
    """
    line = Text()
    # The version rides in the banner because this output gets pasted into
    # tickets and mails, where the first question asked of a surprising reading
    # is which build produced it. A reader who has the report should never have
    # to go back to the machine for that.
    line.append(f"lsdsk {__init__conf__.version}  ", style="bold")
    line.append(inventory.hostname, style=theme.STYLE_IDENTIFIER)
    line.append(f"   {len(inventory.disks)} disks on {len(inventory.controllers)} controllers")
    # The placement hints talk about what "this board" can offer, so naming it
    # turns that advice into something the reader can act on or shop for.
    if inventory.board:
        line.append(f"   {inventory.board}")

    caveat = environment_caveat(inventory)
    limitation = privilege_note(inventory)
    if not caveat and not limitation:
        return line

    lines: list[RenderableType] = [line]
    if caveat:
        lines.append(Text(caveat, style=theme.STYLE_CAVEAT))
    if limitation:
        lines.append(Text(limitation, style=theme.STYLE_CEILING))
    return Group(*lines)


def environment_caveat(inventory: Inventory) -> str:
    """Say what these readings actually describe, when it is not this machine.

    Args:
        inventory: The machine that was scanned.

    Returns:
        The caveat, or an empty string on bare metal.

    Example:
        >>> from lsdsk.domain.models import Inventory
        >>> from lsdsk.domain.enums import Environment
        >>> environment_caveat(Inventory("h", environment=Environment.BARE_METAL))
        ''
    """
    named = f" ({inventory.environment_detail})" if inventory.environment_detail else ""
    if inventory.environment is Environment.CONTAINER:
        return (
            f"Running in a container{named}. This storage belongs to the HOST, seen through a shared "
            "kernel. The findings are real, but they are about the host machine, so act on it there."
        )
    if inventory.environment is Environment.VIRTUAL_MACHINE:
        return (
            f"Running in a virtual machine{named}. These disks and link speeds are what the "
            "hypervisor presents, not physical hardware. Diagnose the host instead."
        )
    return ""


def privilege_note(inventory: Inventory) -> str:
    """Explain why health data is missing, without misattributing the cause.

    Being unprivileged and having no device nodes look identical in the output
    and are fixed differently: elevating helps in the first case and changes
    nothing in the second, which is what a container usually is.

    Naming the specific columns matters: a reader who is told only that "SMART is
    unavailable" cannot tell which dashes in the table mean zero and which mean
    unknown.

    Args:
        inventory: The machine that was scanned.

    Returns:
        The note, or an empty string when health data was read.
    """
    if inventory.privileged and inventory.devices_accessible:
        return ""
    missing = ", ".join(name for name, _ in HEALTH_NEEDING_SMART)
    if not inventory.devices_accessible:
        return (
            f"Not read: {missing}. This environment exposes no device nodes to open, "
            "so elevating would not change that."
        )
    return f"Not read: {missing}. Run as root or Administrator to include them."


def slot_privilege_note(inventory: Inventory) -> str:
    """Explain what the slots view is missing, and whether privilege would help.

    Mirrors :func:`privilege_note` rather than competing with it: being
    unprivileged and being unable to reach the hardware at all look identical in
    the output and are fixed differently. Telling a container user to elevate
    sends them to do something that cannot work.

    Args:
        inventory: The machine that was scanned.

    Returns:
        The note, or an empty string when the slot data was read.

    Example:
        >>> from ...domain.models import Inventory
        >>> slot_privilege_note(Inventory("h", privileged=True))
        ''
        >>> "Run as root" in slot_privilege_note(Inventory("h", privileged=False))
        True
    """
    if inventory.privileged:
        return ""
    missing = ", ".join(SLOTS_NEEDING_ROOT)
    if not inventory.hardware_is_local:
        return f"Not read: {missing}. This is the host's hardware, so run lsdsk on the host to see them."
    return f"Not read: {missing}. Run as root or Administrator to include them."


def form_factor_note() -> str:
    """State that the form factor is unknown, and why, so nobody infers it.

    Measured on three boards: the firmware slot table is the only source that
    carries form factor, and it named no M.2 socket on any of them while getting
    most of its bus addresses wrong. Silence here would invite the reader to
    guess from the width, and an x4 port is just as likely a card slot as an M.2
    socket.
    """
    return (
        "Form factor is not reported: no readable source gives it, so an M.2 socket cannot be told "
        "from a card slot here. Match the slot number against the board manual."
    )


def render_verdict(findings: Sequence[Finding], summary_limit: int = SUMMARY_LIMIT) -> RenderableType:
    """Render the problem summary that opens the report.

    Args:
        findings: The findings, already sorted most urgent first.
        summary_limit: How many to name before saying "and N more".

    Returns:
        A renderable summary.
    """
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1))
    table.add_column("marker", width=2, justify="right", no_wrap=True)
    table.add_column("subject", no_wrap=True)
    table.add_column("title", overflow="fold")

    if not findings:
        table.add_row("", Text("PROBLEMS", style="bold"), Text("none found", style=theme.STYLE_AT_CAPABILITY))
        return table

    counts = count_by_severity(tuple(findings))
    parts = Text()
    for severity in (Severity.CRITICAL, Severity.WARNING, Severity.HINT):
        if counts[severity]:
            parts.append(f"{counts[severity]} {theme.SEVERITY_LABELS[severity]}   ", theme.SEVERITY_STYLES[severity])
    table.add_row("", Text("PROBLEMS", style="bold"), parts)

    for finding in findings[:summary_limit]:
        style = theme.SEVERITY_STYLES[finding.severity]
        table.add_row(
            Text(theme.SEVERITY_MARKERS[finding.severity], style=style),
            Text(finding.subject, style=style),
            Text(finding.title),
        )
    if len(findings) > summary_limit:
        remaining = len(findings) - summary_limit
        table.add_row("", "", Text(f"and {remaining} more, run `lsdsk findings`", style=theme.STYLE_UNKNOWN))
    return table


def _pcie_text(link: PcieLink) -> str:
    """Render a running PCIe link as a generation and width."""
    return theme.format_pcie_generation(link.current_speed_gtps, link.current_width)


def _pcie_capability(link: PcieLink) -> str:
    """Render what a PCIe link could carry at best."""
    return theme.format_pcie_generation(link.max_speed_gtps, link.max_width)


def disk_cells(disk: Disk, port: PcieLink | None = None) -> dict[str, str]:
    """Build the plain-text cells for one disk, before styling.

    Args:
        disk: The disk to describe.
        port: The PCIe port a directly-attached disk sits in, when it is known.
            Without it the port column would repeat the drive's own capability,
            which hides the case this tool exists to find: a Gen4 drive in a Gen3
            seat, where the seat is the constraint.

    Returns:
        A cell value per column key.
    """
    # Three plain numbers rather than one comparison: the port is what the seat
    # offers, the disk is what the drive could ever do, and the link is what they
    # actually agreed on. Which of the three is the constraint is then visible
    # instead of being asserted, and a drive at its own maximum in a faster port
    # reads as the placement question it is rather than as a fault.
    if disk.pcie is not None:
        port_text = _pcie_capability(port) if port is not None else "-"
        drive_text = _pcie_capability(disk.pcie)
        link_text = _pcie_text(disk.pcie)
    else:
        port_text = theme.format_speed(disk.link.port_max_gbps)
        drive_text = theme.format_speed(disk.link.drive_max_gbps)
        link_text = theme.format_speed(disk.link.negotiated_gbps)

    health = disk.health
    temp_text, _ = theme.format_temperature(
        None if health is None else health.temperature_c,
        None if health is None else health.temperature_warning_c,
        None if health is None else health.temperature_critical_c,
    )
    wear_text, _ = theme.format_wear(None if health is None else health.percent_used)

    return {
        "device": disk.path,
        "model": disk.model,
        "size": theme.format_size(disk.size_bytes),
        "kind": theme.format_kind(disk.kind),
        "bus": theme.format_bus(disk.bus),
        "port": port_text,
        "disk": drive_text,
        "link": link_text,
        "temp": temp_text,
        "wear": wear_text,
    }


def disk_cell_styles(disk: Disk, port: PcieLink | None = None) -> dict[str, str]:
    """Style per cell, so colour only ever marks a measured relation.

    A PCIe disk is graded exactly as a SATA one: its negotiated rate against
    what the seat and the drive can each do. Colouring it green whenever it
    trained at all made every NVMe row look healthy, including a Gen4 drive
    running Gen3 because its seat is Gen3.

    Args:
        disk: The disk to style.
        port: The PCIe port it sits in, when known.
    """
    health = disk.health
    if disk.pcie is not None:
        negotiated = pcie_bandwidth_gbps(disk.pcie.current_speed_gtps, disk.pcie.current_width)
        drive_max = pcie_bandwidth_gbps(disk.pcie.max_speed_gtps, disk.pcie.max_width)
        port_max = None if port is None else pcie_bandwidth_gbps(port.max_speed_gtps, port.max_width)
        port_style = theme.port_style(port_max, drive_max)
        drive_style = theme.disk_style(drive_max, port_max)
        negotiated_style = theme.link_style(negotiated, port_max, drive_max)
    else:
        port_style = theme.port_style(disk.link.port_max_gbps, disk.link.drive_max_gbps)
        drive_style = theme.disk_style(disk.link.drive_max_gbps, disk.link.port_max_gbps)
        negotiated_style = theme.link_style(
            disk.link.negotiated_gbps, disk.link.port_max_gbps, disk.link.drive_max_gbps
        )
    _, temp_style = theme.format_temperature(
        None if health is None else health.temperature_c,
        None if health is None else health.temperature_warning_c,
        None if health is None else health.temperature_critical_c,
    )
    _, wear_style = theme.format_wear(None if health is None else health.percent_used)
    return {
        "device": "bold",
        "model": "",
        "size": "",
        "kind": "",
        "bus": "",
        "port": port_style,
        "disk": drive_style,
        "link": negotiated_style,
        "temp": temp_style,
        "wear": wear_style,
    }


_MARKER_RESERVE = max(len(marker) for marker in theme.SEVERITY_MARKERS.values())


def _controller_line(controller: Controller, severity: Severity | None) -> Text:
    """Render the full-width line that introduces one controller."""
    line = Text()
    # Leading, for the same reason the disk rows lead with it: this line has no
    # width budget at all, so a trailing marker wrapped to its own line on a
    # narrow terminal and stranded itself away from the controller it marks.
    marker = "" if severity is None else theme.SEVERITY_MARKERS[severity]
    line.append(marker.ljust(_MARKER_RESERVE) + " ", style="" if severity is None else theme.SEVERITY_STYLES[severity])
    line.append(f"{controller.address}  ", style=theme.STYLE_IDENTIFIER)
    line.append(controller.name, style="bold")
    if controller.driver:
        line.append(f"  {controller.driver}")
    if controller.firmware:
        line.append(f"  fw {controller.firmware}")

    link = controller.link
    running_generation = pcie_generation(link.current_speed_gtps)
    if running_generation is not None and link.current_width is not None:
        style = theme.STYLE_AT_CAPABILITY
        if severity is Severity.CRITICAL:
            style = theme.STYLE_FAILING
        elif severity is Severity.WARNING:
            style = theme.STYLE_BELOW_CAPABILITY
        elif severity is Severity.HINT:
            style = theme.STYLE_CEILING
        text = f"  PCIe {theme.format_pcie_decimal(link.current_speed_gtps, link.current_width)}"
        # Only spell out the capability when the link is not already at it;
        # "PCIe 4.0 x4 of 4.0 x4" is noise on a card that is running perfectly.
        if link.capability_is_known and link.is_downgraded:
            text += f" of {theme.format_pcie_decimal(link.max_speed_gtps, link.max_width)}"
        line.append(text, style=style)

    ports = controller.ports_free
    if controller.port_count is not None:
        line.append(f"   {controller.port_count} ports")
        if ports:
            line.append(f", {ports} free", style=theme.STYLE_AT_CAPABILITY)
    return line


def _disk_line(
    disk: Disk,
    glyph: str,
    layout: Layout,
    *,
    severity: Severity | None,
    port: PcieLink | None = None,
) -> Text:
    """Render one padded disk row."""
    cells = disk_cells(disk, port)
    styles = disk_cell_styles(disk, port)
    line = Text()
    line.append(glyph.ljust(len(GUTTER)))
    # The marker LEADS the row, as it already does in every table. Appended at
    # the end it sat past the fitted width, so on a narrow terminal it wrapped
    # onto a line of its own and detached the one mark that says the row is a
    # problem; cropping instead would have removed the marker rather than the
    # data, which is worse. Measured at COLUMNS=40 across three real fixtures:
    # every flagged row stranded its marker.
    marker = "" if severity is None else theme.SEVERITY_MARKERS[severity]
    line.append(marker.ljust(_MARKER_RESERVE) + " ", style="" if severity is None else theme.SEVERITY_STYLES[severity])
    for column in layout.columns:
        width = layout.widths[column.key]
        line.append(pad(cells.get(column.key, ""), width, column.align), style=styles.get(column.key, ""))
        line.append(GAP)
    return line


def _header_line(columns: Sequence[Column], widths: dict[str, int]) -> Text:
    """Render the column header row."""
    line = Text()
    line.append(" " * len(GUTTER))
    # Matches the marker field `_disk_line` now leads with, or the header would
    # sit one field to the left of the columns it labels.
    line.append(" " * (_MARKER_RESERVE + 1))
    for column in columns:
        line.append(pad(column.title, widths[column.key], column.align), style=theme.STYLE_HEADER)
        line.append(GAP)
    return line


def render_tree(inventory: Inventory, findings: Sequence[Finding], width: int = DEFAULT_WIDTH) -> RenderableType:
    """Render the topology as one globally aligned tree.

    Args:
        inventory: The machine.
        findings: The findings, used to mark affected rows.
        width: Terminal width, which decides how many columns fit.

    Returns:
        A renderable tree.
    """
    if not inventory.disks and not inventory.controllers:
        return Text("No storage controllers or disks found.", style=theme.STYLE_UNKNOWN)

    rows = [disk_cells(disk, inventory.port_link_for(disk)) for disk in inventory.disks]
    # The severity marker is appended after the fitted columns, so the columns
    # have to be fitted to a width that leaves room for it. Without the
    # reservation a flagged row lands exactly on the terminal width and the
    # marker wraps to a line of its own, detaching the one thing that says the
    # row is a problem. Measured at COLUMNS=40 on three real fixtures: every
    # flagged row, and at 42 of 181 widths tested, wherever the fit happened to
    # land on the boundary.
    layout = Layout.for_rows(DISK_COLUMNS, rows, width - _MARKER_RESERVE - 1)

    lines: list[RenderableType] = []
    attached: set[str] = set()
    for index, controller in enumerate(inventory.controllers):
        disks = inventory.disks_on(controller.address)
        attached.update(disk.node for disk in disks)
        if index:
            lines.append(Text(""))
        lines.append(_controller_line(controller, worst_severity(findings, controller.address)))
        # The header is repeated above each group rather than printed once at the
        # top. On a machine with five controllers the single top header ends up
        # twenty lines away from the rows it labels, which is the same as having
        # no header at all.
        if disks:
            lines.append(_header_line(layout.columns, layout.widths))
        lines.extend(_disk_lines(disks, findings, layout, inventory))

    orphans = [disk for disk in inventory.disks if disk.node not in attached]
    if orphans:
        lines.append(Text(""))
        lines.append(Text("not attached to a known controller", style=theme.STYLE_UNKNOWN))
        lines.append(_header_line(layout.columns, layout.widths))
        lines.extend(_disk_lines(orphans, findings, layout, inventory))
    return Group(*lines)


def _disk_lines(
    disks: Sequence[Disk],
    findings: Sequence[Finding],
    layout: Layout,
    inventory: Inventory,
) -> list[Text]:
    """Render every disk under one controller, with tree glyphs."""
    lines: list[Text] = []
    for position, disk in enumerate(disks):
        glyph = _TREE_LAST if position == len(disks) - 1 else _TREE_BRANCH
        lines.append(
            _disk_line(
                disk,
                glyph,
                layout,
                severity=worst_severity(findings, disk.path),
                port=inventory.port_link_for(disk),
            )
        )
    return lines


# Display order for the slot view, and the order columns are given up in as
# space runs out. The port and its verdict never go: they are what the view is
# for.
SLOT_COLUMNS: tuple[Column, ...] = (
    Column("port", "port", priority=0),
    Column("slot", "slot", priority=3),
    Column("capable", "capable", priority=1),
    Column("running", "running", priority=1),
    Column("occupant", "occupant", priority=0, flexible=True, min_width=14),
    Column("needs", "needs", priority=2),
    Column("verdict", "verdict", priority=0),
)


def slot_verdict(slot: PcieSlot) -> theme.Cell:
    """Say what can be done with one port, and style it.

    This is the column the view exists for, and every branch is a statement
    about measured values only. A port whose connector could not be established
    withholds "FREE" rather than asserting a socket nobody has seen: an internal
    port to a soldered-down device is empty in exactly the same way, and sending
    somebody to look for it wastes the trip.

    Args:
        slot: The port to judge.

    Returns:
        The text and the style to render it in.

    Example:
        >>> from ...domain.models import PcieLink, PcieSlot
        >>> free = PcieSlot("0000:00:1c.0", PcieLink(8.0, 1, 8.0, 1), connector_present=True)
        >>> slot_verdict(free)[0]
        'FREE'
        >>> unknown = PcieSlot("0000:00:1c.0", PcieLink(8.0, 1, 8.0, 1))
        >>> slot_verdict(unknown)[0]
        'empty, connector unknown'
    """
    if not slot.occupied:
        return _empty_verdict(slot)
    if slot.occupant_is_display:
        return "in use (graphics)", theme.STYLE_UNKNOWN
    capability = slot.capability_gbps
    need = slot.occupant_need_gbps
    if capability is None or need is None:
        return "in use", theme.STYLE_UNKNOWN
    if need > capability:
        return "port limits it", theme.STYLE_BELOW_CAPABILITY
    spare = round(capability - need, 2)
    if spare > 0:
        # Headroom is measured, so it is reported whether or not a move could be
        # proposed. Whether this port can actually receive a card additionally
        # needs the connector, which is what is_swap_candidate gates on; tying
        # the number to that gate hid 27 GB/s of unused bandwidth behind a
        # missing privilege.
        return f"spare {spare:.2f} GB/s", theme.STYLE_OPPORTUNITY
    return "full", ""


def _empty_verdict(slot: PcieSlot) -> theme.Cell:
    """Judge a port with nothing in it.

    An unread connector withholds "FREE": an internal port to a soldered-down
    device is empty in exactly the same way as a real socket, and only the Slot
    Implemented bit tells them apart.
    """
    if slot.connector_present is True:
        return "FREE", theme.STYLE_AT_CAPABILITY
    if slot.connector_present is False:
        return "no connector", theme.STYLE_UNKNOWN
    return "empty, connector unknown", theme.STYLE_UNKNOWN


def _slot_row(slot: PcieSlot) -> Row:
    """Build one styled row for the slot view."""
    number = "-" if slot.physical_slot_number is None else f"#{slot.physical_slot_number}"
    occupant = slot.occupant_description
    needs = "-" if slot.occupant_link is None else _pcie_capability(slot.occupant_link)
    return {
        "port": (slot.address, theme.STYLE_IDENTIFIER),
        "slot": (number, "" if number != "-" else theme.STYLE_UNKNOWN),
        "capable": (_pcie_capability(slot.link), ""),
        # An empty port trains to nothing, and printing that as "Gen1 x0" reads
        # like a fault rather than an absence.
        "running": (_pcie_text(slot.link) if slot.occupied else "-", ""),
        "occupant": (occupant, "" if slot.occupied else theme.STYLE_UNKNOWN),
        "needs": (needs, ""),
        "verdict": slot_verdict(slot),
    }


def render_slots(inventory: Inventory, width: int = DEFAULT_WIDTH) -> RenderableType:
    """Render the board's PCIe ports, what occupies them and what is free.

    Answers "where could this card go" in one screen, which the findings can
    only answer one card at a time.

    Args:
        inventory: The machine.
        width: Terminal width, which decides how many columns fit.

    Returns:
        The board header, the port table and any caveat below it.
    """
    rows = [_slot_row(slot) for slot in inventory.slots]
    plain = [{key: value[0] for key, value in row.items()} for row in rows]
    widths = natural_widths(SLOT_COLUMNS, plain)
    chosen = fit(SLOT_COLUMNS, widths, width)

    lines: list[RenderableType] = [_board_line(inventory)]
    lines.append(_header_line(chosen, widths))
    for row in rows:
        line = Text(GUTTER)
        for index, column in enumerate(chosen):
            text, style = row.get(column.key, ("-", theme.STYLE_UNKNOWN))
            line.append(pad(text, widths[column.key], column.align), style=style)
            if index != len(chosen) - 1:
                line.append(GAP)
        lines.append(line)

    lines.append(Text(""))
    lines.append(Text(form_factor_note(), style=theme.STYLE_CEILING))
    note = slot_privilege_note(inventory)
    if note:
        lines.append(Text(note, style=theme.STYLE_CEILING))
    return Group(*lines)


def _board_line(inventory: Inventory) -> Text:
    """Render the board name and how many of its ports are free."""
    line = Text()
    line.append(inventory.board or "mainboard not named by firmware", style="bold")
    line.append(f"   {len(inventory.slots)} ports")
    free = sum(1 for slot in inventory.slots if not slot.occupied and slot.connector_present is True)
    if free:
        line.append(f"   {free} free", style=theme.STYLE_AT_CAPABILITY)
    return line


# The SMART attribute table, in the order a drive reports it. Nothing is
# dropped: a vendor attribute nobody has a name for is still evidence, and
# hiding it would make the page disagree with what other tools show.
ATTRIBUTE_COLUMNS: tuple[Column, ...] = (
    # The marker carries the failing verdict in text. Colour alone does not
    # survive a pipe, a log file, NO_COLOR or a colourblind reader, and this
    # table is the one place a manufacturer says the drive is failing.
    Column("mark", "", priority=0),
    Column("id", "id", align=Align.RIGHT, priority=0),
    Column("attribute", "attribute", priority=0, flexible=True, min_width=16),
    Column("value", "value", align=Align.RIGHT, priority=0),
    Column("worst", "worst", align=Align.RIGHT, priority=1),
    Column("threshold", "threshold", align=Align.RIGHT, priority=0),
    Column("raw", "raw", align=Align.RIGHT, priority=0),
)


def _attribute_style(attribute: SmartAttribute) -> str:
    """Style one attribute row against the threshold its own maker set.

    The drive publishes the only figure that means anything here, so nothing is
    judged against a number this tool invented. A raw count that looks alarming
    while its normalised value sits far above the threshold is the drive saying
    it is healthy.
    """
    if attribute.is_failing:
        return theme.STYLE_FAILING
    if attribute.threshold is None:
        return theme.STYLE_UNKNOWN
    return ""


def render_smart(inventory: Inventory, width: int = DEFAULT_WIDTH) -> RenderableType:
    """Render every disk's SMART attributes, one table per disk.

    Every disk is shown rather than one selected disk, because the question this
    page answers is "is anything about to fail", and that cannot be answered one
    drive at a time.

    Args:
        inventory: The machine.
        width: Terminal width, which decides how many columns fit.

    Returns:
        A table per disk that reported attributes, and a line for each that did
        not, so no disk is silently absent.
    """
    lines: list[RenderableType] = [Text(f"SMART attributes on {inventory.hostname}", style="bold")]
    for disk in inventory.disks:
        lines.append(Text(""))
        lines.extend(_disk_attributes(disk, width))
    if len(lines) == 1:
        lines.append(Text("No disk reported a SMART attribute table.", style=theme.STYLE_UNKNOWN))
    return Group(*lines)


def _disk_attributes(disk: Disk, width: int) -> list[RenderableType]:
    """Render one disk's heading and its attribute table."""
    attributes = () if disk.health is None else disk.health.attributes
    heading = Text()
    heading.append(disk.path, style="bold")
    heading.append(f"  {disk.model}", style="")
    if not attributes:
        heading.append(f"  {_no_attributes_reason(disk)}", style=theme.STYLE_UNKNOWN)
        return [heading]
    heading.append(f"  {len(attributes)} attributes")

    rows = [
        {
            "mark": theme.SEVERITY_MARKERS[Severity.CRITICAL] if attribute.is_failing else "",
            "id": str(attribute.id),
            "attribute": attribute.name or "-",
            "value": str(attribute.value),
            "worst": str(attribute.worst),
            "threshold": "-" if attribute.threshold is None else str(attribute.threshold),
            "raw": str(attribute.raw),
        }
        for attribute in attributes
    ]
    widths = natural_widths(ATTRIBUTE_COLUMNS, rows)
    chosen = fit(ATTRIBUTE_COLUMNS, widths, width)

    lines: list[RenderableType] = [heading, _header_line(chosen, widths)]
    for attribute, row in zip(attributes, rows, strict=True):
        style = _attribute_style(attribute)
        line = Text(GUTTER)
        for index, column in enumerate(chosen):
            line.append(pad(row[column.key], widths[column.key], column.align), style=style)
            if index != len(chosen) - 1:
                line.append(GAP)
        lines.append(line)
    return lines


def _no_attributes_reason(disk: Disk) -> str:
    """Say why a disk shows no attribute table, rather than showing nothing.

    An NVMe drive has no ATA attribute table at all; it publishes a fixed health
    log instead, which the health view already shows. That is a different thing
    from an ATA drive whose table could not be read, and a reader who cannot
    tell them apart will go looking for a fault that is not there.
    """
    if disk.bus is BusType.NVME:
        return "NVMe publishes a fixed health log, not an attribute table. See the health page."
    if disk.health is None:
        return "no SMART data was read; this needs root or Administrator."
    return "reported no attribute table."


def render_findings(findings: Sequence[Finding]) -> RenderableType:
    """Render every finding in full, with its reasoning and its remedy."""
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1))
    table.add_column("marker", width=2, justify="right", no_wrap=True)
    table.add_column("body", overflow="fold")

    if not findings:
        table.add_row("", Text("No problems found.", style=theme.STYLE_AT_CAPABILITY))
        return table

    for index, finding in enumerate(findings):
        if index:
            table.add_row("", "")
        style = theme.SEVERITY_STYLES[finding.severity]
        heading = Text()
        heading.append(f"{finding.subject}  ", style=style)
        heading.append(finding.title, style="bold")
        table.add_row(Text(theme.SEVERITY_MARKERS[finding.severity], style=style), heading)
        if finding.detail:
            table.add_row("", Text(finding.detail))
        if finding.action:
            table.add_row("", Text(f"-> {finding.action}"))
    return table


__all__ = [
    "DEFAULT_WIDTH",
    "DISK_COLUMNS",
    "HEALTH_NEEDING_SMART",
    "SUMMARY_LIMIT",
    "disk_cells",
    "render_findings",
    "render_header",
    "render_tree",
    "render_verdict",
    "worst_severity",
]
