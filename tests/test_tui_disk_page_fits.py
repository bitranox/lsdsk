"""The interactive disk page fits its columns to the window, as ``lsdsk disks`` fits them to a terminal.

The page used to add all thirteen columns whatever its width, in the order the printed table
declares them, so the identity columns (wwn, serial, firmware) pushed ``port``, ``disk`` and
``link`` - the three the page exists to compare - off the right edge below about 130 columns, and
a reader had to scroll sideways to see whether a drive was linked at full speed. It now gives
columns up by the printed table's own priorities, through the same fitting code, and fits again
when the window changes. Nothing is lost by that: the detail panel carries every identity field
for the drive under the cursor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.cells import cell_len

from lsdsk.adapters.config.tunables import DEFAULT_WWN_WIDTH
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.app import disk_page_layout
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

#: What the page is for: which drive, and how its link compares with both of its ends.
ESSENTIAL = ("device", "port", "disk", "link")


def _machine() -> Inventory:
    payload: dict[str, Any] = json.loads(CAPTURE.read_text(encoding="utf-8"))
    return build_from(payload)


def _headings(app: LsdskApp) -> list[str]:
    table = rows_of(app.query_one("#disk-table"))
    return [str(column.label) for column in table.ordered_columns]


def _cell_under(app: LsdskApp, node: str, heading: str) -> str:
    table = rows_of(app.query_one("#disk-table"))
    return str(table.get_row(node)[_headings(app).index(heading)])


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 100, 120])
async def test_the_columns_the_page_exists_for_stay_on_screen(width: int) -> None:
    app = LsdskApp(_machine())
    async with app.run_test(size=(width, 45)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        headings = _headings(app)
        table = app.query_one("#disk-table")
        drawn, room = table.virtual_size.width, table.scrollable_content_region.width
    for heading in ESSENTIAL:
        assert heading in headings, f"{heading} is off the page at {width} columns: {headings}"
    assert drawn <= room, f"the rows run {drawn - room} columns past a {room}-column page at {width}"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_wide_window_still_draws_every_column() -> None:
    """The control: fitting gives columns up only where they do not fit.

    It is also what holds the page and ``lsdsk disks`` to one column list: the
    headings are read off the table the page DREW, so a page that skipped one
    of the printed table's columns fails here.
    """
    app = LsdskApp(_machine())
    async with app.run_test(size=(260, 45)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        headings = _headings(app)
    assert [h for h in headings if h] == [column.title for column in tables.DISK_COLUMNS]


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_value_stays_under_its_own_heading_when_columns_are_given_up() -> None:
    machine = _machine()
    disk = machine.disks[0]
    printed = tables.disk_table_row(disk, machine.port_link_for(disk))
    app = LsdskApp(machine)
    async with app.run_test(size=(84, 45)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert "wwn" not in _headings(app), "the premise: at this width an identity column has gone"
        for heading in ("device", "link", "disk"):
            assert _cell_under(app, disk.node, heading) == printed[heading][0]


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_narrowing_the_window_fits_the_page_again() -> None:
    app = LsdskApp(_machine())
    async with app.run_test(size=(260, 45)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert "wwn" in _headings(app), "the control: a wide window draws the identity columns"
        await pilot.resize_terminal(84, 45)
        await pilot.pause()
        await pilot.pause()
        narrowed = _headings(app)
        await pilot.resize_terminal(260, 45)
        await pilot.pause()
        await pilot.pause()
        widened = _headings(app)
    assert "wwn" not in narrowed
    assert "link" in narrowed
    assert "wwn" in widened, "widening again must bring the columns back"


async def _page_against_printed(capture: str, widths: range) -> tuple[list[str], list[str]]:
    """Resize one app through ``widths``; collect where the page and ``lsdsk disks`` part, and where it scrolls."""
    payload: dict[str, Any] = json.loads((CAPTURE.parent / f"{capture}.json").read_text(encoding="utf-8"))
    app = LsdskApp(build_from(payload))
    differ: list[str] = []
    scrolled: list[str] = []
    async with app.run_test(size=(widths[0], 45)) as pilot:
        await pilot.press("3")
        for width in widths:
            await pilot.resize_terminal(width, 45)
            await pilot.pause()
            table = app.query_one("#disk-table")
            region, drawn = table.scrollable_content_region.width, table.virtual_size.width
            page = [heading for heading in _headings(app) if heading]
            fitted = tables.render_disks(app.inventory, app.findings, region)
            printed = [str(column.header) for column in fitted.columns if str(column.header)]
            if page != printed:
                differ.append(f"{width}: page {page} printed {printed}")
            if drawn > region:
                scrolled.append(f"{width}: {drawn} > {region}")
    return differ, scrolled


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["linux-sas-hba", "windows-chipset-switch"])
async def test_the_page_keeps_every_column_the_printed_table_keeps_at_its_width(capture: str) -> None:
    """``lsdsk disks`` at the page's own width is the yardstick, from 70 columns to 160.

    The page once reckoned its gutter and a gap twice, so for five columns of
    width after each point where the printed table took a column back, the page
    still left it off. Both captures draw a one-character marker, which is
    where the page and the printed table can agree at every width.
    """
    differ, scrolled = await _page_against_printed(capture, range(70, 161))

    assert not differ, differ
    assert not scrolled, scrolled


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["linux-usb-ehci", "linux-minimal"])
async def test_the_page_never_scrolls_sideways_whatever_marker_it_draws(capture: str) -> None:
    """The control on the marker's width: ``!!`` in one capture, no marker in the other.

    The page's marker column is as wide as the widest marker it draws, so a
    room that assumed one width would run past the page on the capture
    carrying ``!!``. Every third width, because a room one cell too generous
    overruns at most widths rather than at the column transitions alone.
    """
    _, scrolled = await _page_against_printed(capture, range(70, 161, 3))

    assert not scrolled, scrolled


def _printed_headings(inventory: Inventory, width: int) -> list[str]:
    """The column headings ``lsdsk disks`` draws on a console ``width`` cells wide."""
    findings = list(diagnose(inventory, thresholds=DEFAULT_THRESHOLDS))
    fitted = tables.render_disks(inventory, findings, width)
    return [str(column.header) for column in fitted.columns if str(column.header)]


def _marker_width(inventory: Inventory) -> int:
    """How wide the page draws its marker column: the widest severity marker a row carries."""
    findings = list(diagnose(inventory, thresholds=DEFAULT_THRESHOLDS))
    rows = tables.disk_table_rows(inventory, findings, expand_virtual=False)
    return max((cell_len(row.marker[0]) for row in rows.rich), default=0)


def _around_each_change(inventory: Inventory, widths: range) -> list[int]:
    """Every width where ``lsdsk disks`` changes its columns, and the width just before it.

    The page and the printed table can only part where one of them changes its
    columns, so these are the widths a sample has to contain; between two of
    them both lists hold still.
    """
    picked: set[int] = set()
    previous = _printed_headings(inventory, widths[0] - 1)
    for width in widths:
        current = _printed_headings(inventory, width)
        if current != previous:
            picked.update({width - 1, width})
        previous = current
    return sorted(picked)


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["linux-minimal", "linux-usb-ehci"])
async def test_the_page_never_keeps_a_column_the_printed_table_gives_up(capture: str) -> None:
    """At each width where ``lsdsk disks`` changes its columns, from 30 to 160, and the one before it.

    Driven through the window on two captures, the page with no marker and the
    page with ``!!``; every capture at every width is held below through the
    function the page draws with. The page once fitted its columns to its room
    rather than to its width, one cell more generous than the printed table at
    the same width, so a page whose rows carry no marker kept a column the
    printed table had just given up. Where no marker is drawn the page costs
    exactly what the printed table does, so there the two lists are one.
    """
    payload: dict[str, Any] = json.loads((CAPTURE.parent / f"{capture}.json").read_text(encoding="utf-8"))
    inventory = build_from(payload)
    regions = _around_each_change(inventory, range(30, 161))
    assert len(regions) >= 8, f"the premise: the printed table changes its columns across the sweep: {regions}"
    unmarked = _marker_width(inventory) == 0
    extra: list[str] = []
    parted: list[str] = []
    app = LsdskApp(inventory)
    async with app.run_test(size=(regions[0] + 2, 45)) as pilot:
        await pilot.press("3")
        for region in regions:
            await pilot.resize_terminal(region + 2, 45)
            await pilot.pause()
            table = app.query_one("#disk-table")
            drawn = table.scrollable_content_region.width
            assert drawn == region, f"the premise: a window two cells wider gives the table {region}, not {drawn}"
            page = [heading for heading in _headings(app) if heading]
            printed = _printed_headings(inventory, region)
            if [heading for heading in printed if heading in page] != page:
                extra.append(f"{region}: page {page} printed {printed}")
            if unmarked and page != printed:
                parted.append(f"{region}: page {page} printed {printed}")
    assert not extra, extra
    assert not parted, parted


ALL_CAPTURES = sorted(path.stem for path in CAPTURE.parent.glob("*.json"))
#: The DataTable's padding on each side of a column, as the page sets it.
PAGE_CELL_PADDING = 1


@pytest.mark.parametrize("capture", ALL_CAPTURES)
def test_at_every_width_the_page_keeps_no_column_the_printed_table_drops(capture: str) -> None:
    """Every capture, every width from 30 to 160, through the function the page draws with.

    Two claims at each width: no column the printed table gave up, and a row
    that fits the table without a sideways scroll. Where no marker is drawn
    the page costs exactly what the printed table does, so there the two
    column lists are one.
    """
    payload: dict[str, Any] = json.loads((CAPTURE.parent / f"{capture}.json").read_text(encoding="utf-8"))
    inventory = build_from(payload)
    findings = list(diagnose(inventory, thresholds=DEFAULT_THRESHOLDS))
    rows = tables.disk_table_rows(inventory, findings, expand_virtual=False)
    marker = _marker_width(inventory)
    extra: list[str] = []
    parted: list[str] = []
    overrun: list[str] = []
    for width in range(30, 161):
        fitted = disk_page_layout(tables.disk_columns(DEFAULT_WWN_WIDTH), rows, width, cell_padding=PAGE_CELL_PADDING)
        page = [column.title for column in fitted.columns]
        printed = _printed_headings(inventory, width)
        if [heading for heading in printed if heading in page] != page:
            extra.append(f"{width}: page {page} printed {printed}")
        if marker == 0 and page != printed:
            parted.append(f"{width}: page {page} printed {printed}")
        drawn = marker + sum(fitted.widths[column.key] for column in fitted.columns)
        drawn += 2 * PAGE_CELL_PADDING * (len(fitted.columns) + 1)
        if drawn > width:
            overrun.append(f"{width}: the row takes {drawn}")
    assert not extra, extra
    assert not parted, parted
    assert not overrun, overrun


def test_the_sweep_holds_both_kinds_of_page() -> None:
    """The sweep above reaches pages with and without a marker, or its equality claim holds over nothing."""
    markers = {
        _marker_width(build_from(json.loads((CAPTURE.parent / f"{name}.json").read_text(encoding="utf-8"))))
        for name in ALL_CAPTURES
    }
    assert 0 in markers
    assert markers - {0}
