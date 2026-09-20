"""The interactive view: one page per question, over a single scan.

The scan runs once and every page reads the same inventory, so the pages cannot
disagree with one another, and moving between them costs nothing.

Sizing uses fractional and automatic units throughout, with no fixed widths, so
a resize reflows rather than clips.  That is verified by tests that drive the
app at several terminal sizes rather than by looking at it once.

System Role:
    Adapter layer, presentation.  Consumes domain objects; decides nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from ... import __init__conf__
from ...domain.diagnostics import count_by_severity, diagnose
from ...domain.enums import CliCommand, Severity, TreeDensity
from ...domain.history import History

# Imported at runtime, not only for typing: the topology page decides which
# record to build from what KIND of thing a line is about, and an isinstance
# needs the class rather than its name.
from ...domain.models import Disk, Inventory, PciNode
from ...domain.thresholds import DEFAULT_THRESHOLDS, Thresholds
from ..config.tunables import DisplaySettings
from ..render import detail, layout, report, tables, theme
from ..render.tree import fabric_lines
from ..render.trend import TREND_COLUMNS, render_trend, trend_rows
from . import palette as tui_palette
from .typed_table import raising_table_id, rows_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType
    from textual import events

    from ...domain.models import Finding
    from ..render.detail import Detail
    from ..render.layout import Column
    from ..render.rows import MarkedRow, Row
    from ..render.tree import FabricLine, FabricSubject, FabricView

# Column sets per page. Every one DERIVES from the printed table of the same
# name rather than restating it, because a page and the command of one name are
# one view and a second tuple cannot be kept in step by hand. Measured before
# they did: the controllers page was missing `free` and `load` and meant
# something else by `ports`, and the health page identified a drive by path
# alone, having dropped `model` - the same defect the disk page had already
# been fixed for, still live in two more pages. The leading empty label is the
# severity marker, which the printed tables draw as a fixed gutter instead.
CONTROLLER_COLUMNS = ("", *(column.title for column in tables.CONTROLLER_COLUMNS))
# Taken from the printed table rather than restated, because a page and the
# command of one name are one view. Restating it is how the page came to name a
# drive by model alone: its own tuple was written without `serial` and
# `firmware`, and nothing compared the two lists. The leading empty label is the
# severity marker, which the printed table draws as a fixed gutter instead.
DISK_COLUMNS = ("", *(column.title for column in tables.DISK_COLUMNS))
HEALTH_COLUMNS = ("", *(column.title for column in tables.HEALTH_COLUMNS))
SLOT_COLUMNS = tuple(column.title for column in report.SLOT_COLUMNS)
# Derived from the printed view's own columns rather than restated, for the
# reason DISK_COLUMNS is: a page and the command of one name are one view, and a
# second tuple is how the disk page came to name a drive by model alone.
TREND_PAGE_COLUMNS = tuple(column.title for column in TREND_COLUMNS)


def _cell(text: str, style: str = "") -> Text:
    """Render one table cell, in the interactive palette.

    Cells are Rich text rather than plain strings so a table can carry the same
    meaning the printed report does. A plain string reaches the terminal
    unstyled, which silently drops every severity signal the render layer
    computed.

    The colour is swapped here and not upstream: the render layer writes one
    vocabulary and this is the single door every cell of every page comes
    through, so a page cannot be drawn in the printed palette by forgetting.
    """
    return Text(text, style=tui_palette.restyle(style))


def _note() -> Text:
    """The sentence under the trend table, which the printed view prints too."""
    return Text(
        "Rates are per power-on hour of the drive itself, so a machine that "
        "spends most of its time switched off still reports a meaningful figure.",
        style=tui_palette.restyle(theme.STYLE_UNKNOWN),
    )


def _marked(row: MarkedRow, columns: Sequence[Column]) -> list[Text]:
    """A printed table's row as this app's cells: the marker, then each column.

    The one place a MarkedRow becomes a page's row, so a page cannot pick its
    cells out of the printed row by hand and leave one behind.
    """
    return [_cell(*row.marker), *(_cell(*row.cells[column.key]) for column in columns)]


def _disk_cell(cells: Row, column: Column) -> Text:
    """One disk-page cell, cut to its column's ceiling when it has one.

    The wwn column is the only one with a ceiling today, but nothing here names
    it: a value is clipped because its own column carries a ``max_width``, not
    because of which key it happens to be, so a second column gaining a ceiling
    would be cut here without this function changing at all.
    """
    text, style = cells.get(column.key, ("-", theme.STYLE_UNKNOWN))
    if column.max_width is not None:
        text = layout.clip(text, column.max_width)
    return _cell(text, style)


def fabric_view_for(display: DisplaySettings) -> FabricView:
    """How this app draws the fabric, from the settings the page is showing.

    One place, because the printed section and the selectable list are the same
    section: a second spelling of these three values is how one of them would
    start drawing a different tree.
    """
    from ..render import tree  # noqa: PLC0415 - keeps rich render off app import

    return tree.FabricView(
        density=display.tree_density,
        expand_virtual=display.expand_virtual,
        how_to_change=tree.KEY_HINT,
        header_style=tui_palette.PALETTE.header_style,
    )


def render_fabric_for(
    inventory: Inventory,
    findings: Sequence[Finding],
    display: DisplaySettings,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> RenderableType:
    """The topology page's body, from the settings the page is showing.

    One helper rather than three call sites spelling the same arguments,
    because the density the page cycles with ``d``, the tally the
    ``--expand-virtual`` flag controls and the inventory are already agreed on
    here: a page and the printed command of its name are one view, and a
    second delivery path is how one of them goes deaf.

    The section lays itself out at the width the page gives it rather than at a
    width chosen here, because a window's width is known only once the layout
    has run and changes again on every resize.
    """
    from ..render import tree  # noqa: PLC0415 - keeps rich render off app import

    return tree.FabricSection(inventory, findings, fabric_view_for(display), thresholds)


#: The short label each page carries in the footer, in number-key order. Keyed by
#: the command rather than by a string, so the page ids cannot drift from
#: :class:`CliCommand` and a new page without a label fails loudly at import.
PAGE_LABELS: Final[dict[CliCommand, str]] = {
    CliCommand.TOPOLOGY: "Topology",
    CliCommand.CONTROLLERS: "Ctrl",
    CliCommand.DISKS: "Disks",
    CliCommand.HEALTH: "Health",
    CliCommand.SMART: "SMART",
    CliCommand.FINDINGS: "Findings",
    CliCommand.SLOTS: "Slots",
    CliCommand.TREND: "Trend",
}


#: Which group of the record each page wants read first. The panel's CONTENT is
#: the subject's and never the page's, so a drive says the same thing wherever it
#: is selected; what a page chooses is only what is answered at the top, which is
#: its own question. A label here that names no group at all raises rather than
#: being ignored - see :func:`detail.order_groups`.
DETAIL_ORDER: Final[dict[CliCommand, tuple[str, ...]]] = {
    CliCommand.TOPOLOGY: (detail.LINK, detail.PLACE),
    CliCommand.CONTROLLERS: (detail.LINK, detail.UPSTREAM, detail.PORTS),
    CliCommand.DISKS: (detail.IDENTITY, detail.LINK),
    CliCommand.HEALTH: (detail.HEALTH, detail.COUNTERS),
    CliCommand.SLOTS: (detail.SLOT, detail.OCCUPANT),
    CliCommand.TREND: (detail.COUNTERS, detail.HEALTH),
}

#: Which table on which page puts what under the cursor. One mapping rather than
#: a chain of ``if`` in the handler, so a page added without an entry shows the
#: empty panel loudly instead of silently keeping the previous page's answer.
#: Every id a pane may carry, so the one conversion from Textual's own wire
#: string can answer "not a page" instead of raising on one.
PAGE_IDS: Final[frozenset[str]] = frozenset(page.value for page in CliCommand)

DETAIL_TABLES: Final[dict[str, CliCommand]] = {
    "controller-table": CliCommand.CONTROLLERS,
    "disk-table": CliCommand.DISKS,
    "health-table": CliCommand.HEALTH,
    "slot-table": CliCommand.SLOTS,
    "trend-table": CliCommand.TREND,
}


class FabricList(OptionList):
    """The topology page's list of fabric lines, which says when it has a width.

    The section has to be laid out at exactly the width this widget gives its
    options, or a row that uses the whole width wraps onto a second line. That
    width is known only once the layout has run, and the App cannot see that
    moment: its own ``Resize`` arrives before its children are placed, so a fill
    driven from there uses the window's width and overruns by the scrollbar's
    two columns. The widget is the only thing that knows, so it says.
    """

    class Resized(Message):
        """This list has been placed, and its options can be laid out to fit."""

        def __init__(self, width: int) -> None:
            """Carry the WIDTH rather than the widget.

            The message can be handled after the screen has gone, on the way out
            of a run, and a handler that reached back for the widget would raise
            there.
            """
            super().__init__()
            self.width = width

    def on_resize(self, event: events.Resize) -> None:
        """Tell the app to lay the fabric out again at the width now known."""
        del event
        self.post_message(self.Resized(self.scrollable_content_region.width))


class LsdskApp(App[None]):
    """The lsdsk terminal application."""

    CSS = """
    Screen { layout: vertical; }
    #verdict { height: auto; padding: 0 1; }
    TabbedContent { height: 1fr; }
    DataTable { height: 1fr; width: 1fr; }
    #wwn-row { height: 2; padding: 0 1; }
    #wwn-label { width: auto; padding: 0 1 0 0; }
    #wwn-strip { height: 2; overflow-x: auto; overflow-y: hidden; }
    #wwn-full { width: auto; text-wrap: nowrap; }
    /* A CEILING, not a height: a short record takes the lines it needs and a
       long one scrolls inside the panel, so the table above never loses more of
       the window than the record actually uses. The percentage comes from
       display.detail_height_percent at mount. */
    /* Fenced on BOTH sides. With a rule above and nothing below, the record
       ran straight into the key bar and the two read as one block. */
    #detail {
        height: auto; overflow-y: auto; overflow-x: hidden; padding: 0 1;
        border-top: solid $panel; border-bottom: solid $panel;
    }
    /* No padding and no border: the section draws its own spine and is laid out
       to the width this leaves, so a character taken here would wrap a row.
       scrollbar-gutter: stable is load-bearing for the same reason and is the
       harder half - without it the fill happens at the full width, the options
       then overflow, the scrollbar appears and takes two columns, and every row
       that used all of them wraps. Measured: 45 rendered rows for 38 lines at
       width 100. Reserving the gutter makes the width the fill is laid out at
       the width the fill is drawn at, whether the bar is needed or not. */
    #tree-lines { height: 1fr; width: 1fr; padding: 0; border: none; background: $surface; }
    #tree-lines { scrollbar-gutter: stable; }
    #tree-fallback { height: 1fr; width: 1fr; }
    /* Nothing in this view is dimmed. A repeated column header is an option the
       cursor must skip, so it is disabled, and Textual draws a disabled option
       at alpha 0.38 - which dimmed the tree's vertical rules for the height of
       every header they ran through, and the tree looked broken there. Both
       component classes are named so the two are equal BY CONSTRUCTION rather
       than by both happening to resolve to the same default. */
    #tree-lines > .option-list--option { color: $foreground; }
    #tree-lines > .option-list--option-disabled { color: $foreground; }
    /* A header is a header wherever it is drawn. The tree draws its own in the
       palette's hue and the card draws its group labels in it; this is the
       third place one appears, and it is Textual's to draw, so it is said here
       rather than in a cell. */
    DataTable > .datatable--header { color: $lsdsk-header; text-style: bold; }
    """

    # Laid out the way the *top family works, because that is the muscle memory
    # anyone reaching for this already has: a number or the matching function key
    # switches page, q quits, and the footer lists them so nothing has to be
    # learned from a manual. priority=True so a focused table cannot swallow them.
    BINDINGS: ClassVar[list[Binding]] = [  # pyright: ignore[reportIncompatibleVariableOverride] - Textual declares BINDINGS as a wider class variable than the list of Binding it documents
        *(
            Binding(f"{number},f{number}", f"show('{command.value}')", label, priority=True)
            for number, (command, label) in enumerate(PAGE_LABELS.items(), start=1)
        ),
        Binding("tab,right", "next_page", "Next", priority=True, show=False),
        Binding("shift+tab,left", "prev_page", "Prev", priority=True, show=False),
        Binding("comma", "wwn_left", "WWN <", priority=True),
        Binding("full_stop", "wwn_right", "WWN >", priority=True),
        Binding("d", "tree_density", "Density", priority=True),
        Binding("i", "toggle_detail", "Detail", priority=True),
        Binding("shift+down", "detail_down", "Detail v", priority=True),
        Binding("shift+up", "detail_up", "Detail ^", priority=True),
        Binding("r,f9", "rescan", "Rescan", priority=True, show=False),
        Binding("q,f10,escape", "quit", "Quit", priority=True),
    ]

    #: Page order, used by the number keys and by cycling with tab. Taken from
    #: the command enum so the two surfaces cannot drift: a page and its
    #: command are one view under one name. Held as the MEMBERS rather than
    #: their wire strings, so the navigation arithmetic never leaves the enum's
    #: own space; a ``TabbedContent.active`` id is converted back at the one
    #: point that reads or writes it.
    PAGES: ClassVar[tuple[CliCommand, ...]] = tuple(CliCommand)

    def __init__(
        self,
        inventory: Inventory,
        history: History | None = None,
        *,
        display: DisplaySettings | None = None,
        store_refusal: str | None = None,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
    ) -> None:
        """Build the app around one already-collected inventory.

        Args:
            inventory: The machine to show.
            history: Counter samples recorded on earlier runs. Without them the
                trend page explains that there is nothing to compare yet, and
                every other page reads exactly as it did before.
            display: How the pages are laid out and where their cut-offs
                sit. One object rather than a keyword per value, because every
                page must answer from the same settings the printed commands
                used: a page and the command of the same name are one view
                under one name, and a second delivery path is how one of them
                goes deaf.
            thresholds: What this run judges by, so the wear cell on the disk
                and health pages is coloured against the same figures the
                findings beside it were graded with - a page and the printed
                command of its name are one view.
            store_refusal: Why the counter store could not be read, when it
                could not. There is no stderr behind a full-screen page, so a
                refusal that is only warned about reaches nobody here and the
                trend page would report the machine as one nothing has ever
                been recorded on.
        """
        super().__init__()
        self.inventory = inventory
        # NOT self.display: Textual's DOMNode already owns that name as the
        # show/hide property, and shadowing it breaks rendering.
        self.display_settings = display if display is not None else DisplaySettings()
        self.thresholds = thresholds
        self.history: History = history if history is not None else History(hostname=inventory.hostname)
        self.store_refusal = store_refusal
        self.findings: tuple[Finding, ...] = diagnose(inventory, history=history)
        #: The uncut WWN of each listed drive, by row key. The cell is clipped
        #: to the column width, so the whole of it has to be kept somewhere for
        #: the strip to show; reading it back off the cell would only return
        #: what was already cut.
        self._wwn_of: dict[str, str | None] = {}
        #: Every listed drive by the key its rows carry, so the panel can answer
        #: for the disk page and the health page from one lookup rather than two
        #: that could disagree about which drive a row is.
        self._disk_of: dict[str, Disk] = {}
        #: Where each table's cursor was left. Recorded for EVERY table, not only
        #: the visible one, because switching page moves no cursor and raises no
        #: row event: without this the panel would keep answering for the page
        #: the reader just left until they pressed an arrow key.
        self._row_of: dict[str, str | None] = {}
        #: The fabric's drawn lines, in the order the topology list holds them,
        #: so an option index resolves to the device that line is about.
        self._tree_lines: tuple[FabricLine, ...] = ()
        #: The width the lines in hand were laid out at, so a resize that did
        #: not actually change it costs nothing and cannot loop.
        self._tree_width_used = 0
        #: Where the topology cursor was left, for the same reason every table's
        #: row is remembered: coming back to the page raises no highlight.
        self._tree_highlighted: int | None = None
        self.title = f"lsdsk {__init__conf__.version}"
        self.sub_title = inventory.hostname

    def compose(self) -> ComposeResult:
        """Lay out the header, the summary, the pages and the footer."""
        yield Header()
        yield Static(self.verdict_line(), id="verdict")
        with TabbedContent(initial=CliCommand.TOPOLOGY.value):
            with TabPane("Topology", id=CliCommand.TOPOLOGY.value):
                # One option per drawn line, so a reader can put the cursor on a
                # device and the panel can answer for it. The old Static is kept
                # beside it for the capture that carries no PCI reading at all,
                # whose section is the disk-and-controller table rather than a
                # list of fabric lines; exactly one of the two is ever shown.
                yield FabricList(id="tree-lines")
                with VerticalScroll(id="tree-fallback"):
                    yield Static(id="tree")
            with TabPane("Controllers", id=CliCommand.CONTROLLERS.value):
                yield DataTable[str](id="controller-table", zebra_stripes=True, cursor_type="row")
            with TabPane("Disks", id=CliCommand.DISKS.value), Vertical():
                yield DataTable[str](id="disk-table", zebra_stripes=True, cursor_type="row")
                # A clipped cell has to stay reachable, and a DataTable cell
                # cannot hold a widget, so the whole identifier of the drive
                # under the cursor lives here. The strip is exactly as wide as
                # the column, which is what makes its scrollbar appear on the
                # same values the column had to cut and on no others.
                with Horizontal(id="wwn-row"):
                    yield Static("wwn", id="wwn-label")
                    with HorizontalScroll(id="wwn-strip"):
                        yield Static(id="wwn-full")
            with TabPane("Health", id=CliCommand.HEALTH.value):
                yield DataTable[str](id="health-table", zebra_stripes=True, cursor_type="row")
            with TabPane("SMART", id=CliCommand.SMART.value), VerticalScroll():
                yield Static(id="smart-body")
            with TabPane("Findings", id=CliCommand.FINDINGS.value), VerticalScroll():
                yield Static(id="findings-body")
            with TabPane("Slots", id=CliCommand.SLOTS.value), Vertical():
                yield DataTable[str](id="slot-table", zebra_stripes=True, cursor_type="row")
                yield Static(tui_palette.Recoloured(report.form_factor_note()), id="slot-note")
            with TabPane("Trend", id=CliCommand.TREND.value), Vertical():
                # A table rendered as text until now, which is what it
                # structurally is: one row per counter of one drive. As a table
                # its rows can be selected, and the panel can answer for the
                # drive a row is about.
                yield DataTable[str](id="trend-table", zebra_stripes=True, cursor_type="row")
                # The note under it, and the whole section when there is no
                # history yet: an empty table would say nothing about WHY.
                yield Static(id="trend-body")
        # Outside the TabbedContent, so it is one widget with one handler rather
        # than a copy per page free to answer differently, and so it survives a
        # page switch with the row the reader left it on.
        with VerticalScroll(id="detail"):
            yield Static(id="detail-body")
        yield Footer()

    def get_css_variables(self) -> dict[str, str]:
        """Publish the interactive palette to the stylesheet.

        A literal hue in the CSS would be a second copy of a value
        ``tui_palette.PALETTE`` already holds, free to drift from the one every
        cell is recoloured with - and the contrast gate measures the palette,
        not the stylesheet, so the drift would be invisible to it.

        Returns:
            Textual's own variables, plus this app's.
        """
        return {**super().get_css_variables(), "lsdsk-header": tui_palette.PALETTE.header}

    def on_mount(self) -> None:
        """Fill every table once the widgets exist."""
        # Sized here rather than in the stylesheet for the reason the wwn strip
        # is: a literal in the CSS would be a second copy of a configured number.
        self.query_one("#detail", VerticalScroll).styles.max_height = f"{self.display_settings.detail_height_percent}%"
        self._fill_controllers()
        self._fill_disks()
        self._fill_health()
        self._fill_slots()
        self._fill_trend()
        self._fill_smart()
        self._fill_findings()
        self._refill_tree()

    def _fill_smart(self) -> None:
        """Draw the SMART page, in this view's palette."""
        self.query_one("#smart-body", Static).update(tui_palette.Recoloured(report.render_smart(self.inventory)))

    def _fill_findings(self) -> None:
        """Draw the findings page, in this view's palette.

        One place, called at mount and again on a rescan. Written out at BOTH
        it was written differently: the palette reached the rescan's copy and
        not the one a reader actually opens the app on.
        """
        self.query_one("#findings-body", Static).update(tui_palette.Recoloured(report.render_findings(self.findings)))

    def verdict_line(self) -> str:
        """Summarise the findings in one line for the banner.

        Returns:
            The banner text.
        """
        if not self.findings:
            return "No problems found."
        counts = count_by_severity(self.findings)
        parts = [
            f"{counts[severity]} {theme.SEVERITY_LABELS[severity]}"
            for severity in (Severity.CRITICAL, Severity.WARNING, Severity.HINT)
            if counts[severity]
        ]
        return "PROBLEMS   " + "   ".join(parts)

    def _fill_controllers(self) -> None:
        """Populate the controller page."""
        table = rows_of(self.query_one("#controller-table"))
        table.add_columns(*CONTROLLER_COLUMNS)
        for controller in self.inventory.controllers:
            row = tables.controller_table_row(controller, self.inventory, self.findings, bandwidth=True)
            table.add_row(*_marked(row, tables.CONTROLLER_COLUMNS), key=controller.address)

    def _fill_slots(self) -> None:
        """Populate the mainboard slot page."""
        table = rows_of(self.query_one("#slot-table"))
        table.add_columns(*SLOT_COLUMNS)
        # Built from the printed view's own row builder rather than spelled out
        # here. Spelled out, this page had drifted from the table of the same
        # name: it drew the decimal generation where the table drew the
        # marketing one for the identical port. A page and the command of one
        # name are one view, which is the rule the disk page already follows.
        for slot in self.inventory.slots:
            cells = report.slot_table_row(slot, bandwidth=True)
            table.add_row(
                *(_cell(*cells[column.key]) for column in report.SLOT_COLUMNS),
                key=slot.address,
            )

    def _fill_disks(self) -> None:
        """Populate the disk page."""
        table = rows_of(self.query_one("#disk-table"))
        table.add_columns(*DISK_COLUMNS)
        # Sized here rather than in the stylesheet so the strip and the clip read
        # one number. A literal in the CSS would be a second copy, free to drift
        # from the width the cell was cut to and quietly turn "the control
        # appears when something was hidden" into a near-miss either way.
        width = self.display_settings.wwn_width
        self.query_one("#wwn-strip", HorizontalScroll).styles.width = width
        self._wwn_of = {}
        self._disk_of = {}
        # The wwn ceiling lives on the column, exactly as the printed table
        # reads it, so the two views cannot cut a wwn in two different places.
        columns = tables.disk_columns(width)
        listed = (
            (*self.inventory.disks, *self.inventory.virtual_disks)
            if self.display_settings.expand_virtual
            else self.inventory.disks
        )
        for disk in listed:
            port = self.inventory.port_link_for(disk)
            cells = tables.disk_table_row(disk, port, bandwidth=True)
            severity = report.worst_severity(self.findings, disk.path)
            table.add_row(
                _cell(theme.marker_for(severity), theme.style_for(severity)),
                *(_disk_cell(cells, column) for column in columns),
                key=disk.node,
            )
            self._wwn_of[disk.node] = disk.wwn
            self._disk_of[disk.node] = disk
        # The cursor starts on the first row without raising anything a handler
        # would see on a rescan, so the strip is set from here rather than left
        # holding the previous scan's answer.
        self._show_wwn(next(iter(self._wwn_of.values()), None))

    def _show_wwn(self, value: str | None) -> None:
        """Put one drive's whole identifier in the strip, wound back to its start.

        Args:
            value: The identifier, or ``None`` for a drive that carries none.

        The rewind matters: an identifier scrolled to its end would otherwise
        leave the next, shorter one showing the blank space past it.
        """
        unread = tui_palette.restyle(theme.STYLE_UNKNOWN)
        self.query_one("#wwn-full", Static).update(Text(value or "-", style="" if value else unread))
        # Immediate, not Textual's default of after the next refresh. Offset 0 is
        # valid whatever the new identifier's width, so there is nothing to wait
        # for, and waiting costs a frame: the update that lays out the new text
        # paints it still scrolled and only then runs the queued rewind.
        self.query_one("#wwn-strip", HorizontalScroll).scroll_to(x=0, animate=False, immediate=True)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Follow the cursor: the whole WWN on the disk page, the record on any page.

        Args:
            event: The table and row the cursor moved to.

        Every page's table raises this, and at mount they all raise it in turn
        with the slot table last, so without the check the strip would settle on
        an answer the slot page gave to a question the disk page asked. The
        panel needs the same guard for a second reason: the cursor of a table
        the reader cannot see must not replace what is under the one they can.
        """
        table_id = raising_table_id(event) or ""
        page = DETAIL_TABLES.get(table_id)
        if page is CliCommand.DISKS:
            node = event.row_key.value
            self._show_wwn(self._wwn_of.get(node) if node is not None else None)
        if page is None:
            return
        self._row_of[table_id] = event.row_key.value
        if page is self._active_page():
            self._show_detail(self._record_for(page, event.row_key.value))

    def _active_page(self) -> CliCommand | None:
        """Which page is in front, or ``None`` when that is not a page.

        ``TabbedContent.active`` is Textual's own wire string and is EMPTY until
        the first pane is activated, so this is the one place the string becomes
        a member, and it answers ``None`` rather than raising: every caller is
        asking whether one particular page is up, and a pane that is not up yet
        is simply not that page.

        Returns:
            The page in front, or ``None`` before one is or if the active id
            names something that is not a page.
        """
        active = self.query_one(TabbedContent).active
        return CliCommand(active) if active in PAGE_IDS else None

    def _refresh_detail(self) -> None:
        """Ask the page now in front what its cursor is on.

        A page switch moves no cursor and raises no row event, so the panel has
        to be asked again here or it keeps answering for the page just left.
        """
        page = self._active_page()
        if page is CliCommand.TOPOLOGY:
            index = self._tree_highlighted
            self._show_detail(None if index is None else self._record_of(self._subject_at(index)))
            return
        for table_id, command in DETAIL_TABLES.items():
            if command is page:
                self._show_detail(self._record_for(command, self._row_of.get(table_id)))
                return
        self._show_detail(None)

    def _record_for(self, page: CliCommand, key: str | None) -> Detail | None:
        """The record behind one row, or nothing when the row names no subject.

        Args:
            page: Which page's table raised the move.
            key: The row key, which every table now carries.

        Returns:
            The record, or ``None``.
        """
        if key is None:
            return None
        if page is CliCommand.CONTROLLERS:
            controller = next((one for one in self.inventory.controllers if one.address == key), None)
            return None if controller is None else detail.controller_detail(controller, self.inventory)
        if page is CliCommand.SLOTS:
            slot = next((one for one in self.inventory.slots if one.address == key), None)
            return None if slot is None else detail.slot_detail(slot, self.inventory)
        # A trend row is one COUNTER of one drive, so its key names both; the
        # record is the drive's, because that is what the row is about.
        disk = self._disk_of.get(key.split("|")[0] if page is CliCommand.TREND else key)
        return None if disk is None else detail.disk_detail(disk, self.inventory, self.history, self.thresholds)

    def _show_detail(self, record: Detail | None) -> None:
        """Draw one record in the panel, with the active page's group first.

        Args:
            record: What the cursor is on, or ``None`` for a row naming nothing.

        The record itself is the subject's and never the page's: only the ORDER
        changes here, so one drive cannot read two ways on two pages.
        """
        body = self.query_one("#detail-body", Static)
        if record is None:
            body.update(Text("Nothing selected.", style=tui_palette.restyle(theme.STYLE_UNKNOWN)))
            return
        page = CliCommand(self.query_one(TabbedContent).active)
        ordered = record._replace(groups=detail.order_groups(record.groups, DETAIL_ORDER.get(page, ())))
        card = detail.render_detail(ordered, self.findings, header_style=tui_palette.PALETTE.header_style)
        body.update(tui_palette.Recoloured(card))
        # The panel is re-measured by the layout that draws it, so whether the
        # scroll keys apply has to be asked again rather than assumed unchanged.
        self.refresh_bindings()

    def _fill_trend(self) -> None:
        """Populate the trend page, or explain why it has nothing to show.

        The rows come from ``trend.trend_rows``, the same list the printed table
        draws, so the two views cannot end up showing different counters of one
        machine. Without a recorded past there are no rows at all, and the note
        under the table becomes the whole section: an empty table would say
        nothing about WHY it is empty, and "no counter has moved" and "nothing
        has been recorded yet" are answers a reader must be able to tell apart.
        """
        table = rows_of(self.query_one("#trend-table"))
        table.add_columns(*TREND_PAGE_COLUMNS)
        wear_floor = self.display_settings.wear_row_floor_percent
        rows = trend_rows(self.inventory, self.history, wear_floor)
        self.query_one("#trend-table", DataTable).display = bool(rows)
        for row in rows:
            table.add_row(
                *(_cell(*row.cells[column.key]) for column in TREND_COLUMNS),
                key=f"{row.disk.node}|{row.kind.value}",
            )
        self.query_one("#trend-body", Static).update(
            tui_palette.Recoloured(
                render_trend(self.inventory, self.history, wear_floor=wear_floor, store_refusal=self.store_refusal)
                if not rows
                else _note()
            )
        )

    def _fill_health(self) -> None:
        """Populate the health page.

        The cells come from the printed health table's own row builder, so the
        two views cannot disagree about one drive - including the trend mark on
        every counter, which the page used to recompute and which decides
        whether a still-rising count keeps its "+" and a count proved quiet
        drops out of red.
        """
        table = rows_of(self.query_one("#health-table"))
        table.add_columns(*HEALTH_COLUMNS)
        for disk in self.inventory.disks:
            row = tables.health_table_row(disk, self.inventory, self.findings, self.history, self.thresholds)
            table.add_row(*_marked(row, tables.HEALTH_COLUMNS), key=disk.node)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Give the newly shown page's table the keyboard.

        Textual's own focus key is `tab`, and this app binds that to switching
        pages, so nothing would otherwise move focus off the tab bar and a page
        could never be scrolled with the arrow keys. Page navigation does not
        depend on this: left and right are bound at app level with priority, so
        they keep working whatever holds focus.
        """
        del event
        pane = self.query_one(TabbedContent).active
        # The WWN keys belong to one page, so the footer has to be asked again
        # each time the page changes or it keeps offering them everywhere.
        self.refresh_bindings()
        self._refresh_detail()
        self._focus_page(pane)

    def _focus_page(self, pane: str) -> None:
        """Give the keyboard to whatever the page in front is driven by.

        Tried in order: a table, the topology's list of fabric lines, and last
        the scroll container of a page that is only long text - focusing that is
        what makes up and down move it, and without it findings and SMART
        ignored the keyboard entirely.

        A widget that is not DISPLAYED is skipped rather than focused: the
        topology page carries both the list and the no-PCI fallback and shows
        one, so focusing the first match found would hand the keyboard to the
        hidden one on every capture that has a fabric.
        """
        for selector in (f"#{pane} DataTable", f"#{pane} OptionList", f"#{pane} VerticalScroll"):
            shown = [widget for widget in self.query(selector) if widget.display]
            if shown:
                shown[0].focus()
                return

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Offer the WWN scroll keys on the page that has a strip to scroll.

        Args:
            action: The action a binding would run.
            parameters: Its arguments, unused here.

        Returns:
            ``True`` where the action applies, ``False`` to drop it from the
            footer. Textual reads ``False`` as hidden and ``None`` as shown but
            greyed, which is the opposite way round from what the names
            suggest.

        A key advertised on all eight pages that answers on one is a key a
        reader stops believing.
        """
        del parameters
        if action in {"detail_down", "detail_up"}:
            # Offered only where there is something to scroll TO, the same rule
            # the wwn strip's control follows: a key advertised on a panel that
            # already shows everything is a key that answers nothing.
            panel = self.query_one("#detail", VerticalScroll)
            return bool(panel.display) and panel.virtual_size.height > panel.size.height
        if action in {"wwn_left", "wwn_right"}:
            return self._active_page() is CliCommand.DISKS
        if action == "tree_density":
            # Gated to the page whose view it changes, the same shape the WWN
            # keys take: check_action refusing to dispatch an action also
            # hides it from the footer, so the key reads as topology-only.
            return self._active_page() is CliCommand.TOPOLOGY
        return True

    def action_tree_density(self) -> None:
        """Step to the next density, adding detail each press and wrapping at the top.

        The whole machine on the topology page is what the full density is
        for, and what four unrelated devices in five bury; cycling is what
        makes the reduced shapes a keypress away rather than a configuration
        edit, which is the decision that made the always-every-device tree
        livable.

        The climb is the ENUM's, not this function's: ``TreeDensity`` is
        declared from least detail to most and the shipped default is its
        first member, so stepping forward from wherever the reader is adds
        detail until it wraps back to the least. Both halves of that are held
        by tests, because either one alone leaves the sequence broken.
        """
        members = list(TreeDensity)
        current = self.display_settings.tree_density
        position = members.index(current) if current in members else 0
        next_density = members[(position + 1) % len(members)]
        self.display_settings = self.display_settings.with_changes(tree_density=next_density)
        self._refill_tree()

    def _refill_tree(self) -> None:
        """Redraw the topology page from the settings now held.

        The section is laid out at exactly the option list's own content width,
        which is the only thing that keeps one drawn line to one option: an
        option wider than its box WRAPS, and measured on textual 8.2.8 neither
        a Rich ``no_wrap`` nor a CSS ``text-wrap: nowrap`` prevents it. A window
        knows its width only after the layout runs, so this is called again on
        every resize rather than sized once.

        A capture with no PCI reading has no lines to list, and gets the old
        disk-and-controller section in the Static beside the list instead.
        """
        self._tree_width_used = self._tree_width()
        lines = fabric_lines(
            self.inventory, self.findings, self._tree_width_used, fabric_view_for(self.display_settings)
        )
        options = self.query_one("#tree-lines", OptionList)
        options.display = bool(lines)
        self.query_one("#tree-fallback", VerticalScroll).display = not lines
        self._tree_lines = lines
        if not lines:
            self.query_one("#tree", Static).update(
                tui_palette.Recoloured(
                    render_fabric_for(self.inventory, self.findings, self.display_settings, self.thresholds)
                )
            )
            return
        # Kept across the redraw, or a resize and a density change would both
        # throw the reader back to the first device every time.
        keep = options.highlighted
        options.clear_options()
        options.add_options(
            [
                Option(tui_palette.retext(line.text), id=str(index), disabled=line.subject is None)
                for index, line in enumerate(lines)
            ]
        )
        if keep is not None and keep < len(lines) and lines[keep].subject is not None:
            options.highlighted = keep
            return
        # Opened, or reopened on a line that is gone: start on the first thing
        # there is to say something about rather than on nothing, so the panel
        # below is answering from the moment the page appears.
        first = next((index for index, line in enumerate(lines) if line.subject is not None), None)
        if first is not None:
            options.highlighted = first

    def _tree_width(self) -> int:
        """Columns the fabric may draw in, as the list will actually offer them.

        ``scrollable_content_region``, never ``content_size``: the two differ by
        the vertical scrollbar's two columns, which ``content_size`` does not
        take off. Laid out at the wider figure, every row that used the whole
        width wrapped - measured as 45 rendered rows for 38 lines at a window of
        100 - while a wide window hid it because nothing was long enough to
        reach the edge. The gutter is reserved as stable in the stylesheet so
        this figure does not change when the bar appears.

        Before the first layout the region is empty, and a zero would lay the
        section out at its own minimum; the window's width is the best answer
        available then, and the resize that follows corrects it.
        """
        width = self.query_one("#tree-lines", OptionList).scrollable_content_region.width
        return width if width > 0 else self.size.width

    def on_fabric_list_resized(self, event: FabricList.Resized) -> None:
        """Lay the fabric out again for the width the list now offers.

        Terminates because the second fill changes no width: the list's region
        is decided by the layout, not by what is in it, and the scrollbar gutter
        is reserved whether or not the bar is needed.

        The widget is looked for rather than demanded, because this can arrive
        after the screen has been torn down.
        """
        if not self.query("#tree-lines") or event.width == self._tree_width_used:
            return
        self._refill_tree()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Follow the topology cursor with the record of the device it is on.

        Recorded whether or not the topology page is in front, and DRAWN only
        when it is: a refill can highlight its first line long after the reader
        has moved to another page - the list is refilled on the resize that
        follows the layout - and drawing then would replace the record of the
        row they are actually looking at. The same rule the tables follow, for
        the same reason.
        """
        if event.option_list.id != "tree-lines":
            return
        self._tree_highlighted = event.option_index
        if self._active_page() is CliCommand.TOPOLOGY:
            self._show_detail(self._record_of(self._subject_at(event.option_index)))

    @property
    def tree_lines(self) -> tuple[FabricLine, ...]:
        """The fabric lines the topology page is listing, in the list's order.

        Public because the pairing between an option and the thing it is about
        IS the page's contract: a reader that can only see the drawn text cannot
        tell a wrapped row from two devices, which is the mistake the section's
        previous guard made.
        """
        return self._tree_lines

    def _subject_at(self, index: int) -> FabricSubject | None:
        """What the fabric line at one option index is about."""
        return self._tree_lines[index].subject if 0 <= index < len(self._tree_lines) else None

    def _record_of(self, subject: FabricSubject | None) -> Detail | None:
        """The record for whatever a fabric line is about.

        A storage controller is on the fabric as a ``PciNode`` and in the
        inventory as a ``Controller``, and the second holds what a reader of
        that row wants - its uplink, its ports, what its drives demand - so the
        address is resolved to the controller where one exists.
        """
        if isinstance(subject, Inventory):
            return detail.machine_detail(subject)
        if isinstance(subject, Disk):
            return detail.disk_detail(subject, self.inventory, self.history, self.thresholds)
        if isinstance(subject, PciNode):
            controller = next((one for one in self.inventory.controllers if one.address == subject.address), None)
            if controller is not None:
                return detail.controller_detail(controller, self.inventory)
            return detail.node_detail(subject, self.inventory)
        return None

    def action_toggle_detail(self) -> None:
        """Hide the panel, or bring it back, giving the table the whole window.

        ``display`` is Textual's own show/hide property on the widget - the one
        the app deliberately does NOT use for its settings - so this is the one
        place that name means what it reads as.
        """
        panel = self.query_one("#detail", VerticalScroll)
        panel.display = not panel.display
        self.refresh_bindings()

    def action_detail_down(self) -> None:
        """Move further into a record too tall for the panel."""
        self.query_one("#detail", VerticalScroll).scroll_page_down(animate=False)

    def action_detail_up(self) -> None:
        """Move back towards the top of a record too tall for the panel."""
        self.query_one("#detail", VerticalScroll).scroll_page_up(animate=False)

    def action_wwn_left(self) -> None:
        """Wind the WWN strip back towards the start of the identifier."""
        self.query_one("#wwn-strip", HorizontalScroll).scroll_page_left(animate=False)

    def action_wwn_right(self) -> None:
        """Wind the WWN strip on towards the end of the identifier.

        A page at a time rather than a character: the longest identifier
        measured is a hundred characters against a column of twenty-four, which
        is four presses rather than seventy-seven.
        """
        self.query_one("#wwn-strip", HorizontalScroll).scroll_page_right(animate=False)

    def action_show(self, pane: str) -> None:
        """Switch to a page by name.

        Textual hands over the raw string from the binding, so this is the edge
        where it becomes a page. Resolving it through the enum means an id that
        is not a command raises here, rather than silently activating no tab.

        Args:
            pane: The page identifier, which must be a :class:`CliCommand` value.

        Raises:
            ValueError: If the identifier names no page.
        """
        self.query_one(TabbedContent).active = CliCommand(pane).value

    def action_next_page(self) -> None:
        """Move to the next page, wrapping at the end.

        ``tabs.active`` is Textual's own wire string, so it is parsed into the
        enum here and nowhere else, the same boundary ``action_tree_density``
        crosses for ``TreeDensity``; the arithmetic between the two ends stays
        in the enum's own space.
        """
        current = self._active_page() or self.PAGES[0]
        position = self.PAGES.index(current)
        self.query_one(TabbedContent).active = self.PAGES[(position + 1) % len(self.PAGES)].value

    def action_prev_page(self) -> None:
        """Move to the previous page, wrapping at the start.

        See :meth:`action_next_page` for why the wire string is parsed here.
        """
        current = self._active_page() or self.PAGES[0]
        position = self.PAGES.index(current)
        self.query_one(TabbedContent).active = self.PAGES[(position - 1) % len(self.PAGES)].value

    def action_rescan(self) -> None:
        """Re-run the diagnosis over the inventory already held.

        Every page is refilled, not just the two that used to be. Refreshing the
        banner and the findings while the tree, the tables and the SMART body
        kept their first-scan markers put two different verdicts on one screen.
        The history goes back in as well, or a rescan would quietly drop the
        escalation and de-escalation the first diagnosis had applied.
        """
        self.findings = diagnose(self.inventory, history=self.history)
        self.query_one("#verdict", Static).update(self.verdict_line())
        self._fill_findings()
        self._refill_tree()
        self._fill_smart()
        for table_id in ("#controller-table", "#disk-table", "#health-table", "#slot-table", "#trend-table"):
            rows_of(self.query_one(table_id)).clear(columns=True)
        self._fill_controllers()
        self._fill_disks()
        self._fill_health()
        self._fill_slots()
        self._fill_trend()
        # The panel holds a record built from the PREVIOUS diagnosis, so it is
        # redrawn like every other page: refreshing the banner and leaving one
        # view on the first scan's answer is what put two verdicts on one screen
        # before.
        self._refresh_detail()


__all__ = [
    "CONTROLLER_COLUMNS",
    "DISK_COLUMNS",
    "HEALTH_COLUMNS",
    "PAGE_LABELS",
    "SLOT_COLUMNS",
    "TREND_PAGE_COLUMNS",
    "LsdskApp",
]
