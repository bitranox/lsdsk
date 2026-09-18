"""The whole record of one selected thing: what it is, what was measured, what it means.

A table answers in columns, and a column has to be given up as the terminal
narrows, so most of what a scan reads never reaches a page: the disks table
draws twelve of a drive's fields and the health table eleven, and between them
they never show ``ok``, the drive's own temperature limits, ``bytes_read``, the
spare, the power cycles or a single SMART attribute. A finding's sentence and
its remedy live on another page again. This module builds the record the tables
cannot: one thing, every value it carries, and the findings that name it.

Three rules hold it together, each of them earned elsewhere in this tool:

- **The record is keyed by the SUBJECT, never by the page showing it.** A drive
  reads the same under the cursor on the disk page, the health page, the trend
  page and the topology tree; only the ORDER of its groups changes, which is
  what :func:`order_groups` is for. Two page-shaped records of one drive would
  drift exactly as two column lists already did once, when the disk page named
  a drive by model alone for a whole minor series.
- **Every value goes through the formatter its table cell uses**, and the link
  group is taken from :func:`tables.disk_table_row` rather than re-derived, so
  a figure cannot be worded or coloured one way in a row and another here.
- **A value that was not read is a dash in** ``STYLE_UNKNOWN``, and the panel
  prints the legend ``- not read`` once, only when a dash is actually in it.
  The fabric's hop columns carry a legend for the same reason: a bare dash on
  its own is the blank-implies-fine the link rules refuse.

System Role:
    Adapter layer, presentation. Consumes domain objects; decides nothing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final, NamedTuple

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ...domain.diagnostics import attached_demand_gbytes
from ...domain.history import CounterKind
from . import tables, theme
from .report import findings_for, pcie_capability, serial_speed, slot_verdict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from rich.console import RenderableType

    from ...domain.history import DiskSeries, History
    from ...domain.models import Controller, Disk, Finding, Health, Inventory, PcieLink, PcieSlot, PciNode
    from .theme import Cell


class DetailGroupLabel(StrEnum):
    """Which run of paired values a :class:`DetailGroup` carries.

    A closed set rather than a loose string, because a page names one of these
    to promote it to the top of the panel (see :func:`order_groups`) and a
    misspelling used to promote nothing at all, silently - the type checker saw
    a ``str`` either way. This is a presentation concern, not a domain one: the
    value is exactly what the panel prints as the group's own heading, so it
    lives beside the code that draws it rather than in ``domain/enums.py``.

    Example:
        >>> f"{DetailGroupLabel.HEALTH}"
        'health'
    """

    IDENTITY = "identity"
    LINK = "link"
    SEAT = "seat"
    HEALTH = "health"
    COUNTERS = "counters"
    DEVICE = "device"
    UPSTREAM = "upstream"
    PORTS = "ports"
    PLACE = "place"
    SLOT = "slot"
    OCCUPANT = "occupant"
    MACHINE = "machine"


#: The group labels, one module constant per :class:`DetailGroupLabel` member.
#: Kept as individual names because a page, and this module's own
#: ``*_detail`` functions, ask for one at a time rather than the whole enum.
IDENTITY: Final = DetailGroupLabel.IDENTITY
LINK: Final = DetailGroupLabel.LINK
SEAT: Final = DetailGroupLabel.SEAT
HEALTH: Final = DetailGroupLabel.HEALTH
COUNTERS: Final = DetailGroupLabel.COUNTERS
DEVICE: Final = DetailGroupLabel.DEVICE
UPSTREAM: Final = DetailGroupLabel.UPSTREAM
PORTS: Final = DetailGroupLabel.PORTS
PLACE: Final = DetailGroupLabel.PLACE
SLOT: Final = DetailGroupLabel.SLOT
OCCUPANT: Final = DetailGroupLabel.OCCUPANT
MACHINE: Final = DetailGroupLabel.MACHINE

#: Every label a group may carry, which is simply the enum's own membership.
#: :func:`order_groups` validates a page's preference against THIS rather than
#: against the groups in hand, which is the split that matters: a misspelled
#: label is a mistake and raises, while a label naming a group this particular
#: subject has none of is ordinary and is skipped.
GROUP_LABELS: Final[frozenset[DetailGroupLabel]] = frozenset(DetailGroupLabel)

#: What the second scope of a drive's findings is about. Said in the panel
#: because the finding is not about the row the cursor is on: firmware
#: consistency is diagnosed per MODEL, so it names a model string that no row is
#: keyed by, and read without this line it accuses one drive of a fleet's state.
MODEL_NOTE: Final = "about every drive of this model"

#: Printed once under the values when any of them is a dash.
UNREAD_LEGEND: Final = "- not read"


class DetailGroup(NamedTuple):
    """One labelled run of name-and-value pairs."""

    label: DetailGroupLabel
    values: tuple[tuple[str, Cell], ...]


class FindingScope(NamedTuple):
    """One subject string to gather findings under, and what it covers."""

    subject: str
    note: str = ""


class Detail(NamedTuple):
    """Everything the panel draws for one selected thing."""

    heading: tuple[Cell, ...]
    groups: tuple[DetailGroup, ...]
    scopes: tuple[FindingScope, ...]


def order_groups(groups: Sequence[DetailGroup], first: Sequence[str]) -> tuple[DetailGroup, ...]:
    """Float the groups a page is about to the top, keeping the rest in order.

    Args:
        groups: The subject's groups, in their own order.
        first: Labels to promote, most important first.

    Returns:
        The same groups, reordered.

    Raises:
        ValueError: If ``first`` names a label no group can ever carry.

    Example:
        >>> a = DetailGroup(IDENTITY, ())
        >>> b = DetailGroup(HEALTH, ())
        >>> [g.label.value for g in order_groups((a, b), (HEALTH,))]
        ['health', 'identity']
        >>> order_groups((a, b), ("helth",))
        Traceback (most recent call last):
        ValueError: no such detail group: helth
    """
    unknown = tuple(label for label in first if label not in GROUP_LABELS)
    if unknown:
        raise ValueError(f"no such detail group: {', '.join(unknown)}")
    rank = {label: position for position, label in enumerate(first)}
    return tuple(sorted(groups, key=lambda group: rank.get(group.label, len(first))))


def disk_detail(disk: Disk, inventory: Inventory, history: History | None = None) -> Detail:
    """The whole record of one drive."""
    row = tables.disk_table_row(disk, inventory.port_link_for(disk), bandwidth=True)
    series = tables.series_for(disk, history)
    heading = (
        (disk.path, theme.STYLE_IDENTIFIER),
        row["model"],
        row["kind"],
        row["bus"],
    )
    # The capacity is a LABELLED pair rather than a word in the heading. In the
    # heading it sat between the model and the media kind with nothing saying
    # what it was, so a reader looking for the size read the labels below and
    # did not find one. It is written on both scales here because there is room:
    # the column above has to choose, this does not.
    size = (theme.format_size_both(disk.size_bytes), "" if disk.size_bytes else theme.STYLE_UNKNOWN)
    groups = (
        DetailGroup(
            IDENTITY,
            (("size", size), ("serial", row["serial"]), ("firmware", row["firmware"]), ("wwn", row["wwn"])),
        ),
        DetailGroup(LINK, _disk_link_values(disk, row, inventory.port_link_for(disk))),
        DetailGroup(SEAT, _seat_values(disk, inventory)),
        DetailGroup(HEALTH, _health_values(disk.health)),
        DetailGroup(COUNTERS, _counter_values(disk.health, series)),
    )
    return Detail(heading, groups, (FindingScope(disk.path), FindingScope(disk.model, MODEL_NOTE)))


def controller_detail(controller: Controller, inventory: Inventory) -> Detail:
    """The whole record of one storage controller."""
    heading = ((controller.address, theme.STYLE_IDENTIFIER), (controller.name, ""), (str(controller.kind), ""))
    attached = inventory.disks_on(controller.address)
    groups = (
        DetailGroup(DEVICE, (("driver", _said(controller.driver)), ("firmware", _said(controller.firmware)))),
        DetailGroup(LINK, _pcie_values(controller.link)),
        DetailGroup(UPSTREAM, _upstream_values(controller)),
        DetailGroup(PORTS, _port_values(controller, attached, inventory)),
    )
    return Detail(heading, groups, (FindingScope(controller.address),))


def node_detail(node: PciNode, inventory: Inventory) -> Detail:
    """The whole record of one device on the PCI fabric.

    A finding never names a bare PCI address, only a controller's, so a bridge
    or a stray device here shows values and no verdict. That is the honest
    answer rather than an empty one: the panel says no finding names it.
    """
    heading = ((node.address, theme.STYLE_IDENTIFIER), (node.name, ""))
    groups = (
        DetailGroup(DEVICE, _node_device_values(node)),
        DetailGroup(LINK, _node_link_values(node)),
        DetailGroup(PLACE, _node_place_values(node, inventory)),
    )
    return Detail(heading, groups, (FindingScope(node.address),))


def slot_detail(slot: PcieSlot, inventory: Inventory) -> Detail:
    """The whole record of one PCIe port, and whatever sits in it."""
    del inventory
    verdict, verdict_style = slot_verdict(slot)
    heading = ((slot.address, theme.STYLE_IDENTIFIER), (slot.occupant_description, ""), (verdict, verdict_style))
    groups = (
        DetailGroup(SLOT, _slot_values(slot)),
        DetailGroup(LINK, _slot_link_values(slot)),
        DetailGroup(OCCUPANT, _occupant_values(slot)),
    )
    return Detail(heading, groups, (FindingScope(slot.address),))


def machine_detail(inventory: Inventory) -> Detail:
    """The whole record of the machine the board carries."""
    heading = ((inventory.hostname, theme.STYLE_IDENTIFIER), (inventory.board, ""))
    groups = (
        DetailGroup(
            MACHINE,
            (
                ("environment", (str(inventory.environment), "")),
                # Dropped rather than dashed when empty: nothing to add is not
                # the same as nothing read, and the dash means the second.
                *((("detail", (inventory.environment_detail, "")),) if inventory.environment_detail else ()),
                ("privileged", _yes_no(value=inventory.privileged)),
                ("devices readable", _yes_no(value=inventory.devices_accessible)),
            ),
        ),
        DetailGroup(
            DEVICE,
            (
                ("controllers", (str(len(inventory.controllers)), "")),
                ("drives", (str(len(inventory.disks)), "")),
                ("kernel-virtual", (str(len(inventory.virtual_disks)), "")),
                ("ports", (str(len(inventory.slots)), "")),
                ("pci devices", (str(len(inventory.pci_tree)), "")),
            ),
        ),
    )
    return Detail(heading, groups, ())


def render_detail(
    detail: Detail,
    findings: Sequence[Finding],
    *,
    header_style: str = theme.STYLE_HEADER,
) -> RenderableType:
    """Draw one record: a heading, its groups, then the findings that name it.

    No width is threaded in. Every part expands to whatever console renders it,
    so the panel fits the window it lands in and a resize needs nothing: a width
    passed here would be one a caller measured before the layout ran, which for
    a window is the one moment it cannot be known.

    Args:
        detail: The record to draw.
        findings: Every finding of the scan, filtered here by the record's own
            scopes rather than by the caller, so no page can pass a shorter list.
        header_style: How a group's label is drawn. A label IS a header, and a
            header is the one role whose colour cannot be swapped after the
            fact, so the view that wants its own says so here.

    Returns:
        A Rich renderable.
    """
    gathered = tuple((scope, findings_for(findings, scope.subject)) for scope in detail.scopes)
    total = sum(len(matching) for _scope, matching in gathered)
    parts: list[RenderableType] = [_heading_line(detail.heading, total), _values_table(detail.groups, header_style)]
    if _anything_unread(detail.groups):
        parts.append(Text(f" {UNREAD_LEGEND}", style=theme.STYLE_UNKNOWN))
    parts.append(_findings_table(gathered))
    return Group(*parts)


def _values_table(groups: Sequence[DetailGroup], header_style: str = theme.STYLE_HEADER) -> Table:
    """The labelled groups, each wrapping under its values rather than its label.

    The label column is drawn as a HEADER, which is what it is: it names the
    run of pairs beside it, exactly as a column title names the values under
    it. Drawn in the quieter unknown grey it read as a value that could not be
    read, which is the one thing that style is reserved to mean.
    """
    label_width = max((len(group.label) for group in groups), default=0)
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1), expand=True)
    table.add_column("label", width=label_width, no_wrap=True, style=header_style)
    table.add_column("body", overflow="fold", ratio=1)
    for group in groups:
        table.add_row(group.label, _values_text(group.values))
    return table


def _findings_table(gathered: Sequence[tuple[FindingScope, Sequence[Finding]]]) -> Table:
    """Every finding naming this record, each under the scope that found it.

    Built as its own marker-and-body table, the shape :func:`render_findings`
    already uses, so a sentence that wraps keeps its indent instead of falling
    back to column zero on its second line.
    """
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1), expand=True)
    table.add_column("marker", width=2, justify="right", no_wrap=True)
    table.add_column("body", overflow="fold", ratio=1)
    if not any(matching for _scope, matching in gathered):
        table.add_row("", Text("No finding names this.", style=theme.STYLE_AT_CAPABILITY))
        return table
    for scope, matching in gathered:
        if not matching:
            continue
        if scope.note:
            table.add_row("", Text(scope.note, style=theme.STYLE_UNKNOWN))
        for finding in matching:
            _append_one_finding(table, finding)
    return table


def _append_one_finding(table: Table, finding: Finding) -> None:
    """Add one finding's marker, title, reasoning and remedy."""
    style = theme.SEVERITY_STYLES[finding.severity]
    heading = Text(finding.title, style="bold")
    table.add_row(Text(theme.SEVERITY_MARKERS[finding.severity], style=style), heading)
    if finding.detail:
        table.add_row("", Text(finding.detail))
    if finding.action:
        table.add_row("", Text(f"-> {finding.action}"))


def _heading_line(heading: Sequence[Cell], findings: int) -> Table:
    """What is selected on the left, how many findings name it on the right."""
    line = Text()
    for index, (text, style) in enumerate(heading):
        if index:
            line.append("  ")
        line.append(text, style=style)
    counted = "no findings" if not findings else f"{findings} finding" + ("s" if findings > 1 else "")
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column("what", ratio=1, overflow="ellipsis", no_wrap=True)
    grid.add_column("count", justify="right", no_wrap=True)
    grid.add_row(line, Text(counted, style=theme.STYLE_UNKNOWN))
    return grid


def _values_text(values: Sequence[tuple[str, Cell]]) -> Text:
    """Join one group's pairs, wrapping under the values rather than the label."""
    line = Text()
    for index, (name, (text, style)) in enumerate(values):
        if index:
            line.append("   ")
        line.append(f"{name} ", style=theme.STYLE_UNKNOWN)
        line.append(text, style=style)
    return line


def _anything_unread(groups: Iterable[DetailGroup]) -> bool:
    """Whether any drawn value is the dash that means nobody published it."""
    return any(text == "-" for group in groups for _name, (text, _style) in group.values)


def _said(value: str | None) -> Cell:
    """A string the platform published, or the dash that says it did not."""
    return ("-", theme.STYLE_UNKNOWN) if not value else (value, "")


def _yes_no(*, value: bool | None) -> Cell:
    """A tri-state answer, where unknown is not the same as no."""
    if value is None:
        return "-", theme.STYLE_UNKNOWN
    return ("yes", theme.STYLE_AT_CAPABILITY) if value else ("no", theme.STYLE_BELOW_CAPABILITY)


def _disk_link_values(disk: Disk, row: dict[str, Cell], port: PcieLink | None) -> tuple[tuple[str, Cell], ...]:
    """The three figures the disk table draws, plus what the PAIRING could manage.

    The three come from the table's own row builder, so a figure cannot be
    worded or coloured one way in a row and another here. They arrive carrying
    their bandwidth because :func:`disk_detail` asks for it: the table gives
    that detail up on a narrow terminal and the panel never has to.

    ``achievable`` is a different claim from any one of them - the best the two
    ends could manage together - and it reads the SAME pairing they do. The
    three switch source by drive kind, so this must too: ``InterfaceLink`` is
    empty on every NVMe drive by construction, so asking it there returned the
    NOT-READ dash for a pairing that was fully measured, which is one symbol
    making two claims. A SATA drive keeps the bits scale its own ends use.
    """
    if disk.pcie is not None:
        limiting = disk.pcie.limiting_end(port)
        text = theme.NOT_READ if limiting is None else pcie_capability(limiting, bandwidth=True)
        achievable = (text, "")
    else:
        achievable = (serial_speed(disk.link.achievable_gbps, bandwidth=True), "")
    return (("port", row["port"]), ("drive", row["disk"]), ("negotiated", row["link"]), ("achievable", achievable))


def _seat_values(disk: Disk, inventory: Inventory) -> tuple[tuple[str, Cell], ...]:
    """Which controller this drive hangs off, named rather than only addressed."""
    address = disk.controller_address
    controller = next((one for one in inventory.controllers if one.address == address), None)
    if controller is None:
        return (("controller", _said(address)), ("node", _said(disk.node)))
    return (
        ("controller", (controller.address, theme.STYLE_IDENTIFIER)),
        ("", (controller.name, "")),
        ("driver", _said(controller.driver)),
        ("node", _said(disk.node)),
    )


def _health_values(health: Health | None) -> tuple[tuple[str, Cell], ...]:
    """What the drive says about itself, limits included."""
    temperature = theme.format_temperature(
        _of(health, lambda h: h.temperature_c),
        _of(health, lambda h: h.temperature_warning_c),
        _of(health, lambda h: h.temperature_critical_c),
    )
    return (
        ("ok", _yes_no(value=None if health is None else health.ok)),
        ("temp", temperature),
        ("limits", _limits(health)),
        ("worn", theme.format_wear(_of(health, lambda h: h.percent_used))),
        ("hours", (tables.counter_text(_of(health, lambda h: h.power_on_hours)), "")),
        ("written", (theme.format_size(_of(health, lambda h: h.bytes_written)), "")),
        ("read", (theme.format_size(_of(health, lambda h: h.bytes_read)), "")),
        ("spare", _spare(health)),
        ("smart", _smart(health)),
    )


def _counter_values(health: Health | None, series: DiskSeries | None) -> tuple[tuple[str, Cell], ...]:
    """Every error counter, carrying the same trend mark the health table draws."""
    watched: tuple[tuple[str, Callable[[Health], int | None], CounterKind], ...] = (
        ("realloc", lambda h: h.reallocated_sectors, CounterKind.REALLOCATED_SECTORS),
        ("pending", lambda h: h.pending_sectors, CounterKind.PENDING_SECTORS),
        ("uncorr", lambda h: h.uncorrectable_sectors, CounterKind.UNCORRECTABLE_SECTORS),
        ("crc", lambda h: h.crc_errors, CounterKind.CRC_ERRORS),
        ("media", lambda h: h.media_errors, CounterKind.MEDIA_ERRORS),
        ("error log", lambda h: h.error_log_entries, CounterKind.ERROR_LOG_ENTRIES),
    )
    marked = tuple(
        (label, tables.counter_cell(_of(health, accessor), tables.trend_of(series, kind)))
        for label, accessor, kind in watched
    )
    plain = (
        ("cycles", (tables.counter_text(_of(health, lambda h: h.power_cycles)), "")),
        ("unsafe", (tables.counter_text(_of(health, lambda h: h.unsafe_shutdowns)), "")),
    )
    return marked + plain


def _limits(health: Health | None) -> Cell:
    """The drive's own temperature thresholds, which colour the reading above."""
    warning = _of(health, lambda h: h.temperature_warning_c)
    critical = _of(health, lambda h: h.temperature_critical_c)
    if warning is None and critical is None:
        return "-", theme.STYLE_UNKNOWN
    return f"warn {tables.counter_text(warning)}, crit {tables.counter_text(critical)}", ""


def _spare(health: Health | None) -> Cell:
    """Spare blocks left against the floor the drive itself declares."""
    spare = _of(health, lambda h: h.available_spare)
    if spare is None:
        return "-", theme.STYLE_UNKNOWN
    threshold = _of(health, lambda h: h.available_spare_threshold)
    style = theme.STYLE_FAILING if threshold is not None and spare < threshold else theme.STYLE_AT_CAPABILITY
    return f"{spare}% of {tables.counter_text(threshold)}%", style


def _smart(health: Health | None) -> Cell:
    """How many attributes were decoded, and how many are past their threshold."""
    if health is None or not health.attributes:
        return "-", theme.STYLE_UNKNOWN
    failing = sum(1 for attribute in health.attributes if attribute.is_failing)
    text = f"{len(health.attributes)} attributes, {failing} failing"
    return text, theme.STYLE_FAILING if failing else ""


def _of(health: Health | None, accessor: Callable[[Health], int | None]) -> int | None:
    """One counter, read through a typed accessor rather than a field-name string.

    The old signature took the field's NAME and read it with ``getattr``, which
    is the ``data["key"]`` pattern wearing a function call: renaming a
    :class:`Health` field would surface only as a runtime ``AttributeError``,
    invisible to the type checker. ``accessor`` closes over the real attribute
    read instead, so pyright checks it against the actual model and a rename
    is a type error at every call site that still names the old field.

    Args:
        health: The drive's health record, or ``None`` when nothing was read.
        accessor: A callable naming which field to read, for example
            ``lambda h: h.percent_used``.

    Returns:
        The field's value, or ``None`` when there is no health record at all.
    """
    if health is None:
        return None
    return accessor(health)


def _pcie_values(link: PcieLink) -> tuple[tuple[str, Cell], ...]:
    """A PCIe link's two figures, each carrying what IT is worth.

    The panel has no width contest - it wraps rather than dropping columns - so
    both figures always carry their bandwidth here.

    There is no third ``carries`` value any more. It held the CAPABLE link's
    throughput and sat at the end of a line whose first value was the RUNNING
    link, so a reader comparing a downgraded card against its own capability
    took the number nearest what they were looking at and read the wrong one.
    Each figure now carries its own, which is the same fact in the seat that
    says whose it is.
    """
    running = theme.format_pcie_generation(link.current_speed_gtps, link.current_width)
    capable = theme.format_pcie_generation(link.max_speed_gtps, link.max_width)
    style = "" if running == capable else theme.STYLE_BELOW_CAPABILITY
    return (
        ("running", (theme.with_bandwidth(running, link.current_bandwidth_gbps), style)),
        ("capable", (theme.with_bandwidth(capable, link.max_bandwidth_gbps), "")),
    )


def _upstream_values(controller: Controller) -> tuple[tuple[str, Cell], ...]:
    """The port this controller hangs off, which is where an uplink is lost."""
    if controller.upstream is None:
        return (("port", _said(controller.upstream_address)), ("name", _said(controller.upstream_name)))
    return (
        ("port", _said(controller.upstream_address)),
        ("name", _said(controller.upstream_name)),
        *_pcie_values(controller.upstream),
    )


def _port_values(
    controller: Controller, attached: Sequence[Disk], inventory: Inventory
) -> tuple[tuple[str, Cell], ...]:
    """How many sockets this controller has, how many are taken, and what they pull."""
    total = controller.port_count
    used = controller.ports_used
    ports = "-" if total is None else f"{used or 0} of {total}"
    demand = attached_demand_gbytes(controller, inventory)
    return (
        ("in use", (ports, "" if total is not None else theme.STYLE_UNKNOWN)),
        ("free", (tables.counter_text(controller.ports_free), "")),
        ("drives", (str(len(attached)), "")),
        ("peak demand", _gbytes(demand)),
        ("uplink carries", _gbytes(controller.achievable_bandwidth_gbps)),
    )


def _node_device_values(node: PciNode) -> tuple[tuple[str, Cell], ...]:
    """What the fabric publishes about a device, ids included."""
    return (
        ("driver", _said(node.driver)),
        ("class", _hex(node.class_code, 6)),
        ("vendor", _hex(node.vendor, 4)),
        ("kind", (str(node.port_kind), "")),
    )


def _node_link_values(node: PciNode) -> tuple[tuple[str, Cell], ...]:
    """A device's link, with the reason it has none said rather than left blank."""
    if node.pcie_capability_present is False:
        return (("pcie", (theme.LEGACY, theme.STYLE_UNKNOWN)),)
    return _pcie_values(node.link)


def _node_place_values(node: PciNode, inventory: Inventory) -> tuple[tuple[str, Cell], ...]:
    """Where a device sits: what carries it, what it carries, which socket."""
    del inventory
    slot = node.physical_slot_number
    return (
        ("behind", _said(node.parent_address)),
        ("carries", (str(len(node.children)), "")),
        ("socket", ("-" if slot is None else f"#{slot}", theme.STYLE_UNKNOWN if slot is None else "")),
        ("connector", _yes_no(value=node.connector_present)),
    )


def _slot_values(slot: PcieSlot) -> tuple[tuple[str, Cell], ...]:
    """The socket itself, apart from whatever is plugged into it."""
    number = slot.physical_slot_number
    return (
        ("socket", ("-" if number is None else f"#{number}", theme.STYLE_UNKNOWN if number is None else "")),
        ("connector", _yes_no(value=slot.connector_present)),
        ("occupied", _yes_no(value=slot.occupied)),
        ("vendor", _hex(slot.vendor, 4)),
    )


def _slot_link_values(slot: PcieSlot) -> tuple[tuple[str, Cell], ...]:
    """What the socket can do, and what it IS doing only when something is in it.

    An empty socket still publishes a negotiated speed and width, usually x0,
    and the slots table draws a dash there rather than that figure. The panel
    follows it: a reader comparing the two must not be shown a link running in
    a socket they can see is empty.
    """
    values = _pcie_values(slot.link)
    if slot.occupied:
        return values
    return tuple((name, ("-", theme.STYLE_UNKNOWN) if name == "running" else cell) for name, cell in values)


def _occupant_values(slot: PcieSlot) -> tuple[tuple[str, Cell], ...]:
    """What sits in the socket, and what it could use if the socket allowed it."""
    needs = (
        "-"
        if slot.occupant_link is None
        else theme.with_bandwidth(
            theme.format_pcie_generation(slot.occupant_link.max_speed_gtps, slot.occupant_link.max_width),
            slot.occupant_link.max_bandwidth_gbps,
        )
    )
    return (
        ("address", _said(slot.occupant_address)),
        ("name", _said(slot.occupant_name)),
        ("needs", (needs, theme.STYLE_UNKNOWN if slot.occupant_link is None else "")),
        ("devices", (str(slot.occupant_count), "")),
        ("vendor", _hex(slot.occupant_vendor, 4)),
    )


def _gbytes(value: float | None) -> Cell:
    """A bandwidth in BYTES per second, spelled out.

    Never through :func:`theme.format_speed`, which renders the drive rows one
    line above in bits and suffixes both with a bare ``G``: 6G and 15.75G would
    read as one scale when they are eight times apart. The wording itself comes
    from :func:`theme.format_bandwidth`, so a figure standing alone here and one
    in parentheses beside a link are written the same way.
    """
    return ("-", theme.STYLE_UNKNOWN) if value is None else (theme.format_bandwidth(value), "")


def _hex(value: int | None, digits: int) -> Cell:
    """A numeric id as the hex every other tool prints it in."""
    return ("-", theme.STYLE_UNKNOWN) if value is None else (f"0x{value:0{digits}x}", "")


__all__ = [
    "COUNTERS",
    "DEVICE",
    "GROUP_LABELS",
    "HEALTH",
    "IDENTITY",
    "LINK",
    "MACHINE",
    "MODEL_NOTE",
    "OCCUPANT",
    "PLACE",
    "PORTS",
    "SEAT",
    "SLOT",
    "UNREAD_LEGEND",
    "UPSTREAM",
    "Detail",
    "DetailGroup",
    "DetailGroupLabel",
    "FindingScope",
    "controller_detail",
    "disk_detail",
    "machine_detail",
    "node_detail",
    "order_groups",
    "render_detail",
    "slot_detail",
]
