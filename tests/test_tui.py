"""Interactive view tests, including the resize behaviour.

Terminal size is the thing most easily broken and least often checked, so the
app is driven at several real sizes rather than looked at once at one size.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Static, TabbedContent

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.layout import Column, fit, natural_widths, pad
from lsdsk.adapters.render.report import DISK_COLUMNS, render_tree
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.enums import Align
from lsdsk.domain.models import Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

# Sizes worth proving: a classic terminal, a comfortable window, and a wide one.
TERMINAL_SIZES = ((80, 24), (120, 40), (200, 60))


def inventory() -> Inventory:
    """Build the inventory every test in this module uses."""
    with FIXTURE.open(encoding="utf-8") as handle:
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
        assert any(style in styles for style in ("yellow", "bold red", "orange3")), (
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
