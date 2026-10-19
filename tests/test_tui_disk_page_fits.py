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

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.typed_table import rows_of

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
    """The control: fitting gives columns up only where they do not fit."""
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
    async with app.run_test(size=(90, 45)) as pilot:
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
        await pilot.resize_terminal(90, 45)
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
