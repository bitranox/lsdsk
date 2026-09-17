"""The root-down PCI fabric of the topology view.

Everything else in the report answers a question about storage. This section
answers "what is this machine's PCIe fabric": every PCI device - the bridges,
the controllers, and whatever else the board came with - hangs root-down under
its own structure, so a card on a root-complex port and one three bridges below
a chipset used as a PCIe switch are drawn differently, and a narrower hop is
visible where it happens.

Two measured facts shaped it. Windows publishes no link registers for a
bridge, so a hop there is unread rather than absent, and the two read
differently: the columns carry a symbol for each (:data:`theme.NOT_READ` and
:data:`theme.LEGACY`) and the section spells out whichever it drew, because a
dash nobody explains is the blank-implies-fine this tool refuses. And a real
machine's fabric dwarfs its storage - 45 to 95 devices with 3 to 7 storage
controllers on the committed captures - which is exactly why density is a
choice rather than a mechanism that hides anything.

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

from ...domain.enums import PciPortKind, TreeDensity
from ..config.tunables import DEFAULT_PIPED_WIDTH, DEFAULT_TREE_DENSITY
from . import theme
from .layout import (
    GAP,
    TREE_BRANCH,
    TREE_DOWN,
    TREE_LAST,
    TREE_LEAD,
    TREE_PIPE,
    TREE_STOP,
    Column,
    Layout,
    clip,
    pad,
)
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
    from collections.abc import Iterable, Mapping, Sequence
    from typing import TypeAlias

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from ...domain.models import Disk, Finding, Inventory, PciNode

    #: What a drawn line of the fabric can be ABOUT. A device on the fabric, a
    #: drive under one of them, or - for the board line - the machine itself.
    #: The union is written out rather than widened to ``object`` because the
    #: interactive page dispatches on it, and a match that falls through is the
    #: thing a written-out union makes visible.
    FabricSubject: TypeAlias = PciNode | Disk | Inventory

# Spine geometry: two characters per drawn level, then the column a row's own
# turn points down into, then one blank so the leader never touches the address.
# S = 2K + 2 for the deepest drawn level K.
_SPINE_UNIT = 2
_MARGIN_BEFORE_COLUMNS = 2

# Row geometry, named so the budget arithmetic reads as the fields it prices.
_MARKER_WIDTH = 3
_ADDRESS_WIDTH = 12
#: Width of each hop column, public because it is the claim
#: ``test_no_hop_figure_is_wider_than_the_column_it_is_drawn_in`` checks.
# The widest figure the formatter can produce for a shipping generation is
# "5.0 x16" (7). The two symbols beside it are shorter, so the column is sized
# by the measurement rather than by the longest word about its absence, which
# is 3 characters per column back to the device name.
HOP_WIDTH = 7
_GAP_WIDTH = 2
#: Both hop columns with their gaps: they are drawn together or not at all.
_HOPS_WIDTH = 2 * (HOP_WIDTH + _GAP_WIDTH)
#: Characters a name is worth drawing in. Below this the hops go first, because
#: a device with no name is not identifiable and a hop is a figure beside one.
_MIN_NAME_WIDTH = 8
#: The floor the disk columns are fitted in, whatever the spine costs.
_MIN_COLUMNS_WIDTH = 20

_TREE_GAP = "  "

# Width assumed when the caller gives none, as a Textual page does.
DEFAULT_WIDTH = DEFAULT_PIPED_WIDTH

#: What each density draws, in the words a reader of the view would use. Said
#: rather than left to be inferred, because the default shows the least and a
#: view that quietly holds devices back is the blank-implies-fine this tool
#: exists to refuse - the same reason the kernel-virtual tally names its flag.
_DENSITY_DRAWN: dict[TreeDensity, str] = {
    TreeDensity.STORAGE_ONLY: "storage and the bridges above it",
    TreeDensity.STORAGE_AND_SIBLINGS: "storage and whatever shares a bridge with it",
    TreeDensity.FULL: "every PCI device",
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
    """(capable, running) for one device's own hop, as a figure or a symbol.

    Asked of the NODE rather than of its link, because the difference between
    a register nobody could read and a device that has none is carried by
    :attr:`~lsdsk.domain.models.PciNode.pcie_capability_present` and by nothing
    in the link itself. Whichever symbol a section draws, it explains in
    :meth:`Fabric.hop_legend`.

    Example:
        >>> from lsdsk.domain.models import PciNode, PcieLink
        >>> hop_cells(PciNode("a", "b", link=PcieLink(8.0, 4, 8.0, 4)))
        (('3.0 x4', ''), ('3.0 x4', ''))
        >>> hop_cells(PciNode("a", "b", pcie_capability_present=False))[0][0]
        'legacy'
        >>> hop_cells(PciNode("a", "b"))[0][0]
        '-'
    """
    return theme.hop_link_cells(node.link, capability_present=node.pcie_capability_present)


class Field(NamedTuple):
    """One field of a device row: which column it is, and how wide it is drawn.

    Attributes:
        key: The column, as :data:`DEVICE_COLUMNS` names it.
        width: Characters it is given.
    """

    key: str
    width: int


#: The device row's columns, in the order it draws them, titled in the words
#: this repo already uses for these figures: `report.SLOT_COLUMNS` and
#: `tables.CONTROLLER_COLUMNS` both say `address`, `capable` and `running`. The
#: last is `name` rather than `device`, because the disk table one line below
#: says `device` for a path like /dev/sda and two headers spelling one word for
#: two different things is worse than a plainer word.
DEVICE_COLUMNS: tuple[Column, ...] = (
    Column("address", "address"),
    Column("capable", "capable"),
    Column("running", "running"),
    Column("name", "name"),
)

_HEADER_CELLS: dict[str, theme.Cell] = {column.key: (column.title, theme.STYLE_HEADER) for column in DEVICE_COLUMNS}


def device_fields(width: int, spine: int) -> tuple[Field, ...]:
    """Which fields a device row draws at this width, and how wide each is.

    Decided once per SECTION rather than per row, because it depends only on
    the width and the spine - which is what lets the header label exactly the
    columns the rows drew. The hop pair goes whole or not at all: a clipped
    speed is a different figure rather than a shorter one. Below the address
    there is nothing left to give up, so the address is cut and ends the row,
    which beats wrapping onto a line that carries no address and reads as
    another device.

    Args:
        width: Width the section is laid out in.
        spine: Width the tree rules occupy, after the marker.

    Returns:
        The fields, in drawing order, never empty.

    Example:
        >>> [(field.key, field.width) for field in device_fields(200, 9)]
        [('address', 12), ('capable', 7), ('running', 7), ('name', 156)]
        >>> [field.key for field in device_fields(48, 9)]
        ['address', 'name']
        >>> device_fields(20, 5)
        (Field(key='address', width=12),)
    """
    room = width - _MARKER_WIDTH - spine
    if room < _ADDRESS_WIDTH + _GAP_WIDTH:
        return (Field("address", max(room, 1)),)
    room -= _ADDRESS_WIDTH + _GAP_WIDTH
    hops: tuple[Field, ...] = ()
    if room >= _HOPS_WIDTH + _MIN_NAME_WIDTH:
        hops = (Field("capable", HOP_WIDTH), Field("running", HOP_WIDTH))
        room -= _HOPS_WIDTH
    return (Field("address", _ADDRESS_WIDTH), *hops, Field("name", room))


def _append_fields(line: Text, fields: Sequence[Field], cells: Mapping[str, theme.Cell]) -> None:
    """Draw one row of fields: padded and separated, the last one clipped.

    One loop for the rows and the header, so a title cannot sit a character off
    the values under it - which is exactly what a second copy of this
    arithmetic did to the disk header.
    """
    for field in fields[:-1]:
        text, style = cells[field.key]
        line.append(f"{text:<{field.width}}{_TREE_GAP}", style=style)
    text, style = cells[fields[-1].key]
    line.append(clip(text, fields[-1].width), style=style)


def device_header_line(fabric: Fabric, rules: str = "") -> Text:
    """The column header over the device rows, offset and ruled like one.

    It carries the rules live at the point it is drawn, so a header repeated
    between two devices does not break the vertical line running past it.
    """
    line = Text()
    line.append(" " * _MARKER_WIDTH)
    line.append(rules or " " * fabric.spine)
    _append_fields(line, fabric.fields, _HEADER_CELLS)
    return line


def _spine_width(deepest_level: int) -> int:
    """Spine width for the deepest drawn level (a root bus is level 0)."""
    return _SPINE_UNIT * deepest_level + _MARGIN_BEFORE_COLUMNS


class Fabric:
    """One render call's shared state: the tree, the density, the geometry.

    Public because a row is where the geometry is observable: the guards that
    hold the spine to one width and a device to one line ask a single row what
    it drew, which reading the finished section back as text cannot answer -
    a wrapped row and two devices look alike there.
    """

    def __init__(
        self,
        nodes: Sequence[PciNode],
        width: int,
        density: TreeDensity,
        *,
        expand_virtual: bool = False,
    ) -> None:
        self.nodes = nodes
        self.width = width
        self.density = density
        self.expand_virtual = expand_virtual
        self.by_address = {node.address: node for node in nodes if not node.is_root}
        self.kept = self._kept()
        self.by_parent = self._grouped(node for node in self.by_address.values() if node.address in self.kept)
        # A root is drawn when something under it SURVIVED the density. Asked
        # of the kept set rather than of every device, which is always true by
        # construction - a root bus exists exactly where a device attaches to
        # one - and so listed a root complex with nothing beneath it.
        self.roots = [node for node in nodes if node.is_root and self.by_parent.get(node.address)]
        deepest = max((self.level_of(node) for node, _level in self.drawn()), default=0)
        # One width for the whole section, wide enough for the DEEPEST row's
        # legs: padding to anything narrower let that row's columns sit two
        # characters right of every other row's, which is exactly the law this
        # module exists to keep. Capped so the address still fits beside it.
        self.spine = min(_spine_width(deepest), max(width - _MARKER_WIDTH - _ADDRESS_WIDTH, 0))
        #: What every row of this section draws, so the header labels the same.
        self.fields = device_fields(self.width, self.spine)

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

        FULL keeps everything. A reduced density starts from the STORAGE and
        keeps the path to it: the bridges that come with it are the ones above
        a drawn device, added by :meth:`_with_ancestors`, not every class-06
        device on the board. Keeping them all drew the whole bridge skeleton -
        a downstream port leading to a graphics card, an LPC bridge, the four
        legs of a Thunderbolt switch - in a view that calls itself the storage
        one, and on the reporter's capture 11 of the 26 devices drawn had no
        storage anywhere below them.

        STORAGE_AND_SIBLINGS adds the non-storage devices that share a BRIDGE
        with storage - the neighbours that explain lane sharing. A shared
        parent that is not a bridge keeps nothing: devices on a root bus share
        no link, so they are not each other's neighbours there.
        """
        if self.density is TreeDensity.FULL:
            return set(self.by_address)
        storage = {address for address, node in self.by_address.items() if node.is_storage}
        keep = set(storage)
        if self.density is TreeDensity.STORAGE_AND_SIBLINGS:
            by_parent_all = self._grouped(self.by_address.values())
            for address in storage:
                parent = self.by_address[address].parent_address
                above = self.by_address.get(parent) if parent is not None else None
                if above is not None and above.is_bridge_family:
                    keep.update(sibling.address for sibling in by_parent_all.get(parent, ()))
        return self._with_ancestors(keep)

    def _with_ancestors(self, keep: set[str]) -> set[str]:
        """Add whatever stands between a kept device and its root bus.

        The density selects by CLASS and the drawing walks DOWN through kept
        parents, so a kept device whose parent was not kept is selected and
        never reached - it does not float, it vanishes, which is the failure a
        view that exists to show hardware can least afford. It happens wherever
        an intermediate device carries no class code, which is exactly what
        Windows publishes for its host bridge.

        On the committed captures every ancestor of a kept device is a bridge
        and already kept, which is why the measured counts do not move.
        """
        complete = set(keep)
        for address in keep:
            parent = self.by_address[address].parent_address
            while parent is not None and parent in self.by_address and parent not in complete:
                complete.add(parent)
                parent = self.by_address[parent].parent_address
        return complete

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
                legs.append(TREE_LAST if self.by_parent[member.parent_address][-1] is member else TREE_BRANCH)
            else:
                siblings = self.by_parent.get(member.parent_address, [])
                legs.append(TREE_STOP if siblings and siblings[-1] is member else TREE_PIPE)
        return legs

    def rules_under(self, node: PciNode | None) -> str:
        """The spine a block nested under one device draws.

        Its ancestors' rules unchanged, then a continuing rule in the device's
        OWN column when a drawn sibling still follows it and dead space when
        none does - which is what a reader following a rule down the page
        expects to find on the far side of the block. The spine is a
        fixed-width field, so this costs no width; blanking it was what broke
        the rule at every disk block. ``None`` is a block with no device above
        it - the orphans and the kernel-virtual tally - where there is no rule
        to continue.
        """
        if node is None:
            return " " * self.spine
        legs = self._legs_for(node)
        siblings = self.by_parent.get(node.parent_address, [])
        below = TREE_STOP if siblings and siblings[-1] is node else TREE_PIPE
        # Its ANCESTORS' rules only. The rule ends at the PCIe device itself:
        # the drives under it are that device's own table rather than another
        # level of fabric, so nothing continues into their column.
        return "".join([*legs[:-1], below]).ljust(self.spine)[: self.spine]

    def rules_before(self, node: PciNode) -> str:
        """The rules a line drawn just ABOVE this device carries.

        Its ancestors' rules unchanged, and in the device's own column the
        rule that leads down into it - because between a device and its next
        sibling the rule at that column is live, and a header line drawn in
        between must not break it.
        """
        legs = self._legs_for(node)
        return "".join([*legs[:-1], TREE_PIPE]).ljust(self.spine)[: self.spine]

    def hop_legend(self) -> str:
        """Spell out the hop symbols THIS section drew, or say nothing.

        Collected from the rows rather than from the density or the platform,
        so a machine whose every hop was read is not told what a dash means and
        a section that drew one always is.
        """
        drawn = {text for node, _level in self.drawn() for text, _style in hop_cells(node)}
        return theme.hop_legend(drawn)

    def row(self, node: PciNode, findings: Sequence[Finding]) -> Text:
        """One structure row: marker, spine, then the fields this width holds.

        The severity marker leads, exactly as every table's rows lead, so a
        narrow terminal cannot strand it on a line of its own - the failure
        test_no_width_strands_a_severity_marker_on_its_own_line exists for.
        The fields after the spine are the section's, not this row's, so the
        header above them labels exactly what every row drew.
        """
        severity = worst_severity(findings, node.address)
        line = Text()
        line.append(theme.marker_for(severity).ljust(_MARKER_WIDTH), style=theme.style_for(severity))
        line.append(self._spine_for(node))
        _append_fields(line, self.fields, self._cells(node))
        return line

    def _spine_for(self, node: PciNode) -> str:
        """This row's rules: its legs, then a turn or a leader to the columns.

        The turn (:data:`TREE_DOWN`) sits in the first character after the
        legs, which is exactly the column this device's own children draw their
        glyph in, so a row says on its own line that a device hangs below it.
        Only a DEVICE: the drives under a controller are that controller's own
        table rather than another level of fabric, so the rule ends at the PCIe
        device and the block below it carries its ancestors' rules alone.

        The rest is a leader to the columns after the spine, which is otherwise
        blank padding: a shallow row's glyph and its address sat at opposite
        ends of it with nothing joining them.
        """
        legs = "".join(self._legs_for(node))
        carries = bool(self.by_parent.get(node.address))
        lead = (TREE_DOWN if carries else TREE_LEAD) + TREE_LEAD * self.spine
        # One blank at the end, so the leader stops short of the address rather
        # than running into it.
        return (legs + lead)[: max(self.spine - 1, 0)].ljust(self.spine)

    def _cells(self, node: PciNode) -> dict[str, theme.Cell]:
        """What this device puts in each field, styled."""
        capable, running = hop_cells(node)
        tag = theme.pci_tag(node.port_kind)
        return {
            "address": (node.address, theme.STYLE_IDENTIFIER),
            "capable": capable,
            "running": running,
            "name": (node.name + (f"  ({tag})" if tag else ""), ""),
        }

    def measure(self, inventory: Inventory) -> Layout:
        """Fit the disk columns ONCE over every disk drawn anywhere.

        Passed the current level, so the fitting knows the reservation the
        spine costs it. The reservation is the same on every row: device rows
        and disk rows alike budget for the fullest spine, which is what keeps
        two disks on different controllers comparable straight down the page.

        Fitted over the virtual devices too when they are listed, because they
        are drawn through this same layout: fitting without them sized the
        columns for the drives alone and then clipped every virtual row into
        them, so the same machine read one way here and another in the table
        the old tree draws.
        """
        listed = (*inventory.disks, *inventory.virtual_disks) if self.expand_virtual else inventory.disks
        rows = [disk_cells(disk, inventory.port_link_for(disk)) for disk in listed]
        available = max(self.width - _MARKER_WIDTH - self.spine, _MIN_COLUMNS_WIDTH)
        return Layout.for_rows(DISK_COLUMNS, rows, available)

    def disk_row(
        self,
        disk: Disk,
        layout: Layout,
        findings: Sequence[Finding],
        inventory: Inventory,
        rules: str = "",
    ) -> Text:
        """One disk's row under its controller's fabric row.

        Marker, the rules of the controller above it, then the globally fitted
        columns. The rules rather than a blank, because a reader following one
        down the page lost it at every disk block and had to trust it came back
        in the right column; and no branch glyph of the disk's own, because the
        spine is sized for the deepest DEVICE level and a disk sits one deeper.

        Matches report._disk_line's field order so the two trees' rows read as
        one shape: marker, gutter, then columns.
        """
        line = Text()
        severity = worst_severity(findings, disk.path)
        line.append(theme.marker_for(severity).ljust(_MARKER_WIDTH), style=theme.style_for(severity))
        line.append(rules)
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


class FabricLine(NamedTuple):
    """One drawn line of the fabric section, and the thing it is about.

    ``subject`` is ``None`` for a line that describes the section rather than a
    device: the density note, the hop legend, a column header, a blank, the
    kernel-virtual tally. The board line is NOT one of those - it is about the
    machine, and its subject is the inventory.
    """

    text: Text
    subject: FabricSubject | None


def fabric_lines(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int = DEFAULT_WIDTH,
    view: FabricView = DEFAULT_VIEW,
) -> tuple[FabricLine, ...]:
    """Every line the fabric section draws, each paired with what it is about.

    The one place the section's lines are decided. :func:`render_fabric` prints
    these and the interactive page makes each one selectable, so the printed
    tree and the one a reader moves a cursor through cannot become two trees -
    the law ``device_fields`` and ``_append_fields`` already follow for a row's
    columns, applied to the section.

    Args:
        inventory: The machine.
        findings: The findings, used to mark affected rows.
        width: Width to lay out inside.
        view: How this view draws it.

    Returns:
        The lines, or an empty tuple for a capture carrying no PCI reading at
        all - that machine's storage is drawn by the old disk-and-controller
        tree instead, which :func:`render_fabric` still returns whole.
    """
    if not inventory.pci_tree:
        return ()
    fabric = Fabric(inventory.pci_tree, width, view.density, expand_virtual=view.expand_virtual)
    layout = fabric.measure(inventory)
    attached: set[str] = set()
    out = [*_fabric_head(inventory, fabric, view)]
    out += _fabric_devices(inventory, findings, fabric, layout, attached)
    out += _fabric_orphans(inventory, findings, fabric, layout, attached)
    out += [
        FabricLine(line, None)
        for line in _virtual_block(fabric, inventory, findings, layout, expand_virtual=view.expand_virtual)
    ]
    return tuple(out)


def _fabric_head(inventory: Inventory, fabric: Fabric, view: FabricView) -> list[FabricLine]:
    """The note, the legend it needs, and the board the whole fabric hangs off."""
    out = [FabricLine(Text(density_note(view.density, view.how_to_change), style=theme.STYLE_NOTE), None)]
    legend = fabric.hop_legend()
    if legend:
        out.append(FabricLine(Text(legend, style=theme.STYLE_UNKNOWN), None))
    out.append(FabricLine(board_line(inventory, fabric), inventory))
    return out


def _fabric_devices(
    inventory: Inventory,
    findings: Sequence[Finding],
    fabric: Fabric,
    layout: Layout,
    attached: set[str],
) -> list[FabricLine]:
    """Every drawn device, with the drives of a storage controller under it."""
    out: list[FabricLine] = []
    labelled = False
    for node, _level in fabric.drawn():
        if not labelled:
            # Again after a disk block has come between, for the reason the disk
            # header already repeats per controller: on a machine with several,
            # one header at the top ends up twenty lines from its own columns.
            out.append(FabricLine(device_header_line(fabric, fabric.rules_before(node)), None))
            labelled = True
        out.append(FabricLine(fabric.row(node, findings), node))
        disks = inventory.disks_on(node.address) if node.is_storage else ()
        if not disks:
            continue
        attached.update(disk.node for disk in disks)
        rules = fabric.rules_under(node)
        out.append(FabricLine(disk_header_line(fabric, layout, rules), None))
        out += [FabricLine(fabric.disk_row(disk, layout, findings, inventory, rules), disk) for disk in disks]
        labelled = False
    return out


def _fabric_orphans(
    inventory: Inventory,
    findings: Sequence[Finding],
    fabric: Fabric,
    layout: Layout,
    attached: set[str],
) -> list[FabricLine]:
    """Drives the capture could not put on any controller it drew."""
    orphans = [disk for disk in inventory.disks if disk.node not in attached]
    if not orphans:
        return []
    return [
        FabricLine(Text(""), None),
        FabricLine(Text("not attached to a known controller", style=theme.STYLE_UNKNOWN), None),
        FabricLine(disk_header_line(fabric, layout), None),
        *(FabricLine(fabric.disk_row(disk, layout, findings, inventory), disk) for disk in orphans),
    ]


def render_fabric(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int = DEFAULT_WIDTH,
    view: FabricView = DEFAULT_VIEW,
) -> RenderableType:
    """The whole topology section: the root-down fabric, the disks on each of
    its storage controllers, and the kernel-virtual tally behind them.

    Built from :func:`fabric_lines`, which the interactive page also reads, so
    the printed tree and the selectable one are one tree.

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
    lines = fabric_lines(inventory, findings, width, view)
    if lines:
        return Group(*(line.text for line in lines))
    # No PCI devices at all, and nothing the disk-and-controller table would
    # show either: say the machine is empty rather than rendering a blank
    # section.
    if not (inventory.disks or inventory.controllers or inventory.virtual_disks):
        return Text("No storage controllers or disks found.", style=theme.STYLE_UNKNOWN)
    # A capture with drives but no PCI reading: the disk-and-controller table
    # is the whole section, so the machine's storage is still shown.
    return _no_pci_fallback(inventory, findings, width, expand_virtual=view.expand_virtual)


def board_line(inventory: Inventory, fabric: Fabric) -> Text:
    """The top line of the tree: the board the whole fabric hangs off.

    Every root complex is a port of the processor on this board, so a tree
    whose first line is a bus label begins one level below the thing that
    explains it. The line says only what the capture carries: the board where
    DMI named it and the machine where it did not, the root complexes by their
    own labels, the best link the board's OWN root ports publish, and how many
    PCI devices the machine holds. A platform that publishes no bridge
    registers - which is every Windows machine - gets no PCIe figure rather
    than a guess, for the same reason an unread hop is not a measured one.

    Args:
        inventory: The machine.
        fabric: This render's assembled tree, for the roots it drew.

    Returns:
        The board line.
    """
    line = Text(inventory.board or inventory.hostname, style=theme.STYLE_IDENTIFIER)
    # Every root complex the MACHINE has, not the ones this density drew: how
    # much of the fabric is on screen is a display choice, and a board does not
    # lose a root complex because the view is not showing what hangs off it.
    roots = [node.address for node in fabric.nodes if node.is_root]
    if roots:
        complexes = "root complex" if len(roots) == 1 else "root complexes"
        line.append(f"   {len(roots)} {complexes} ({', '.join(roots)})")
    best = _best_root_port(fabric.nodes)
    if best is not None:
        line.append(f"   root ports to PCIe {best}")
    line.append(f"   {sum(1 for node in fabric.nodes if not node.is_root)} PCI devices")
    return line


def _best_root_port(nodes: Sequence[PciNode]) -> str | None:
    """The best link any root port on this board publishes, as PCIe text.

    Read from the ports the processor itself owns rather than from every
    bridge, because a switch downstream port describes a card on the board and
    not the board. ``None`` where no root port published a capability at all.
    """
    published = [
        node.link
        for node in nodes
        if node.port_kind is PciPortKind.ROOT and node.link.max_speed_gtps is not None and node.link.max_width
    ]
    if not published:
        return None
    best = max(published, key=lambda link: (link.max_speed_gtps or 0.0, link.max_width or 0))
    return theme.format_pcie_decimal(best.max_speed_gtps, best.max_width)


def _virtual_block(
    fabric: Fabric,
    inventory: Inventory,
    findings: Sequence[Finding],
    layout: Layout,
    *,
    expand_virtual: bool,
) -> list[Text]:
    """The kernel-virtual group: a tally, or the devices themselves.

    Folded away by default because a host with forty zvols would otherwise
    bury the drives this view exists to show; the count is always said, never
    hidden. One function here so this tree and any replay agree with the
    printed page about the same machine.
    """
    if not inventory.virtual_disks:
        return []
    lines: list[Text] = [Text(""), Text(VIRTUAL_HEADING, style=theme.STYLE_UNKNOWN)]
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


def disk_header_line(fabric: Fabric, layout: Layout, rules: str = "") -> Text:
    """The disk column header, offset by marker and spine like every row.

    Copied from report's own with the gutter widened to the spine: the header
    has to sit exactly above the cells it labels, which sit spine characters
    further right than they did in the old tree. It carries the same rules as
    the rows below it, so the block is one shape rather than a gap followed by
    a resumption.
    """
    line = Text()
    line.append(" " * _MARKER_WIDTH)
    line.append(rules or " " * fabric.spine)
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
    "DEVICE_COLUMNS",
    "HOP_WIDTH",
    "KEY_HINT",
    "OPTION_HINT",
    "Fabric",
    "FabricLine",
    "FabricSection",
    "FabricView",
    "Field",
    "board_line",
    "density_note",
    "device_fields",
    "device_header_line",
    "fabric_lines",
    "hop_cells",
    "render_fabric",
]
