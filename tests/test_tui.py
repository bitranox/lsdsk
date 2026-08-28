"""Interactive view tests, including the resize behaviour.

Terminal size is the thing most easily broken and least often checked, so the
app is driven at several real sizes rather than looked at once at one size.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from textual.containers import HorizontalScroll
from textual.widgets import Static, TabbedContent

from lsdsk.adapters.config.tunables import DEFAULT_WWN_WIDTH, DisplaySettings
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import theme
from lsdsk.adapters.render.layout import ELLIPSIS, Column, fit, natural_widths, pad
from lsdsk.adapters.render.report import DISK_COLUMNS, render_tree
from lsdsk.adapters.render.tables import DISK_COLUMNS as PRINTED_DISK_COLUMNS
from lsdsk.adapters.render.tables import render_disks
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.app import DISK_COLUMNS as TUI_DISK_COLUMNS
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.enums import Align
from lsdsk.domain.models import Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

# Sizes worth proving: a classic terminal, a comfortable window, and a wide one.
TERMINAL_SIZES = ((80, 24), (120, 40), (200, 60))


def inventory() -> Inventory:
    """Build the inventory every test in this module uses."""
    return inventory_from(FIXTURE.name)


def inventory_from(name: str) -> Inventory:
    """Build an inventory from a named capture beside the module's own.

    Args:
        name: File name under ``tests/fixtures/hw``.

    Returns:
        The machine that capture holds.
    """
    with (FIXTURE.parent / name).open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return build_from(payload)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize(("width", "height"), TERMINAL_SIZES)
async def test_when_the_terminal_is_resized_the_app_still_renders(width: int, height: int) -> None:
    """Verify every page composes and paints at each terminal size."""
    machine = inventory()
    app = LsdskApp(machine)
    async with app.run_test(size=(width, height)) as pilot:
        for key, pane in (("1", "topology"), ("2", "controllers"), ("3", "disks"), ("4", "health"), ("6", "findings")):
            await pilot.press(key)
            await pilot.pause()
            # `app.screen is not None` cannot fail: Textual either returns a
            # Screen or raises. Assert the page is the one the key names, and
            # that the ones holding a table hold a row per object, so a pane
            # that renders empty at this size is caught.
            assert app.query_one(TabbedContent).active == pane, f"{key} did not reach {pane} at {width}x{height}"
            if pane in ("controllers", "disks", "health"):
                table_id = {"controllers": "#controller-table", "disks": "#disk-table", "health": "#health-table"}[pane]
                expected = len(machine.controllers) if pane == "controllers" else len(machine.disks)
                assert rows_of(app.query_one(table_id)).row_count == expected, f"{pane} empty at {width}x{height}"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_when_the_app_starts_the_tables_are_populated() -> None:
    """Verify each page holds one row per object, not an empty table."""
    machine = inventory()
    app = LsdskApp(machine)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        disks = rows_of(app.query_one("#disk-table"))
        controllers = rows_of(app.query_one("#controller-table"))

        assert disks.row_count == len(machine.disks)
        assert controllers.row_count == len(machine.controllers)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_smart_page_needs_no_selection_to_reach() -> None:
    """Verify the SMART page is reachable by its key alone.

    It previously said "select a disk on the Disks page", which nobody could do:
    `tab` is bound to switching pages, so focus never reached a table and no row
    could be selected. The old test called the handler directly, which proved
    the handler worked and never that a user could reach it.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("5")
        await pilot.pause()

        assert app.query_one(TabbedContent).active == "smart"
        assert app.query_one("#smart-body", Static) is not None, "the page carries its own content"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_pages_table_takes_the_keyboard_when_it_opens() -> None:
    """Verify a table can be scrolled, which needs focus to leave the tab bar.

    `tab` is this app's page-switching key, so Textual's own way of moving focus
    is unavailable and nothing would otherwise focus a table.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("3")
        await pilot.pause()

        focused = app.focused

        assert focused is not None
        assert focused.id == "disk-table", f"the disks table should hold the keyboard, not {focused.id!r}"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_when_findings_exist_the_banner_counts_them() -> None:
    """Verify the summary line reflects the diagnosis."""
    app = LsdskApp(inventory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        line = app.verdict_line()

        assert "PROBLEMS" in line
        assert "warning" in line


@pytest.mark.os_agnostic
@pytest.mark.parametrize("width", [60, 80, 100, 120, 200])
def test_when_the_terminal_narrows_columns_are_dropped_not_wrapped(width: int) -> None:
    """Verify the tree fits the width by dropping columns, never by wrapping.

    A wrapped disk row destroys the vertical alignment that makes the tree
    readable, so the layout gives up whole columns instead.
    """
    machine = inventory()
    rows = [
        {
            "device": disk.path,
            "model": disk.model,
            "size": "3.6T",
            "kind": "SSD",
            "bus": "SATA",
            "link": "6G",
            "port": "12G",
            "temp": "25C",
            "wear": "1%",
        }
        for disk in machine.disks
    ]
    widths = natural_widths(DISK_COLUMNS, rows)
    chosen = fit(DISK_COLUMNS, widths, width)

    assert chosen, "at least the device column must survive"
    rendered = 3 + sum(widths[column.key] + 2 for column in chosen)
    assert rendered <= max(width, 40), f"row of {rendered} columns overflows a {width}-wide terminal"

    keys = {column.key for column in chosen}
    assert "device" in keys, "the device is never droppable"
    if width >= 100:
        assert "link" in keys, "the link is the point of the tool at any usable width"


@pytest.mark.os_agnostic
def test_when_a_cell_is_too_long_it_is_truncated_not_wrapped() -> None:
    """Verify overlong text is cut with a marker, keeping the row one line."""
    assert pad("a-very-long-model-name", 10, Align.LEFT) == "a-very-lo>"
    assert len(pad("a-very-long-model-name", 10, Align.LEFT)) == 10
    assert pad("short", 10, Align.RIGHT) == "     short"


@pytest.mark.os_agnostic
def test_when_a_column_can_shrink_it_shrinks_before_others_are_dropped() -> None:
    """Verify a flexible column gives up space before a fixed one is lost."""
    columns = (
        Column("model", "model", priority=0, flexible=True, min_width=8),
        Column("temp", "temp", priority=2),
    )
    widths = {"model": 40, "temp": 4}
    chosen = fit(columns, widths, 30)

    assert {column.key for column in chosen} == {"model", "temp"}
    assert widths["model"] < 40


@pytest.mark.os_agnostic
def test_when_the_machine_is_empty_the_tree_says_so() -> None:
    """Verify an inventory with nothing in it renders a sentence, not a blank."""
    rendered = render_tree(Inventory("empty"), ())

    assert "No storage controllers or disks found." in str(rendered)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "expected"),
    # Derived from the page order rather than listed, so a page added without a
    # working number key fails here instead of being quietly untested.
    [(str(index + 1), page) for index, page in enumerate(LsdskApp.PAGES)],
)
async def test_number_keys_switch_pages(key: str, expected: str) -> None:
    """Verify the *top-style number keys reach every page.

    Pressed while a table has focus, which is where a user actually is, because
    a binding a focused widget swallows is a binding that does not exist.
    """
    from textual.widgets import TabbedContent

    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_show("disks")
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()

        assert app.query_one(TabbedContent).active == expected


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_tab_cycles_through_every_page_and_wraps() -> None:
    """Verify tab reaches each page in turn and returns to the first."""
    from textual.widgets import TabbedContent

    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        seen: list[str] = []
        for _ in range(len(app.PAGES) + 1):
            seen.append(app.query_one(TabbedContent).active)
            await pilot.press("tab")
            await pilot.pause()

        assert seen[: len(app.PAGES)] == list(app.PAGES)
        assert seen[-1] == app.PAGES[0], "cycling past the last page returns to the first"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_q_quits() -> None:
    """Verify the conventional quit key works without a menu."""
    app = LsdskApp(inventory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

    assert app.is_running is False


@pytest.mark.os_agnostic
def test_every_page_has_a_visible_key() -> None:
    """Verify each page is reachable by a key the footer advertises.

    A page nobody can find is a page that does not exist, so the binding list
    and the page list have to stay in step.
    """
    shown = {
        binding.action.split("'")[1]
        for binding in LsdskApp.BINDINGS
        if binding.action.startswith("show(") and binding.show
    }

    assert shown == set(LsdskApp.PAGES)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize("page_key", [str(index + 1) for index in range(len(LsdskApp.PAGES))])
async def test_left_and_right_move_between_pages_from_every_page(page_key: str) -> None:
    """Verify page navigation does not depend on what holds focus.

    Focusing a table so the arrows scroll it took left and right away from the
    tab bar, and the pages with no table lost them too. The result worked on
    exactly one page, so this drives every page rather than a sample.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press(page_key)
        await pilot.pause()
        tabs = app.query_one(TabbedContent)
        start = tabs.active

        await pilot.press("right")
        await pilot.pause()
        forward = tabs.active
        await pilot.press("left")
        await pilot.pause()

        assert forward != start, f"right did nothing on {start}"
        assert tabs.active == start, f"left did not come back to {start}"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_page_without_a_table_still_scrolls() -> None:
    """Verify the long text pages answer the arrow keys.

    SMART lists every attribute of every drive, so it is far taller than any
    terminal. Nothing focuses it unless the app does, because `tab` is bound to
    switching pages.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.press("5")
        await pilot.pause()

        body = app.query("#smart VerticalScroll").first()
        assert body.max_scroll_y > 0, "the fixture must make this page taller than the terminal"
        before = body.scroll_offset.y
        await pilot.press("down", "down", "down")
        await pilot.pause()

        assert body.scroll_offset.y > before, "the page ignored the arrow keys"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize(("page_key", "table_id"), [("3", "#disk-table"), ("4", "#health-table")])
async def test_a_flagged_row_carries_its_colour(page_key: str, table_id: str) -> None:
    """Verify severity reaches the screen as colour, not only as a marker.

    A plain string in a cell renders unstyled, so every colour the render layer
    computed is thrown away between it and the terminal. The tables looked right
    and read wrong: a failing counter was the same colour as a healthy one.
    """
    machine = inventory()
    app = LsdskApp(machine)
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.press(page_key)
        await pilot.pause()

        table = rows_of(app.query_one(table_id))
        styles = {str(cell.style) for row in table.rows for cell in table.get_row(row)}

        assert styles - {""}, "no cell carried any style, so the page is monochrome"
        severity_styles = {
            theme.STYLE_BELOW_CAPABILITY,
            theme.STYLE_FAILING,
            theme.STYLE_OPPORTUNITY,
            *theme.SEVERITY_STYLES.values(),
        }
        assert any(style in styles for style in severity_styles), (
            f"the fixture has findings, so some cell must carry a severity colour; got {sorted(styles)}"
        )


# --------------------------------------------------------------------------
# The interactive view has to say what the commands of the same name say
# --------------------------------------------------------------------------


def _history_for(machine: Inventory) -> Any:
    """A rising CRC series for the first trackable drive in the fixture."""
    from lsdsk.domain.history import DiskSeries, History, Sample, identity_of

    # A drive with a trackable identity AND the counter in question: the
    # first NVMe in this fixture reports no CRC count at all, so a series
    # attached to it renders "-" and the test would assert nothing.
    disk = next(d for d in machine.disks if identity_of(d) and d.health is not None and d.health.crc_errors is not None)
    return History(
        hostname=machine.hostname,
        series=(
            DiskSeries(
                identity=identity_of(disk) or "",
                model=disk.model,
                samples=(
                    Sample(power_on_hours=100, captured_at="2024-01-01T00:00:00Z", crc_errors=5),
                    Sample(power_on_hours=490, captured_at="2024-02-01T00:00:00Z", crc_errors=9000),
                ),
            ),
        ),
    )


@pytest.mark.os_agnostic
def test_the_tui_command_hands_the_app_the_recorded_history() -> None:
    """The app has always accepted history; the command never passed any.

    Structural rather than behavioural because launching the real TUI from a
    test would drive a terminal. The defect was pure wiring: every other view
    read the store first, and this one built ``LsdskApp(inventory)``, so the
    Trend page said "nothing recorded" on a machine whose history was on disk.
    """
    import ast
    import inspect

    from lsdsk.adapters.cli.commands import scan

    # cli_tui is a rich-click Command object; the function is its callback.
    callback = scan.cli_tui.callback
    assert callback is not None, "cli_tui has no callback, so this asserted nothing"
    source = inspect.getsource(callback)
    tree = ast.parse(source.lstrip())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = {node.func.id for node in calls if isinstance(node.func, ast.Name)}
    assert "read_history" in names, "cli_tui does not read the history store"
    app_calls = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "LsdskApp"]
    assert app_calls, "cli_tui no longer builds the app"
    assert len(app_calls[0].args) >= 2, "LsdskApp is built without history"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_health_page_marks_a_still_rising_counter() -> None:
    """The same mark `lsdsk health` puts on the same drive.

    ``_fill_health`` called ``counter_cell`` with one argument, so ``trend``
    always defaulted to None and the page could not show a rising "+" even when
    the app held history. Driven through the real app, then read off the table.
    """
    machine = inventory()
    app = LsdskApp(machine, _history_for(machine))
    async with app.run_test(size=(200, 60)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        table = rows_of(app.query_one("#health-table"))
        cells = [str(table.get_row_at(index)) for index in range(table.row_count)]
    assert any("+" in cell for cell in cells), "no counter carries the still-rising mark"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_every_bound_key_reaches_its_own_action() -> None:
    """`f5` was claimed by both SMART and rescan, so the rescan half was dead.

    Driven with real key presses rather than by calling the handlers, because
    calling a handler proves the handler works and never that a key reaches it.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("f5")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "smart"
        before = app.findings
        await pilot.press("f9")
        await pilot.pause()
        assert app.findings is not before, "f9 did not reach the rescan action"
        assert app.query_one(TabbedContent).active == "smart", "rescan should not move the page"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
class TestTheDiskPageIdentifiesADrive:
    """The page and `lsdsk disks` are one view, so they carry the same identity.

    A drive is identified by model, serial and firmware together: two disks of
    one model differ by serial, and a firmware revision is what a mixed-firmware
    finding sends the reader to check. The page named the model and neither of
    the other two, so it could not answer the question its own finding raises.
    """

    async def _cells(self, node: str) -> list[str]:
        """Press the key a reader presses, then read the row that appeared."""
        machine = inventory()
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            return [str(cell) for cell in rows_of(app.query_one("#disk-table")).get_row(node)]

    async def test_the_firmware_revision_is_under_the_firmware_column(self) -> None:
        """Under its own heading, not merely somewhere on the row.

        The labels are derived from the printed table but the cells are still
        added in their own order beside them, so a value landing one column off
        is what remains possible here.
        """
        disk = next(d for d in inventory().disks if d.firmware)
        cells = await self._cells(disk.node)
        assert cells[TUI_DISK_COLUMNS.index("firmware")] == disk.firmware

    async def test_the_serial_is_under_the_serial_column(self) -> None:
        disk = next(d for d in inventory().disks if d.serial)
        cells = await self._cells(disk.node)
        assert cells[TUI_DISK_COLUMNS.index("serial")] == disk.serial


@pytest.mark.os_agnostic
def test_the_disk_page_carries_the_same_columns_as_the_printed_table() -> None:
    """`lsdsk disks` and page 3 are one view under one name, so prove it.

    The page takes the printed table's columns rather than restating them, so
    this cannot drift by a column being added to one list. What it still holds
    is the derivation itself: replacing it with a literal tuple, which is how
    `serial` and `firmware` went missing at 1.0.0, fails here.
    """
    printed = tuple(column.title for column in PRINTED_DISK_COLUMNS)
    # The leading empty label is the severity marker's column, which the printed
    # table renders as a fixed gutter rather than as one of its own columns.
    assert tuple(name for name in TUI_DISK_COLUMNS if name) == printed


@pytest.mark.os_agnostic
@pytest.mark.asyncio
class TestTheDiskPageKeepsALongIdentifierReachable:
    """A WWN that will not fit is cut, marked, and still readable in full.

    An NVMe WWN runs to a hundred characters where the SATA ones beside it run
    to twenty, so one drive was setting the width of the column for every row
    and pushing the nine columns after it off the page. The column is capped
    now, which means the page has to answer where the rest of the value went:
    the strip under the table carries the whole of the drive under the cursor,
    and grows a scroll control exactly when there is something past its edge.
    """

    @staticmethod
    def _wwn_cell(app: LsdskApp, node: str) -> str:
        """The wwn cell of one row, read under its own heading."""
        row = rows_of(app.query_one("#disk-table")).get_row(node)
        return str(row[TUI_DISK_COLUMNS.index("wwn")])

    @staticmethod
    def _strip(app: LsdskApp) -> HorizontalScroll:
        return app.query_one("#wwn-strip", HorizontalScroll)

    @staticmethod
    def _shown(app: LsdskApp) -> str:
        return str(app.query_one("#wwn-full", Static).content)

    async def test_a_long_wwn_is_cut_to_the_configured_width_and_marked(self) -> None:
        """Cut, and saying so. A value that reads as whole when it is not sends
        somebody looking for a drive by an identifier missing its tail."""
        machine = inventory()
        disk = next(d for d in machine.disks if d.wwn and len(d.wwn) > DEFAULT_WWN_WIDTH)
        assert disk.wwn is not None
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            cell = self._wwn_cell(app, disk.node)
        assert len(cell) == DEFAULT_WWN_WIDTH
        assert cell.endswith(ELLIPSIS)
        assert cell[:-1] == disk.wwn[: DEFAULT_WWN_WIDTH - 1]

    async def test_a_wwn_that_fits_is_left_exactly_as_the_drive_reports_it(self) -> None:
        """The cap is a ceiling, not a haircut: nothing shortens a value that fits."""
        machine = inventory()
        disk = next(d for d in machine.disks if d.wwn and len(d.wwn) <= DEFAULT_WWN_WIDTH)
        assert disk.wwn is not None
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            cell = self._wwn_cell(app, disk.node)
        assert cell == disk.wwn
        assert ELLIPSIS not in cell

    async def test_the_strip_carries_the_whole_identifier_of_the_row_the_cursor_is_on(self) -> None:
        """Uncut, or the cap would have hidden the value with no way back to it.

        Also the guard on which table the handler listens to: every page's table
        raises the same event and at mount they all raise it in turn, the slot
        table last, so without that check the strip settles on a dash.
        """
        machine = inventory()
        disk = next(d for d in machine.disks if d.wwn and len(d.wwn) > DEFAULT_WWN_WIDTH)
        assert disk.wwn is not None
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert self._shown(app) == disk.wwn

    async def test_moving_the_cursor_winds_the_strip_back_to_the_start(self) -> None:
        """A value scrolled into must not leave the next one showing its middle.

        Driven at a width where EVERY row overflows, which is the only way this
        can fail: moving to a value that fits makes Textual clamp the offset by
        itself, so a walk down the shipped widths would pass with the rewind
        deleted and prove nothing.
        """
        machine = inventory()
        listed = list(machine.disks)
        narrow = DisplaySettings(wwn_width=12)
        assert all(d.wwn and len(d.wwn) > narrow.wwn_width for d in listed[:2]), "both rows must overflow"
        app = LsdskApp(machine, display=narrow)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            await pilot.press(".")
            await pilot.pause()
            assert self._strip(app).scroll_x > 0, "the first row must be scrolled before the move means anything"
            await pilot.press("down")
            await pilot.pause()
            assert self._shown(app) == listed[1].wwn
            assert self._strip(app).scroll_x == 0

    async def test_the_scroll_control_appears_only_when_the_identifier_was_cut(self) -> None:
        """One machine carries both cases, so one walk proves both directions."""
        machine = inventory()
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert self._strip(app).show_horizontal_scrollbar, "a cut value must offer its remainder"
            await pilot.press("down")
            await pilot.pause()
            assert not self._strip(app).show_horizontal_scrollbar, "a whole value must offer nothing"

    async def test_a_board_of_short_identifiers_never_shows_the_scroll_control(self) -> None:
        """The negative control: five NVMe drives whose WWNs all fit.

        Without a machine on which the answer must be no, a test that only ever
        looks at the long-WWN capture cannot tell "appears when needed" from
        "always appears".
        """
        machine = inventory_from("linux-nvme-board.json")
        assert machine.disks, "the control needs drives to be a control"
        assert all(d.wwn and len(d.wwn) <= DEFAULT_WWN_WIDTH for d in machine.disks)
        whole = {d.wwn for d in machine.disks}
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            for _ in machine.disks:
                assert not self._strip(app).show_horizontal_scrollbar
                assert self._shown(app) in whole, "the strip must name the drive the cursor is on"
                await pilot.press("down")
                await pilot.pause()

    async def test_a_drive_with_no_wwn_reads_the_same_dash_the_cell_does(self) -> None:
        """The strip answers for every drive, including one with nothing to say."""
        machine = inventory_from("windows-ahci.json")
        disk = next(d for d in machine.disks if d.wwn is None)
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert self._shown(app) == "-"
            assert self._wwn_cell(app, disk.node) == "-"
            assert not self._strip(app).show_horizontal_scrollbar

    async def test_the_scroll_keys_reach_the_strip(self) -> None:
        """Press the keys a reader presses.

        Calling the action would prove the action works and never that a key
        reaches it, which is how this app once shipped a page whose only route
        in was bound away.  `left` and `right` are taken by page switching with
        priority, so the strip's own scroll keys can never fire.
        """
        machine = inventory()
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert self._strip(app).scroll_x == 0
            await pilot.press(".")
            await pilot.pause()
            moved = self._strip(app).scroll_x
            assert moved > 0, "the forward key must move the strip"
            await pilot.press(",")
            await pilot.pause()
            assert self._strip(app).scroll_x < moved, "the back key must move it back"

    async def test_a_configured_width_reaches_both_the_cell_and_the_strip(self) -> None:
        """One key, both halves. A width honoured by one of them would put the
        control on values the column did not cut, or leave cut ones without."""
        machine = inventory()
        disk = next(d for d in machine.disks if d.wwn and len(d.wwn) > DEFAULT_WWN_WIDTH)
        app = LsdskApp(machine, display=DisplaySettings(wwn_width=12))
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert len(self._wwn_cell(app, disk.node)) == 12
            assert self._strip(app).size.width == 12

    async def test_both_views_cut_the_identifier_at_the_same_place(self) -> None:
        """`lsdsk disks` and page 3 are one view under one name.

        They mark the cut with their own renderer's character, so what has to
        agree is where the cut falls and that neither claims the whole value.
        """
        machine = inventory()
        disk = next(d for d in machine.disks if d.wwn and len(d.wwn) > DEFAULT_WWN_WIDTH)
        assert disk.wwn is not None
        buffer = io.StringIO()
        Console(file=buffer, width=400, no_color=True).print(render_disks(machine, (), width=400))
        printed = buffer.getvalue()
        app = LsdskApp(machine)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            cell = self._wwn_cell(app, disk.node)
        kept = disk.wwn[: DEFAULT_WWN_WIDTH - 1]
        assert kept in printed, "the printed table must keep what the page keeps"
        assert cell.startswith(kept)
        assert disk.wwn not in printed, "no width may let the printed table run to a hundred columns"
        assert disk.wwn not in cell

    async def test_the_scroll_keys_are_offered_only_on_the_page_that_has_a_strip(self) -> None:
        """Read from the collection the footer draws, per page.

        Textual reads ``False`` from ``check_action`` as hidden and ``None`` as
        shown-but-greyed, which is the opposite way round from what the names
        suggest; written the other way round this gate offered the keys on all
        eight pages while looking correct.
        """
        app = LsdskApp(inventory())
        async with app.run_test(size=(120, 40)) as pilot:
            for key, offered in (("1", False), ("3", True), ("5", False)):
                await pilot.press(key)
                await pilot.pause()
                actions = {active.binding.action for active in app.screen.active_bindings.values()}
                assert ("wwn_right" in actions) is offered, f"page {key} should offer the keys: {offered}"
