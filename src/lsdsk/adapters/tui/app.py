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
from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane

from ... import __init__conf__
from ...domain.diagnostics import count_by_severity, diagnose
from ...domain.enums import CliCommand, Severity
from ...domain.history import CounterKind, History
from ..config.tunables import DisplaySettings
from ..render import layout, report, tables, theme
from ..render.trend import render_trend
from .typed_table import raising_table_id, rows_of

if TYPE_CHECKING:
    from ...domain.models import Finding, Inventory, PcieLink

# Column sets per page, kept here so a page's shape is readable in one place.
_CONTROLLER_COLUMNS = ("", "address", "controller", "driver", "firmware", "running", "capable", "ports", "disks")
# Taken from the printed table rather than restated, because a page and the
# command of one name are one view. Restating it is how the page came to name a
# drive by model alone: its own tuple was written without `serial` and
# `firmware`, and nothing compared the two lists. The leading empty label is the
# severity marker, which the printed table draws as a fixed gutter instead.
DISK_COLUMNS = ("", *(column.title for column in tables.DISK_COLUMNS))
_HEALTH_COLUMNS = ("", "device", "temp", "worn", "hours", "written", "realloc", "pending", "uncorr", "crc", "media")
_SLOT_COLUMNS = ("port", "slot", "capable", "running", "occupant", "needs", "verdict")


def _pcie(link: PcieLink) -> str:
    """Render a PCIe link's running generation and width."""
    return theme.format_pcie_decimal(link.current_speed_gtps, link.current_width)


def _pcie_capable(link: PcieLink) -> str:
    """Render a PCIe link's best generation and width."""
    return theme.format_pcie_decimal(link.max_speed_gtps, link.max_width)


def _cell(text: str, style: str = "") -> Text:
    """Render one table cell.

    Cells are Rich text rather than plain strings so a table can carry the same
    colour the printed report does. A plain string reaches the terminal unstyled,
    which silently drops every severity signal the render layer computed.
    """
    return Text(text, style=style)


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
        Binding("r,f9", "rescan", "Rescan", priority=True, show=False),
        Binding("q,f10,escape", "quit", "Quit", priority=True),
    ]

    #: Page order, used by the number keys and by cycling with tab.
    #: Page order. Taken from the command enum so the two surfaces cannot drift:
    #: a page and its command are one view under one name.
    PAGES: ClassVar[tuple[str, ...]] = tuple(command.value for command in CliCommand)

    def __init__(
        self,
        inventory: Inventory,
        history: History | None = None,
        *,
        display: DisplaySettings | None = None,
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
        """
        super().__init__()
        self.inventory = inventory
        # NOT self.display: Textual's DOMNode already owns that name as the
        # show/hide property, and shadowing it breaks rendering.
        self.display_settings = display if display is not None else DisplaySettings()
        self.history: History = history if history is not None else History(hostname=inventory.hostname)
        self.findings: tuple[Finding, ...] = diagnose(inventory, history=history)
        #: The uncut WWN of each listed drive, by row key. The cell is clipped
        #: to the column width, so the whole of it has to be kept somewhere for
        #: the strip to show; reading it back off the cell would only return
        #: what was already cut.
        self._wwn_of: dict[str, str | None] = {}
        self.title = f"lsdsk {__init__conf__.version}"
        self.sub_title = inventory.hostname

    def compose(self) -> ComposeResult:
        """Lay out the header, the summary, the pages and the footer."""
        yield Header()
        yield Static(self.verdict_line(), id="verdict")
        with TabbedContent(initial=CliCommand.TOPOLOGY.value):
            with TabPane("Topology", id=CliCommand.TOPOLOGY.value), VerticalScroll():
                yield Static(
                    report.render_tree(
                        self.inventory, self.findings, expand_virtual=self.display_settings.expand_virtual
                    ),
                    id="tree",
                )
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
                yield Static(report.render_smart(self.inventory), id="smart-body")
            with TabPane("Findings", id=CliCommand.FINDINGS.value), VerticalScroll():
                yield Static(report.render_findings(self.findings), id="findings-body")
            with TabPane("Slots", id=CliCommand.SLOTS.value), Vertical():
                yield DataTable[str](id="slot-table", zebra_stripes=True, cursor_type="row")
                yield Static(report.form_factor_note(), id="slot-note")
            with TabPane("Trend", id=CliCommand.TREND.value), VerticalScroll():
                yield Static(render_trend(self.inventory, self.history), id="trend-body")
        yield Footer()

    def on_mount(self) -> None:
        """Fill every table once the widgets exist."""
        self._fill_controllers()
        self._fill_disks()
        self._fill_health()
        self._fill_slots()

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
        table.add_columns(*_CONTROLLER_COLUMNS)
        for controller in self.inventory.controllers:
            severity = report.worst_severity(self.findings, controller.address)
            marker_style = theme.style_for(severity)
            running = _pcie(controller.link)
            capable = _pcie_capable(controller.link)
            table.add_row(
                _cell(theme.marker_for(severity), marker_style),
                _cell(controller.address, theme.STYLE_IDENTIFIER),
                _cell(controller.name, marker_style),
                _cell(controller.driver or "-", "" if controller.driver else theme.STYLE_UNKNOWN),
                _cell(controller.firmware or "-", "" if controller.firmware else theme.STYLE_UNKNOWN),
                _cell(running, "" if running == capable else theme.STYLE_BELOW_CAPABILITY),
                _cell(capable, ""),
                _cell(
                    "-" if controller.port_count is None else f"{controller.ports_used or 0}/{controller.port_count}"
                ),
                _cell(str(len(self.inventory.disks_on(controller.address)))),
            )

    def _fill_slots(self) -> None:
        """Populate the mainboard slot page."""
        table = rows_of(self.query_one("#slot-table"))
        table.add_columns(*_SLOT_COLUMNS)
        for slot in self.inventory.slots:
            verdict, verdict_style = report.slot_verdict(slot)
            table.add_row(
                _cell(slot.address, theme.STYLE_IDENTIFIER),
                _cell(
                    "-" if slot.physical_slot_number is None else f"#{slot.physical_slot_number}",
                    theme.STYLE_UNKNOWN if slot.physical_slot_number is None else "",
                ),
                _cell(_pcie_capable(slot.link), ""),
                _cell(_pcie(slot.link) if slot.occupied else "-"),
                _cell(slot.occupant_description, "" if slot.occupied else theme.STYLE_UNKNOWN),
                _cell(
                    "-" if slot.occupant_link is None else _pcie_capable(slot.occupant_link),
                    theme.STYLE_UNKNOWN if slot.occupant_link is None else "",
                ),
                _cell(verdict, verdict_style),
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
        listed = (
            (*self.inventory.disks, *self.inventory.virtual_disks)
            if self.display_settings.expand_virtual
            else self.inventory.disks
        )
        for disk in listed:
            port = self.inventory.port_link_for(disk)
            cells = report.disk_row(disk, port)
            severity = report.worst_severity(self.findings, disk.path)
            table.add_row(
                _cell(theme.marker_for(severity), theme.style_for(severity)),
                *(_cell(*cells[key]) for key in ("device", "model")),
                _cell(layout.clip(disk.wwn or "-", width), "" if disk.wwn else theme.STYLE_UNKNOWN),
                _cell(disk.serial or "-", "" if disk.serial else theme.STYLE_UNKNOWN),
                _cell(disk.firmware or "-", "" if disk.firmware else theme.STYLE_UNKNOWN),
                *(_cell(*cells[key]) for key in ("size", "kind", "bus", "port", "disk", "link")),
                _cell(
                    disk.controller_address or "-",
                    theme.STYLE_IDENTIFIER if disk.controller_address else theme.STYLE_UNKNOWN,
                ),
                key=disk.node,
            )
            self._wwn_of[disk.node] = disk.wwn
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
        self.query_one("#wwn-full", Static).update(Text(value or "-", style="" if value else theme.STYLE_UNKNOWN))
        self.query_one("#wwn-strip", HorizontalScroll).scroll_to(x=0, animate=False)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Follow the disk page's cursor with the whole WWN of the row it is on.

        Args:
            event: The table and row the cursor moved to.

        Every page's table raises this, and at mount they all raise it in turn
        with the slot table last, so without the check the strip would settle on
        an answer the slot page gave to a question the disk page asked.
        """
        if raising_table_id(event) != "disk-table":
            return
        node = event.row_key.value
        self._show_wwn(self._wwn_of.get(node) if node is not None else None)

    def _fill_health(self) -> None:
        """Populate the health page."""
        table = rows_of(self.query_one("#health-table"))
        table.add_columns(*_HEALTH_COLUMNS)
        for disk in self.inventory.disks:
            health = disk.health
            # Same series and trend the health TABLE computes. Without it every
            # counter cell renders with trend=None, so a still-rising count
            # loses its "+" and a count proved quiet keeps its red, and the page
            # silently disagrees with `lsdsk health` on the same machine.
            series = tables.series_for(disk, self.history)
            severity = report.worst_severity(self.findings, disk.path)
            temperature = theme.format_temperature(
                None if health is None else health.temperature_c,
                None if health is None else health.temperature_warning_c,
                None if health is None else health.temperature_critical_c,
            )
            wear = theme.format_wear(None if health is None else health.percent_used)
            table.add_row(
                _cell(theme.marker_for(severity), theme.style_for(severity)),
                _cell(disk.path, "bold"),
                _cell(*temperature),
                _cell(*wear),
                _cell(tables.counter_text(None if health is None else health.power_on_hours)),
                _cell(theme.format_size(None if health is None else health.bytes_written)),
                _cell(
                    *tables.counter_cell(
                        None if health is None else health.reallocated_sectors,
                        tables.trend_of(series, CounterKind.REALLOCATED_SECTORS),
                    )
                ),
                _cell(
                    *tables.counter_cell(
                        None if health is None else health.pending_sectors,
                        tables.trend_of(series, CounterKind.PENDING_SECTORS),
                    )
                ),
                _cell(
                    *tables.counter_cell(
                        None if health is None else health.uncorrectable_sectors,
                        tables.trend_of(series, CounterKind.UNCORRECTABLE_SECTORS),
                    )
                ),
                _cell(
                    *tables.counter_cell(
                        None if health is None else health.crc_errors, tables.trend_of(series, CounterKind.CRC_ERRORS)
                    )
                ),
                _cell(
                    *tables.counter_cell(
                        None if health is None else health.media_errors,
                        tables.trend_of(series, CounterKind.MEDIA_ERRORS),
                    )
                ),
                key=disk.node,
            )

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
        tables = self.query(f"#{pane} DataTable")
        if tables:
            tables.first().focus()
            return
        # The long text pages have no table. Focusing their scroll container is
        # what makes up and down move them; without it the page ignores the
        # keyboard entirely, which is how findings and SMART behaved.
        scrolls = self.query(f"#{pane} VerticalScroll")
        if scrolls:
            scrolls.first().focus()

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
        if action in {"wwn_left", "wwn_right"}:
            return self.query_one(TabbedContent).active == CliCommand.DISKS.value
        return True

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
        """Move to the next page, wrapping at the end."""
        tabs = self.query_one(TabbedContent)
        current = tabs.active or self.PAGES[0]
        position = self.PAGES.index(current) if current in self.PAGES else 0
        tabs.active = self.PAGES[(position + 1) % len(self.PAGES)]

    def action_prev_page(self) -> None:
        """Move to the previous page, wrapping at the start."""
        tabs = self.query_one(TabbedContent)
        current = tabs.active or self.PAGES[0]
        position = self.PAGES.index(current) if current in self.PAGES else 0
        tabs.active = self.PAGES[(position - 1) % len(self.PAGES)]

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
        self.query_one("#findings-body", Static).update(report.render_findings(self.findings))
        self.query_one("#tree", Static).update(
            report.render_tree(self.inventory, self.findings, expand_virtual=self.display_settings.expand_virtual)
        )
        self.query_one("#smart-body", Static).update(report.render_smart(self.inventory))
        self.query_one("#trend-body", Static).update(render_trend(self.inventory, self.history))
        for table_id in ("#controller-table", "#disk-table", "#health-table", "#slot-table"):
            rows_of(self.query_one(table_id)).clear(columns=True)
        self._fill_controllers()
        self._fill_disks()
        self._fill_health()
        self._fill_slots()


__all__ = ["LsdskApp"]
