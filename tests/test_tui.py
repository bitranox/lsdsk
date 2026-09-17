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
from rich.text import Text
from textual.containers import HorizontalScroll, VerticalScroll
from textual.widgets import DataTable, OptionList, Static, TabbedContent

from lsdsk.adapters.config.tunables import DEFAULT_WWN_WIDTH, DisplaySettings
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import theme
from lsdsk.adapters.render.layout import ELLIPSIS, Column, clip, fit, natural_widths, pad
from lsdsk.adapters.render.report import DISK_COLUMNS, render_tree
from lsdsk.adapters.render.tables import DISK_COLUMNS as PRINTED_DISK_COLUMNS
from lsdsk.adapters.render.tables import render_disks
from lsdsk.adapters.render.tree import KEY_HINT, FabricView, render_fabric
from lsdsk.adapters.render.trend import TREND_COLUMNS
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui import palette as tui_palette
from lsdsk.adapters.tui.app import DISK_COLUMNS as TUI_DISK_COLUMNS
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import Align
from lsdsk.domain.history import DiskSeries, History, Sample, identity_of
from lsdsk.domain.models import Inventory, PciNode

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


def topology_lines(app: LsdskApp) -> list[str]:
    """What the topology page is actually showing, whichever widget holds it.

    The page carries a list of selectable fabric lines and, beside it for a
    capture with no PCI reading at all, the old section in a Static; exactly one
    of the two is displayed. Reading the hidden one gives an empty list, and an
    empty list is a prefix of everything - which is how the width test below
    went on passing while the page it measured showed nothing at all.

    Args:
        app: The running app.

    Returns:
        One rstripped line per line the page draws, never empty.
    """
    from textual.widgets import OptionList

    options = app.query_one("#tree-lines", OptionList)
    if options.display:
        prompts = [options.get_option_at_index(index).prompt for index in range(options.option_count)]
        lines = [prompt.plain.rstrip() if isinstance(prompt, Text) else str(prompt).rstrip() for prompt in prompts]
    else:
        page = app.query_one("#tree", Static)
        lines = [page.render_line(row).text.rstrip() for row in range(page.size.height)]
    assert lines, "the topology page is showing nothing, so anything asserted about it is vacuous"
    return lines


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
async def test_the_density_key_cycles_the_fabric_on_the_topology_page() -> None:
    """Verify pressing `d` redraws the topology at the next density.

    Driven through the key, not the handler, with the rendered ADDRESSES as
    the observable: on this capture the three densities hold different counts
    of device lines (members and neighbours included as the density narrows),
    so a cycle that changed the setting without redrawing, or redrew at the
    same density, would leave those sets unequal in the direction the test
    names rather than passing vacuously.
    """
    import re

    from lsdsk.domain.enums import TreeDensity

    device_address = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")

    def drawn_addresses() -> set[str]:
        return {match.group(0) for line in topology_lines(app) if (match := device_address.search(line))}

    members = list(TreeDensity)
    app = LsdskApp(inventory())
    async with app.run_test(size=(160, 45)) as pilot:
        # Where the cycle STARTS is the shipped default, which is the least
        # detail; the key walks the enum from wherever that is, so the test
        # follows the same order rather than restating one page of it.
        start = members.index(app.display_settings.tree_density)
        seen: dict[TreeDensity, set[str]] = {app.display_settings.tree_density: drawn_addresses()}
        for step in range(1, len(members) + 1):
            await pilot.press("d")
            await pilot.pause()
            expected = members[(start + step) % len(members)]
            assert app.display_settings.tree_density is expected, f"press {step} did not reach {expected.value}"
            seen[expected] = drawn_addresses()

    assert seen[TreeDensity.FULL] > seen[TreeDensity.STORAGE_ONLY], "full draws the whole fabric back"
    assert seen[TreeDensity.STORAGE_AND_SIBLINGS] == seen[TreeDensity.STORAGE_ONLY], (
        "no neighbour of storage shares a BRIDGE here, so the reduced pair agree"
    )


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_every_press_of_the_density_key_adds_detail_until_it_wraps() -> None:
    """The key is what a reader meets, so the climb is asserted at the key.

    The test above takes its expected sequence from ``list(TreeDensity)``, the
    same list production walks, so it stays green whatever that order is: it
    proves the press redraws, never that the redraw shows MORE. Here the
    observable is how many devices each press puts on screen, and the rule is
    that it never falls until the ring wraps at the end.

    Read from the default rather than from a chosen member, because where the
    default sits in the ring is half of what was wrong: the first press used
    to land on the most detailed of the three and the second to fall back.
    """
    import re

    from lsdsk.domain.enums import TreeDensity

    device_address = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")

    def devices_drawn() -> int:
        return sum(1 for line in topology_lines(app) if device_address.search(line))

    app = LsdskApp(inventory())
    async with app.run_test(size=(160, 45)) as pilot:
        counts = [devices_drawn()]
        # One short of a full ring: the last press is the wrap back to the
        # least, which is the one step that is SUPPOSED to fall.
        for _step in range(len(TreeDensity) - 1):
            await pilot.press("d")
            await pilot.pause()
            counts.append(devices_drawn())

    assert counts == sorted(counts), f"a press took detail away: {counts}"
    assert counts[-1] > counts[0], f"the ring never reached more detail than it started with: {counts}"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_density_key_is_gated_to_the_topology_page() -> None:
    """Verify `d` answers on the page whose view it changes, and only there.

    check_action hiding an action is what keeps its key off the footer AND
    out of dispatch; a key advertised on all eight pages that answers on one
    is a key a reader stops believing.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        assert app.check_action("tree_density", ()) is True

        app.action_show("disks")
        await pilot.pause()
        assert app.check_action("tree_density", ()) is False
        before = app.display_settings.tree_density
        await pilot.press("d")
        await pilot.pause()

        assert app.display_settings.tree_density is before, "`d` was dispatched on a page it is hidden from"


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
        # Named as the render layer's roles and then put through this view's
        # palette, rather than written out in the palette's own values: the
        # claim is that severity reaches the screen, not what colour it is, and
        # a literal here would have to be edited every time the palette moves.
        severity_styles = {
            tui_palette.restyle(style)
            for style in (
                theme.STYLE_BELOW_CAPABILITY,
                theme.STYLE_FAILING,
                theme.STYLE_OPPORTUNITY,
                *theme.SEVERITY_STYLES.values(),
            )
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

    async def test_the_next_identifier_is_laid_out_already_wound_back(self) -> None:
        """No frame shows the next identifier still scrolled.

        The strip is watched at the moment its content width changes, which is
        the layout that paints the new identifier. A rewind queued until after a
        refresh has not run at that moment, so the offset read there is the one
        the previous row was left at. The rewind test beside this one only sees
        that when it reads the offset between that paint and the queued rewind,
        which a slow runner does now and then; this one sees it every time.
        """
        machine = inventory()
        listed = list(machine.disks)
        narrow = DisplaySettings(wwn_width=12)
        assert all(d.wwn and len(d.wwn) > narrow.wwn_width for d in listed[:2]), "both rows must overflow"
        app = LsdskApp(machine, display=narrow)
        offsets: list[float] = []
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            await pilot.press(".")
            await pilot.pause()
            strip = self._strip(app)
            assert strip.scroll_x > 0, "the first row must be scrolled before the move means anything"

            def record(_size: object) -> None:
                offsets.append(strip.scroll_x)

            app.watch(strip, "virtual_size", record, init=False)
            await pilot.press("down")
            await pilot.pause()
            assert self._shown(app) == listed[1].wwn
        assert offsets, "the move must lay the strip out again, or nothing was watched"
        assert offsets == [0] * len(offsets)

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
        cut = clip(disk.wwn, DEFAULT_WWN_WIDTH)
        assert cell == cut, "the page must cut where the shared rule cuts"
        assert cut in printed, "and the printed table must produce the identical string, marker included"
        assert "\u2026" not in printed, "including the mark: one renderer's own ellipsis is not the other's"
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


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize("width", [100, 140, 200])
async def test_the_topology_page_lays_the_fabric_out_at_the_width_it_was_given(width: int) -> None:
    """The page had one width built into it, whatever the terminal offered.

    The page asked for the section with no width at all, so it was laid out for
    the piped default of 120 columns: names clipped with room to spare in a
    200-column terminal, and columns fitted for a width the window did not have
    in a 100-column one. The printed command of the same name passes the
    console's width, so one view read two ways.

    Asserted against that command's own output at the page's width rather than
    against a line length, because a length depends on the longest name this
    capture happens to carry and would pass on a machine with short ones.
    """
    machine = inventory()
    findings = diagnose(machine)
    app = LsdskApp(machine)
    async with app.run_test(size=(width, 45)) as pilot:
        await pilot.press("1")
        await pilot.pause()
        # The region the options are laid out in, not content_size: the two
        # differ by the scrollbar gutter, and the page uses the former.
        page_width = app.query_one("#tree-lines", OptionList).scrollable_content_region.width
        painted = topology_lines(app)

    buffer = io.StringIO()
    Console(file=buffer, width=page_width, no_color=True).print(
        render_fabric(machine, findings, page_width, FabricView(how_to_change=KEY_HINT))
    )
    printed = [line.rstrip() for line in buffer.getvalue().splitlines()]

    # Compared whole, not prefix against prefix: an empty painted list is a
    # prefix of every printed one, which is exactly how this assertion passed
    # while the page it measured had been replaced by a hidden widget.
    assert painted == printed, f"the page is not laid out at its own {page_width} columns in a {width}-column terminal"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_topology_page_names_the_key_that_changes_the_detail_level() -> None:
    """The page opens on the least detail, so it has to say how to get more.

    Named as the KEY a reader of this view presses, not as the command-line
    option the printed view names: one sentence, written once, with each view
    supplying what its own reader does.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("1")
        await pilot.pause()
        painted = "\n".join(topology_lines(app))

    assert 'press "d" to change the detail level' in painted, painted[:300]
    assert "--tree-density" not in painted, "the page names the key, not the printed view's option"


def _panel_text(app: LsdskApp, width: int = 118) -> str:
    """What the detail panel currently draws, as text.

    Read off the widget's own renderable rather than off the screen, because the
    panel is scrollable: the visible rows are a window onto the record, and a
    test asserting on that window would pass or fail on how tall the terminal is.
    """
    buffer = io.StringIO()
    Console(width=width, file=buffer, no_color=True).print(app.query_one("#detail-body", Static).content)
    return buffer.getvalue()


class TestTheDetailPanelAnswersForTheRowUnderTheCursor:
    """The box under the tables, and the three ways it could quietly lie."""

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_moving_the_cursor_moves_the_panel_to_the_row_it_landed_on(self) -> None:
        """Driven by the key a reader presses, never by calling the handler.

        Calling the handler proves the handler works; it cannot prove a reader
        can reach it, which is how a feature shipped behind a binding that had
        been bound away.
        """
        machine = inventory()
        app = LsdskApp(machine)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            first = _panel_text(app)
            await pilot.press("down")
            await pilot.pause()
            second = _panel_text(app)

        assert machine.disks[0].path in first.splitlines()[0]
        assert machine.disks[1].path in second.splitlines()[0]
        assert first != second, "the cursor moved and the panel did not"

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_one_drive_reads_the_same_on_every_page_that_can_select_it(self) -> None:
        """The panel is the SUBJECT's record, so only the order may differ.

        Two page-shaped records of one drive would drift exactly as two column
        lists already did, when the disk page named a drive by model alone for a
        whole minor series because its own tuple omitted serial and firmware.
        """
        app = LsdskApp(inventory())
        seen: dict[str, list[str]] = {}
        async with app.run_test(size=(140, 45)) as pilot:
            for key, page in (("3", "disks"), ("4", "health")):
                await pilot.press(key)
                await pilot.pause()
                seen[page] = sorted(line.strip() for line in _panel_text(app).splitlines() if line.strip())

        assert seen["disks"] == seen["health"], "one drive, two answers"
        # And the ORDER did change, or the test above would hold for a panel
        # that ignores the page entirely and proves nothing about ordering.
        app = LsdskApp(inventory())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            disks_first = _panel_text(app).splitlines()[1]
            await pilot.press("4")
            await pilot.pause()
            health_first = _panel_text(app).splitlines()[1]
        assert disks_first != health_first, "the page did not choose what is answered first"
        assert health_first.startswith("health")
        assert disks_first.startswith("identity")

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_the_panel_answers_for_the_page_in_front_not_the_table_that_spoke_last(self) -> None:
        """Every table on screen raises a row event, the slot table last at mount.

        The WWN strip already carries this trap in its own docstring: without a
        check on which table raised, the panel would settle on the answer the
        slot page gave to a question the disk page asked.
        """
        machine = inventory()
        app = LsdskApp(machine)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            opened = _panel_text(app)

        assert machine.disks[0].path in opened.splitlines()[0]
        assert machine.slots[0].address not in opened.splitlines()[0]

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_switching_page_moves_no_cursor_so_the_panel_is_asked_again(self) -> None:
        """A page switch raises no row event, and the panel must not lag behind."""
        machine = inventory()
        app = LsdskApp(machine)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            after = _panel_text(app)

        assert machine.controllers[0].address in after.splitlines()[0], after.splitlines()[0]

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    @pytest.mark.parametrize("share", [25, 50])
    async def test_the_panel_never_takes_more_of_the_window_than_it_was_given(self, share: int) -> None:
        """A ceiling, not a height, and the configured one rather than a literal."""
        app = LsdskApp(inventory(), display=DisplaySettings(detail_height_percent=share))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            panel = app.query_one("#detail")
            height = panel.size.height
            screen = app.screen.size.height

        assert height <= screen * share // 100 + 1, f"{height} rows of {screen} for a {share}% ceiling"
        assert height > 0, "the panel took no room at all"

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_a_bigger_share_gives_the_panel_more_room(self) -> None:
        """The control: a ceiling nothing reads would pass the test above."""
        heights: dict[int, int] = {}
        for share in (20, 60):
            app = LsdskApp(inventory(), display=DisplaySettings(detail_height_percent=share))
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.press("4")
                await pilot.pause()
                heights[share] = app.query_one("#detail").size.height

        assert heights[60] > heights[20], f"the key moved nothing: {heights}"

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_the_detail_key_hides_the_panel_and_brings_it_back(self) -> None:
        """Pressed, not called: the binding is what a reader has."""
        app = LsdskApp(inventory())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            shown = app.query_one("#detail").display
            await pilot.press("i")
            await pilot.pause()
            hidden = app.query_one("#detail").display
            await pilot.press("i")
            await pilot.pause()
            back = app.query_one("#detail").display

        assert shown and not hidden and back

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_the_scroll_keys_are_offered_only_where_the_record_did_not_fit(self) -> None:
        """The rule the wwn strip's control already follows, at the panel."""
        offered: dict[str, bool] = {}
        for label, share in (("tall", 60), ("short", 10)):
            app = LsdskApp(inventory(), display=DisplaySettings(detail_height_percent=share))
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.press("4")
                await pilot.pause()
                offered[label] = "detail_down" in {
                    active.binding.action for active in app.screen.active_bindings.values()
                }

        assert offered["short"], "a record too tall for the panel offers no way to reach the rest"
        assert not offered["tall"], "the keys are offered where the whole record already fits"


def _is_device_row(line: object) -> bool:
    """Whether a fabric line is one the section promises to keep to one line.

    The device rows and the drive rows. NOT the board line, which carries a
    subject and is prose, nor the density note or a column header, which carry
    none.
    """
    from lsdsk.domain.models import Disk, PciNode

    subject = getattr(line, "subject", None)
    return isinstance(subject, (PciNode, Disk))


class TestTheTopologyPageCanBeMovedThrough:
    """The fabric as a list of selectable lines, and the traps that come with it."""

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    @pytest.mark.parametrize("width", [50, 60, 80, 100, 140, 200])
    async def test_a_device_or_a_drive_never_outgrows_the_width_it_was_laid_out_at(self, width: int) -> None:
        """The section's own law, checked where the list can actually hold it.

        An option wider than its box WRAPS, and measured on textual 8.2.8
        neither a Rich ``no_wrap`` nor a CSS ``text-wrap: nowrap`` prevents it -
        only laying the section out at the width the list gives its options
        does, which is ``scrollable_content_region`` and not ``content_size``:
        the two differ by the scrollbar's two columns.

        Asserted per ROW rather than by counting rendered rows against options,
        because the density note and the board line are prose and wrap here
        exactly as they wrap in the printed view - an aggregate count would have
        to allow for that and would then allow a wrapped device row too.
        """
        app = LsdskApp(inventory())
        async with app.run_test(size=(width, 45)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            options = app.query_one("#tree-lines", OptionList)
            laid_out = options.scrollable_content_region.width
            lines = app.tree_lines
            too_wide = [line.text.plain for line in lines if _is_device_row(line) and len(line.text.plain) > laid_out]

        assert lines, "the page listed nothing"
        assert any(_is_device_row(line) for line in lines), "no device row to check at this width"
        assert not too_wide, f"at {laid_out} columns these wrapped: {too_wide[:2]}"

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_below_the_sections_own_floor_a_row_does_outgrow_the_width(self) -> None:
        """The control, and the floor written down rather than discovered again.

        Measured on every committed capture: a device or drive row fits from 48
        columns up and overruns below. The test above would pass vacuously if
        the rows fitted at every width imaginable, so this pins the other side.
        """
        from lsdsk.adapters.render.tree import FabricView, fabric_lines

        machine = inventory()
        findings = diagnose(machine)
        fits = [
            width
            for width in range(20, 61)
            if all(
                len(line.text.plain) <= width
                for line in fabric_lines(machine, findings, width, FabricView())
                if _is_device_row(line)
            )
        ]
        assert min(fits) == 48, f"the section's floor moved to {min(fits)}"

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_the_cursor_stops_only_on_lines_that_are_about_something(self) -> None:
        """Walk the whole page and require every stop to name a device.

        The note, the legend and both repeated column headers are lines about
        the SECTION; a cursor that lands on one has nothing to put in the panel.
        """
        app = LsdskApp(inventory())
        visited: list[int] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            options = app.query_one("#tree-lines", OptionList)
            for _step in range(options.option_count + 2):
                if options.highlighted is not None:
                    visited.append(options.highlighted)
                await pilot.press("down")
                await pilot.pause()
            lines = app.tree_lines

        assert visited, "the cursor never landed anywhere"
        decorative = sorted({index for index in visited if lines[index].subject is None})
        assert not decorative, f"the cursor stopped on lines about nothing: {decorative}"
        # It must also have reached MORE than the line it opened on, or a cursor
        # that cannot move at all would satisfy the assertion above.
        assert len(set(visited)) > 1, "the cursor never moved"

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_the_panel_follows_the_topology_cursor_onto_a_device_and_onto_a_drive(self) -> None:
        """A fabric line is about a device or a drive, and both must answer."""
        machine = inventory()
        app = LsdskApp(machine)
        seen: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            for _step in range(8):
                seen.append(_panel_text(app).splitlines()[0])
                await pilot.press("down")
                await pilot.pause()

        assert any(machine.hostname in line for line in seen), "the board line said nothing about the machine"
        assert any(disk.path in line for line in seen for disk in machine.disks), "no drive was reached"
        assert any(one.address in line for line in seen for one in machine.controllers), "no controller was reached"
        assert "Nothing selected." not in seen

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_a_storage_controller_answers_with_its_controller_record_not_its_bare_device_one(self) -> None:
        """One address, two models, and the reader wants the one with the ports.

        A controller is a ``PciNode`` on the fabric and a ``Controller`` in the
        inventory; only the second carries the uplink, the port count and what
        the attached drives demand.
        """
        machine = inventory()
        app = LsdskApp(machine)
        address = machine.controllers[0].address
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            options = app.query_one("#tree-lines", OptionList)
            index = next(
                position
                for position, line in enumerate(app.tree_lines)
                if isinstance(line.subject, PciNode) and line.subject.address == address
            )
            options.highlighted = index
            await pilot.pause()
            panel = _panel_text(app)

        assert "in use" in panel and "uplink carries" in panel, panel[:400]

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_the_density_key_keeps_the_cursor_where_the_reader_left_it(self) -> None:
        """A redraw that throws the cursor back to the top loses the reader's place."""
        app = LsdskApp(inventory())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            for _step in range(3):
                await pilot.press("down")
                await pilot.pause()
            before = app.query_one("#tree-lines", OptionList).highlighted
            await pilot.press("d")
            await pilot.pause()
            after = app.query_one("#tree-lines", OptionList).highlighted

        assert before is not None and before > 0
        assert after == before, f"the cursor moved from {before} to {after} on a density change"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_capture_with_no_pci_reading_still_shows_its_drives_on_the_topology_page() -> None:
    """The branch no committed capture reaches, so it is built here.

    A software-only environment can publish drives with no `pci` section at
    all. The page's list of fabric lines has nothing to list then, and the old
    disk-and-controller section takes over in the Static beside it. Without this
    the machine's storage would be hidden behind a fabric that does not exist -
    and nothing else in the suite exercises the swap, because every committed
    capture carries a fabric.
    """
    machine = inventory()
    without_pci = Inventory(
        hostname=machine.hostname,
        controllers=machine.controllers,
        disks=machine.disks,
        slots=machine.slots,
        pci_tree=(),
        privileged=machine.privileged,
        board=machine.board,
    )
    app = LsdskApp(without_pci)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("1")
        await pilot.pause()
        # Read INSIDE the run: a widget's display is reset on the way out, so
        # the same two reads after the block answer about a torn-down screen.
        listed_shown = app.query_one("#tree-lines", OptionList).display
        fallback_shown = app.query_one("#tree-fallback", VerticalScroll).display
        shown = topology_lines(app)

    assert not listed_shown, "the list of fabric lines is showing for a capture that has no fabric"
    assert fallback_shown, "the section that replaces it is hidden"
    assert any(disk.path in line for line in shown for disk in without_pci.disks), "the drives are not shown at all"


def _history_with_a_rising_counter(machine: Inventory) -> History:
    """A recorded past, which no committed capture carries on its own.

    The trend view has nothing to draw without one, so a test that wants rows
    has to supply the readings. They go through the real ``Sample`` and
    ``DiskSeries`` the recorder writes, not a double.
    """
    disk = next(one for one in machine.disks if identity_of(one))
    return History(
        hostname=machine.hostname,
        series=(
            DiskSeries(
                identity=identity_of(disk) or "",
                model=disk.model,
                samples=(
                    Sample(power_on_hours=1000, captured_at="2024-01-01T00:00:00Z", crc_errors=10),
                    Sample(power_on_hours=2000, captured_at="2024-02-01T00:00:00Z", crc_errors=900),
                ),
            ),
        ),
    )


class TestTheTrendPageIsATable:
    """It was a table rendered as text; being one is what makes a row selectable."""

    @pytest.mark.os_agnostic
    def test_it_carries_the_same_columns_as_the_printed_view(self) -> None:
        """Derived from the printed view's own columns, never a second list.

        A page that copies a list has no guard at all whatever a name test
        says: that is exactly how the disk page came to identify a drive by
        model alone, its own tuple written without serial and firmware.
        """
        from lsdsk.adapters.render.trend import TREND_COLUMNS as PRINTED
        from lsdsk.adapters.tui.app import TREND_PAGE_COLUMNS

        assert tuple(column.title for column in PRINTED) == TREND_PAGE_COLUMNS

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_a_row_is_selectable_and_the_panel_answers_for_its_drive(self) -> None:
        """A trend row is one COUNTER of one drive, and the drive is the subject."""
        machine = inventory()
        app = LsdskApp(machine, _history_with_a_rising_counter(machine))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("8")
            await pilot.pause()
            table = rows_of(app.query_one("#trend-table"))
            rows = table.row_count
            panel = _panel_text(app)

        assert rows > 0, "the page listed no counter although a rising one was recorded"
        assert any(disk.path in panel.splitlines()[0] for disk in machine.disks), panel.splitlines()[0]
        # The trend page opens on the counters, which is its own question.
        assert panel.splitlines()[1].startswith("counters"), panel.splitlines()[1]

    @pytest.mark.os_agnostic
    @pytest.mark.asyncio
    async def test_without_a_recorded_past_the_page_explains_itself_instead_of_showing_an_empty_table(self) -> None:
        """ "Nothing has moved" and "nothing was recorded" are different answers.

        An empty table says neither, so the table is hidden and the explanation
        the printed view gives takes the whole page.
        """
        machine = inventory()
        app = LsdskApp(machine, History(hostname=machine.hostname))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("8")
            await pilot.pause()
            shown = app.query_one("#trend-table", DataTable).display
            buffer = io.StringIO()
            Console(width=118, file=buffer, no_color=True).print(app.query_one("#trend-body", Static).content)
            said = buffer.getvalue()

        assert not shown, "an empty table is showing where the explanation belongs"
        assert "recorded" in said, said[:200]


@pytest.mark.os_agnostic
def test_every_page_takes_its_columns_from_the_printed_table_of_the_same_name() -> None:
    """The shape, not the three instances of it.

    A page that writes its own column list has no guard whatever a page-NAME
    test says, and forgetting a column is silent: the page still renders, still
    passes, and simply stops answering something. Measured on the code this
    replaces - the controllers page was missing ``free`` and ``load`` and meant
    a different thing by ``ports``, and the health page identified a drive by
    path alone, having dropped ``model``. The disk page had already been fixed
    for exactly that, and the fix had not been carried to its neighbours.

    Every page is asserted here rather than one per test, so a page added with
    a hand-written list fails the moment it is added.
    """
    from lsdsk.adapters.render import report, tables
    from lsdsk.adapters.tui import app as page

    marked = {
        "controllers": (page.CONTROLLER_COLUMNS, tables.CONTROLLER_COLUMNS),
        "disks": (page.DISK_COLUMNS, tables.DISK_COLUMNS),
        "health": (page.HEALTH_COLUMNS, tables.HEALTH_COLUMNS),
    }
    for name, (drawn, printed) in marked.items():
        assert drawn == ("", *(column.title for column in printed)), f"the {name} page has its own column list"
    # Two pages carry no severity gutter, so their titles are the printed set.
    assert tuple(column.title for column in report.SLOT_COLUMNS) == page.SLOT_COLUMNS
    assert tuple(column.title for column in TREND_COLUMNS) == page.TREND_PAGE_COLUMNS


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_controller_row_and_a_health_row_carry_every_value_the_printed_row_does() -> None:
    """The columns agreeing is not the cells agreeing.

    A page could take the right headings and still fill them from its own
    arithmetic, which is how the controllers page came to print a port count
    that meant something different from the printed table's under the same
    heading.
    """
    from lsdsk.adapters.render import tables

    machine = inventory()
    findings = diagnose(machine)
    app = LsdskApp(machine)
    async with app.run_test(size=(200, 45)) as pilot:
        await pilot.pause()
        controllers = rows_of(app.query_one("#controller-table"))
        health = rows_of(app.query_one("#health-table"))
        drawn_controllers = [controllers.get_row_at(index) for index in range(controllers.row_count)]
        drawn_health = [health.get_row_at(index) for index in range(health.row_count)]

    for index, controller in enumerate(machine.controllers):
        printed = tables.controller_table_row(controller, machine, findings)
        expected = [printed.marker[0], *(printed.cells[column.key][0] for column in tables.CONTROLLER_COLUMNS)]
        assert [cell.plain for cell in drawn_controllers[index]] == expected, controller.address

    for index, disk in enumerate(machine.disks):
        printed = tables.health_table_row(disk, machine, findings, app.history)
        expected = [printed.marker[0], *(printed.cells[column.key][0] for column in tables.HEALTH_COLUMNS)]
        assert [cell.plain for cell in drawn_health[index]] == expected, disk.path
