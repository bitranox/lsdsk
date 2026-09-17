"""The root-down PCI fabric of the topology view.

Everything else in the report answers a question about storage. This section
answers "what is this machine's PCIe fabric": every PCI device - the bridges,
the controllers, and whatever else the board came with - hangs root-down under
its own structure, so a card on a root-complex port and one three bridges below
a chipset used as a PCIe switch are drawn differently, and a narrower hop is
visible where it happens.

Two measured facts shaped it. Windows publishes no link registers for a
bridge, so its hops must read ``not read`` rather than a dash: a dash beside a
measured figure means "nothing there", and unread must not borrow it. And a
real machine's fabric dwarfs its storage - 45 to 95 devices with 3 to 7
storage controllers on the committed captures - which is exactly why density
is a choice rather than a mechanism that hides anything.

Every row is ``[marker | spine | address | capable | running | name]``. The
spine is one fixed width for the whole section, so the columns after it never
move at any depth: that is the law this module exists for, the same one the
disk tables keep. The spine width is bounded, and every structure row's NAME
is clipped to its budget; headings are prose and keep wrapping, because
clipping one would cut the flag name off the sentence that names it.

System Role:
    Adapter layer, presentation.  Consumes the assembled tree and the findings,
    decides nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from rich.console import Group
from rich.text import Text

from ...domain.enums import TreeDensity
from ..config.tunables import DEFAULT_PIPED_WIDTH, DEFAULT_TREE_DENSITY
from . import theme
from .layout import GAP, Layout, clip, pad
from .report import (
    DISK_COLUMNS,
    VIRTUAL_HEADING,
    disk_cells,
    disk_row,
    render_controller_disks,
    virtual_note,
    worst_severity,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from ...domain.models import Disk, Finding, Inventory, PciNode

# Spine geometry: two characters per drawn level, one blank before the address
# column. S = 2K + 1 for the deepest drawn level K, so a four-level fabric fits
# a spine of 9 where the old tree gutter was 3.
_SPINE_UNIT = 2
_MARGIN_BEFORE_COLUMNS = 1

# Row geometry, named so the budget arithmetic reads as the fields it prices.
_MARKER_WIDTH = 3
_ADDRESS_WIDTH = 12
# The hop field holds both "3.0 x4" and "legacy PCI" (10) without squashing.
_HOP_WIDTH = 10
_GAP_WIDTH = 2
#: What the spine may grow past before it is clipped, beyond the marker.
_SPINE_RESERVE = 4
#: The widest severity marker, carried into every row's budget so a flagged row
#: never lands exactly on the terminal width and wraps its marker. Same reason
#: report.py reserves it before fitting its disk columns.
_MARKER_RESERVE = 2

_TREE_GAP = "  "
_TREE_BRANCH = "|-"
_TREE_LAST = "'-"
_TREE_PIPE = "| "
#: The vertical rule stops below an ancestor with no drawn sibling after it.
_STOP_LEG = "  "

# Width assumed when the caller gives none, as a Textual page does.
DEFAULT_WIDTH = DEFAULT_PIPED_WIDTH

#: What each density draws, in the words a reader of the view would use. Said
#: rather than left to be inferred, because the default shows the least and a
#: view that quietly holds devices back is the blank-implies-fine this tool
#: exists to refuse - the same reason the kernel-virtual tally names its flag.
_DENSITY_DRAWN: dict[TreeDensity, str] = {
    TreeDensity.FULL: "every PCI device",
    TreeDensity.STORAGE_AND_SIBLINGS: "storage and whatever shares a bridge with it",
    TreeDensity.STORAGE_ONLY: "storage and the bridges above it",
}

#: How a reader of the printed view changes it, and how a reader of the
#: interactive one does. Each view supplies its own; the sentence is written
#: once, so the two cannot describe one setting differently.
OPTION_HINT = "--tree-density"
KEY_HINT = 'press "d"'


def density_note(density: TreeDensity, how_to_change: str = OPTION_HINT) -> str:
    """One line: what this view is drawing, and how to ask for more.

    Args:
        density: The density being drawn.
        how_to_change: What this reader types or presses.

    Returns:
        The note, ASCII only like every other line of the section.

    Example:
        >>> density_note(TreeDensity.STORAGE_ONLY)
        'showing storage and the bridges above it; --tree-density to change the detail level'
        >>> density_note(TreeDensity.FULL, KEY_HINT)
        'showing every PCI device; press "d" to change the detail level'
    """
    return f"showing {_DENSITY_DRAWN[density]}; {how_to_change} to change the detail level"


def hop_cells(node: PciNode) -> tuple[theme.Cell, theme.Cell]:
    """(capable, running) for one device's own hop, never a bare dash.

    Asked of the NODE rather than of its link, because the difference between
    a register nobody could read and a device that has none is carried by
    :attr:`~lsdsk.domain.models.PciNode.pcie_capability_present` and by nothing
    in the link itself.

    Example:
        >>> from lsdsk.domain.models import PciNode, PcieLink
        >>> hop_cells(PciNode("a", "b", link=PcieLink(8.0, 4, 8.0, 4)))
        (('3.0 x4', ''), ('3.0 x4', ''))
        >>> hop_cells(PciNode("a", "b", pcie_capability_present=False))
        (('legacy PCI', ''), ('legacy PCI', ''))
        >>> hop_cells(PciNode("a", "b"))[0][0]
        'not read'
    """
    return theme.hop_link_cells(node.link, capability_present=node.pcie_capability_present)


def _spine_width(deepest_level: int) -> int:
    """Spine width for the deepest drawn level (a root bus is level 0)."""
    return _SPINE_UNIT * deepest_level + _MARGIN_BEFORE_COLUMNS


class _Fabric:
    """One render call's shared state: the tree, the density, the geometry."""

    def __init__(self, nodes: Sequence[PciNode], width: int, density: TreeDensity) -> None:
        self.nodes = nodes
        self.width = width
        self.density = density
        self.by_address = {node.address: node for node in nodes if not node.is_root}
        self.kept = self._kept()
        self.by_parent = self._grouped(node for node in self.by_address.values() if node.address in self.kept)
        # A root is drawn when anything under it survived the density.
        self.roots = [
            node
            for node in nodes
            if node.is_root and any(n.parent_address == node.address for n in self.by_address.values())
        ]
        deepest = max((self.level_of(node) for node, _level in self.drawn()), default=0)
        self.spine = min(_spine_width(deepest), max(width - _MARKER_WIDTH - _SPINE_RESERVE, 1))

    @staticmethod
    def _grouped(devices: Iterable[PciNode]) -> dict[str | None, list[PciNode]]:
        grouped: dict[str | None, list[PciNode]] = {}
        for node in devices:
            grouped.setdefault(node.parent_address, []).append(node)
        for group in grouped.values():
            group.sort(key=lambda item: item.address)
        return grouped

    def _kept(self) -> set[str]:
        """The addresses the density keeps.

        FULL keeps everything. The reduced densities are stated as CLASSES,
        not as a walk: every PCI bridge of any kind (a host bridge, an ISA
        bridge, a PCI-to-PCI bridge - class base 06) plus storage.
        STORAGE_AND_SIBLINGS adds the non-storage devices that share a BRIDGE
        with storage - the neighbours that explain lane sharing. A shared
        parent that is not a bridge keeps nothing: devices on a root bus share
        no link, so they are not each other's neighbours there. Reproduced
        against all four committed captures: 95/20/20, 87/14/14, 45/27/27,
        27/15/12 device lines.
        """
        if self.density is TreeDensity.FULL:
            return set(self.by_address)
        bridges = {address for address, node in self.by_address.items() if node.is_bridge_family}
        storage = {address for address, node in self.by_address.items() if node.is_storage}
        keep = bridges | storage
        if self.density is TreeDensity.STORAGE_AND_SIBLINGS:
            by_parent_all = self._grouped(self.by_address.values())
            for address in storage:
                node = self.by_address[address]
                if node.parent_address in bridges:
                    shared = [sibling.address for sibling in by_parent_all.get(node.parent_address, ())]
                    keep.update(shared)
        return keep

    def drawn(self) -> list[tuple[PciNode, int]]:
        """Every drawn device with its level, parents before children, address
        order within a parent."""
        out: list[tuple[PciNode, int]] = []
        for root in self.roots:
            out.extend(self._recurse(root))
        return out

    def _recurse(self, parent: PciNode) -> Iterable[tuple[PciNode, int]]:
        for node in self.by_parent.get(parent.address, ()):
            yield node, self.level_of(node)
            yield from self._recurse(node)

    def level_of(self, node: PciNode) -> int:
        """Level of a device: the child of a root bus is level 1."""
        level = 0
        parent: str | None = node.parent_address
        while parent is not None:
            level += 1
            above = self.by_address.get(parent)
            parent = above.parent_address if above is not None else None
        return level

    def _legs_for(self, node: PciNode) -> list[str]:
        """One spine element per level above the device, root side first.

        The device's own leg is a branch or a last-turn; each ancestor's leg
        is a continuing pipe where a drawn sibling follows it, and dead space
        where the rule stops there.
        """
        chain: list[PciNode] = []
        current: PciNode | None = node
        while current is not None and current.parent_address is not None:
            chain.append(current)
            current = self.by_address.get(current.parent_address)
        chain.reverse()  # root side first
        legs: list[str] = []
        for position, member in enumerate(chain):
            if position == len(chain) - 1:
                legs.append(_TREE_LAST if self.by_parent[member.parent_address][-1] is member else _TREE_BRANCH)
            else:
                siblings = self.by_parent.get(member.parent_address, [])
                legs.append(_STOP_LEG if siblings and siblings[-1] is member else _TREE_PIPE)
        return legs

    def row(self, node: PciNode, findings: Sequence[Finding]) -> Text:
        """One structure row: marker, spine, address, both hops, name.

        The severity marker leads, exactly as every table's rows lead, so a
        narrow terminal cannot strand it on a line of its own - the failure
        test_no_width_strands_a_severity_marker_on_its_own_line exists for.
        """
        line = Text()
        severity = worst_severity(findings, node.address)
        line.append(theme.marker_for(severity).ljust(_MARKER_WIDTH), style=theme.style_for(severity))
        line.append("".join(self._legs_for(node)).ljust(max(self.spine - _MARKER_WIDTH, 0)))
        line.append(node.address.ljust(_ADDRESS_WIDTH), style=theme.STYLE_IDENTIFIER)
        for text, style in hop_cells(node):
            # Each cell carries its own style, so an unread figure is dimmed
            # here exactly as it is in every other table rather than reading at
            # the same weight as the measurement beside it.
            line.append(f"{text:<{_HOP_WIDTH}}{_TREE_GAP}", style=style)
        tag = theme.pci_tag(node.port_kind)
        label = node.name + (f"  ({tag})" if tag else "")
        line.append(clip(label, self.budget))
        return line

    @property
    def budget(self) -> int:
        """Characters one device's name is given."""
        return max(
            self.width - _MARKER_WIDTH - self.spine - _ADDRESS_WIDTH - 2 * _HOP_WIDTH - 3 * _GAP_WIDTH,
            8,
        )

    def measure(self, inventory: Inventory) -> Layout:
        """Fit the disk columns ONCE over every disk drawn anywhere.

        Passed the current level, so the fitting knows the reservation the
        spine costs it. The reservation is the same on every row: device rows
        and disk rows alike budget for the fullest spine, which is what keeps
        two disks on different controllers comparable straight down the page.
        """
        rows = [disk_cells(disk, inventory.port_link_for(disk)) for disk in inventory.disks]
        available = max(self.width - self.spine - _MARKER_WIDTH - _MARKER_RESERVE - 1, 20)
        return Layout.for_rows(DISK_COLUMNS, rows, available)

    def disk_row(
        self,
        disk: Disk,
        layout: Layout,
        findings: Sequence[Finding],
        inventory: Inventory,
    ) -> Text:
        """One disk's row under its controller's fabric row.

        Marker, spine left BLANK rather than drawn, then the globally fitted
        columns. Blank, not a per-level branch glyph: the columns are the
        comparison the view exists for, and the spine spending no width on
        decoration under a controller is what keeps those columns as wide as
        the fabric rows' own allowance permits.

        Matches report._disk_line's field order so the two trees' rows read as
        one shape: marker, gutter, then columns.
        """
        line = Text()
        severity = worst_severity(findings, disk.path)
        line.append(theme.marker_for(severity).ljust(_MARKER_WIDTH), style=theme.style_for(severity))
        line.append(" " * max(self.spine - _MARKER_WIDTH, 0))
        cells = disk_row(disk, inventory.port_link_for(disk))
        for column in layout.columns:
            width = layout.widths[column.key]
            text, style = cells.get(column.key, ("", ""))
            line.append(pad(text, width, column.align), style=style)
            line.append(GAP)
        return line


class FabricView(NamedTuple):
    """How one view draws the fabric.

    The three travel together through every signature that renders it, and the
    third is what keeps one sentence from becoming two: the note above the tree
    is written once and each view says how ITS reader asks for another shape.

    Attributes:
        density: How much of the fabric to draw.
        expand_virtual: List every kernel-virtual device rather than tallying
            them in one line.
        how_to_change: What this view's reader types or presses, named in the
            note above the tree.

    Example:
        >>> FabricView().density is DEFAULT_TREE_DENSITY
        True
        >>> FabricView(how_to_change=KEY_HINT).how_to_change
        'press "d"'
    """

    density: TreeDensity = DEFAULT_TREE_DENSITY
    expand_virtual: bool = False
    how_to_change: str = OPTION_HINT


#: What a caller that asks for nothing gets: the shipped density, the
#: kernel-virtual devices tallied, and the printed view's option named. One
#: shared instance because the tuple is immutable.
DEFAULT_VIEW = FabricView()


def render_fabric(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int = DEFAULT_WIDTH,
    view: FabricView = DEFAULT_VIEW,
) -> RenderableType:
    """The whole topology section: the root-down fabric, the disks on each of
    its storage controllers, and the kernel-virtual tally behind them.

    Args:
        inventory: The machine.
        findings: The findings, used to mark affected rows.
        width: Width to lay out inside. The piped default when unset, which is
            the form a terminal-less Textual page renders at before it reflows.
        view: How this view draws it - the density, the kernel-virtual tally,
            and what its reader presses to change them.

    Returns:
        The fabric section.
    """
    if not inventory.pci_tree:
        # No PCI devices at all, and nothing the disk-and-controller table
        # would show either: say the machine is empty rather than rendering a
        # blank section.
        if not (inventory.disks or inventory.controllers or inventory.virtual_disks):
            return Text("No storage controllers or disks found.", style=theme.STYLE_UNKNOWN)
        # A capture with drives but no PCI reading: the disk-and-controller
        # table is the whole section, so the machine's storage is still shown.
        return _no_pci_fallback(inventory, findings, width, expand_virtual=view.expand_virtual)
    density, expand_virtual = view.density, view.expand_virtual
    fabric = _Fabric(inventory.pci_tree, width, density)
    layout = fabric.measure(inventory)
    out: list[RenderableType] = [Text(density_note(density, view.how_to_change), style=theme.STYLE_NOTE)]
    if len(fabric.roots) > 1:
        # Prose heading, not a spent spine level: a synthetic root is parentage,
        # not hardware, and two root complexes are two bus labels, not two
        # devices.
        out.append(Text("root complexes:", style="bold"))
        out.extend(Text(root.address, style=theme.STYLE_IDENTIFIER) for root in fabric.roots)
    attached: set[str] = set()
    for node, _level in fabric.drawn():
        out.append(fabric.row(node, findings))
        if node.is_storage and inventory.disks_on(node.address):
            disks = inventory.disks_on(node.address)
            attached.update(disk.node for disk in disks)
            out.append(disk_header_line(fabric, layout))
            out.extend(fabric.disk_row(disk, layout, findings, inventory) for disk in disks)
    orphans = [disk for disk in inventory.disks if disk.node not in attached]
    if orphans:
        out.append(Text(""))
        out.append(Text("not attached to a known controller", style=theme.STYLE_UNKNOWN))
        out.append(disk_header_line(fabric, layout))
        out.extend(fabric.disk_row(disk, layout, findings, inventory) for disk in orphans)
    out.extend(_virtual_block(fabric, inventory, findings, layout, expand_virtual=expand_virtual))
    return Group(*out)


def _virtual_block(
    fabric: _Fabric,
    inventory: Inventory,
    findings: Sequence[Finding],
    layout: Layout,
    *,
    expand_virtual: bool,
) -> list[RenderableType]:
    """The kernel-virtual group: a tally, or the devices themselves.

    Folded away by default because a host with forty zvols would otherwise
    bury the drives this view exists to show; the count is always said, never
    hidden. One function here so this tree and any replay agree with the
    printed page about the same machine.
    """
    if not inventory.virtual_disks:
        return []
    lines: list[RenderableType] = [Text(""), Text(VIRTUAL_HEADING, style=theme.STYLE_UNKNOWN)]
    if not expand_virtual:
        lines.append(Text(f"   {virtual_note(inventory.virtual_disks)}", style=theme.STYLE_UNKNOWN))
        return lines
    lines.append(disk_header_line(fabric, layout))
    lines.extend(fabric.disk_row(disk, layout, findings, inventory) for disk in inventory.virtual_disks)
    return lines


def _no_pci_fallback(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int,
    *,
    expand_virtual: bool,
) -> RenderableType:
    """The old disk-and-controller tree, kept for a capture carrying no PCI.

    A software-only environment can publish drives with no ``pci`` section at
    all; the machine's storage is never hidden behind a fabric that does not
    exist, so the previous renderer still runs. Its keep is test-locked by the
    virtual-device tests that were written against it.
    """
    return render_controller_disks(inventory, findings, width, expand_virtual=expand_virtual)


def disk_header_line(fabric: _Fabric, layout: Layout) -> Text:
    """The disk column header, offset by marker and spine like every row.

    Copied from report's own with the gutter widened to the spine: the header
    has to sit exactly above the cells it labels, which sit spine characters
    further right than they did in the old tree.
    """
    line = Text()
    line.append(" " * _MARKER_WIDTH)
    line.append(" " * max(fabric.spine - _MARKER_WIDTH, 0))
    marker = " " * _MARKER_RESERVE
    line.append(marker + " ")
    for column in layout.columns:
        line.append(pad(column.title, layout.widths[column.key], column.align), style=theme.STYLE_HEADER)
        line.append(GAP)
    return line


class FabricSection:
    """The topology section, laid out at whatever width it is handed.

    :func:`render_fabric` fits its columns once, for one width, which is what a
    command that knows the console's width wants. A page inside a window does
    not know it until the layout runs and loses it again on every resize, so it
    asked for no width at all and got the piped default: the fabric drew itself
    for 120 columns in a 200-column terminal and clipped names that had room.

    Deciding it at render time instead of at build time is what keeps the
    interactive page and the printed command one view, and it costs nothing to
    a caller that already knows the width, which passes it and skips this.

    Example:
        >>> from rich.console import Console
        >>> from lsdsk.domain.models import Disk, Inventory
        >>> machine = Inventory("example", disks=(Disk(path="/dev/sda", node="sda", model="A DRIVE"),))
        >>> console = Console(width=60, no_color=True)
        >>> with console.capture() as capture:
        ...     console.print(FabricSection(machine, ()))
        >>> "A DRIVE" in capture.get()
        True
    """

    def __init__(
        self,
        inventory: Inventory,
        findings: Sequence[Finding],
        view: FabricView = DEFAULT_VIEW,
    ) -> None:
        self.inventory = inventory
        self.findings = findings
        self.view = view

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Render the section at the width the console offers right now."""
        yield render_fabric(self.inventory, self.findings, options.max_width, self.view)


__all__ = [
    "DEFAULT_VIEW",
    "KEY_HINT",
    "OPTION_HINT",
    "FabricSection",
    "FabricView",
    "density_note",
    "hop_cells",
    "render_fabric",
]
