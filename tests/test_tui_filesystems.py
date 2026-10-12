"""The interactive filesystems page: the printed table's columns, rows and sentences.

Each test presses the key a reader presses and reads back what the page holds,
with a value asserted under its own heading rather than somewhere on the row.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from textual.widgets import DataTable, Static, TabbedContent

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables, theme
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui import palette as tui_palette
from lsdsk.adapters.tui.app import FILESYSTEM_COLUMNS
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.enums import FilesystemKind
from lsdsk.domain.models import Filesystem, Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

_EXT4 = Filesystem(
    kind=FilesystemKind.MOUNT,
    name="/var",
    fs_type="ext4",
    disks=("sda",),
    size_bytes=1000,
    used_bytes=500,
    free_bytes=500,
    inodes_total=10,
    inodes_free=5,
)
_FULL = Filesystem(
    kind=FilesystemKind.MOUNT,
    name="/srv",
    fs_type="xfs",
    disks=("sdb",),
    size_bytes=1000,
    used_bytes=980,
    free_bytes=20,
    inodes_total=10,
    inodes_free=5,
)
_POOL = Filesystem(
    kind=FilesystemKind.POOL,
    name="tank",
    fs_type="zfs",
    disks=("sdc",),
    size_bytes=1000,
    used_bytes=100,
    free_bytes=900,
)
_UNREAD = Filesystem(kind=FilesystemKind.MOUNT, name="/data", fs_type="ext4", disks=("sdd",))


def machine(*rows: Filesystem) -> Inventory:
    """The capture, holding exactly these filesystems (no argument: not read)."""
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return build_from(payload).with_changes(filesystems=rows if rows else None)


def read_machine(*rows: Filesystem) -> Inventory:
    """The capture whose filesystems WERE read and hold exactly these (possibly none)."""
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return build_from(payload).with_changes(filesystems=rows)


def cells_of(app: LsdskApp, name: str) -> dict[str, str]:
    """One filesystem's page row as heading -> text, so a value is judged under its heading."""
    drawn = app.inventory.filesystems or ()
    position = next(index for index, one in enumerate(drawn) if one.name == name)
    row = rows_of(app.query_one("#filesystem-table")).get_row(str(position))
    return dict(zip(FILESYSTEM_COLUMNS, (str(cell) for cell in row), strict=True))


def legend_text(app: LsdskApp) -> str:
    """What the legend under the table says; empty when it is not shown."""
    legend = app.query_one("#filesystem-legend", Static)
    return str(legend.content) if legend.display else ""


async def legend_for(*rows: Filesystem) -> str:
    """The legend the page draws for a machine holding these filesystems."""
    app = LsdskApp(machine(*rows))
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        return legend_text(app)


@pytest.mark.asyncio
async def test_the_page_headings_are_the_printed_tables_own() -> None:
    app = LsdskApp(machine(_EXT4))
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        headings = tuple(str(column.label) for column in rows_of(app.query_one("#filesystem-table")).columns.values())

    assert headings == ("", *(column.title for column in tables.FILESYSTEM_COLUMNS))
    assert headings == FILESYSTEM_COLUMNS


@pytest.mark.asyncio
async def test_every_row_lands_under_its_own_heading() -> None:
    app = LsdskApp(machine(_EXT4, _POOL))
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        var, tank = cells_of(app, "/var"), cells_of(app, "tank")

    assert var["filesystem"] == "/var"
    assert var["type"] == "ext4"
    assert var["use%"] == "50%"
    assert var["inodes%"] == "50%"
    assert var["disks"] == "sda"
    assert tank["type"] == "zfs"
    assert tank["use%"] == "10%"
    assert tank["inodes%"] == theme.NOT_APPLICABLE, "a pool has no fixed inode table"
    assert tank["disks"] == "sdc"


@pytest.mark.asyncio
async def test_a_full_filesystem_carries_its_finding_marker() -> None:
    app = LsdskApp(machine(_EXT4, _FULL))
    # The finding comes from the real diagnosis of a 98% full mount, not from an
    # injected one, so the page proves it reads what the rule raises.
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        quiet, full = cells_of(app, "/var"), cells_of(app, "/srv")

    assert quiet[""].strip() == "", "a filesystem nothing is wrong with carries no marker"
    assert full[""].strip() != "", "a filesystem at 98% did not carry the marker its finding earns"


@pytest.mark.asyncio
async def test_the_legend_names_only_the_markers_drawn() -> None:
    none_drawn = await legend_for(_EXT4)
    all_drawn = await legend_for(_POOL, _UNREAD)
    na_only = await legend_for(_POOL)

    assert none_drawn == ""
    assert tables.FILESYSTEM_NOT_READ_LEGEND in all_drawn
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND in all_drawn
    assert tables.FILESYSTEM_NOT_READ_LEGEND not in na_only
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND in na_only


@pytest.mark.asyncio
async def test_filesystems_that_were_not_read_are_said_so_not_shown_as_an_empty_table() -> None:
    app = LsdskApp(machine())
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        note = app.query_one("#filesystem-note", Static)
        shown = (note.display, str(note.content))
        table_shown = app.query_one("#filesystem-table", DataTable).display

    assert shown[0], "the page did not say the filesystems were not read"
    assert tables.FILESYSTEMS_NOT_READ_SENTENCE in shown[1]
    assert not table_shown, "an empty table beside no sentence reads as 'no filesystems'"


@pytest.mark.asyncio
async def test_a_read_inventory_draws_no_not_read_sentence() -> None:
    app = LsdskApp(machine(_EXT4))
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        note_shown = app.query_one("#filesystem-note", Static).display
        table_shown = app.query_one("#filesystem-table", DataTable).display

    assert not note_shown
    assert table_shown


@pytest.mark.asyncio
async def test_f9_opens_the_filesystems_page_and_f10_rescans_without_quitting() -> None:
    app = LsdskApp(machine(_EXT4))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("f9")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "filesystems"
        before = app.findings
        await pilot.press("f10")
        await pilot.pause()
        assert app.findings is not before, "f10 did not reach the rescan action"
        assert app.is_running, "f10 quit the app; it rescans now"
        assert app.query_one(TabbedContent).active == "filesystems", "rescan should not move the page"


@pytest.mark.asyncio
async def test_a_rescan_does_not_add_the_filesystem_columns_or_rows_twice() -> None:
    app = LsdskApp(machine(_EXT4, _POOL))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        table = rows_of(app.query_one("#filesystem-table"))
        shape = (len(table.columns), table.row_count)
        await pilot.press("r")
        await pilot.pause()
        assert (len(table.columns), table.row_count) == shape


def _panel_plain(app: LsdskApp) -> str:
    """What the detail panel draws, as text."""
    buffer = io.StringIO()
    Console(width=160, file=buffer, no_color=True).print(app.query_one("#detail-body", Static).content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_no_printed_colour_reaches_the_filesystem_page_on_either_fill() -> None:
    """A row coloured by its finding is drawn in the interactive palette at mount and after a rescan."""
    printed = {
        value.upper()
        for name, value in vars(theme).items()
        if name.startswith("STYLE_") and isinstance(value, str) and value.startswith("#")
    }
    # The coloured row is not first: the cursor row is painted over by the table.
    app = LsdskApp(machine(_UNREAD, _FULL))
    # Real diagnosis: /srv is 98% full, so the rule raises its warning at mount,
    # and the rescan below re-diagnoses and must keep the marker.
    pictures: list[str] = []
    panels: list[str] = []
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        pictures.append(app.export_screenshot().upper())
        await pilot.press("f10")
        await pilot.pause()
        pictures.append(app.export_screenshot().upper())
        # The cursor above sits on the unread row, whose panel draws no finding.
        # Moving it onto the row that carries one sweeps the panel's finding block
        # on the rescan's fill, then again on a fresh fill after one more rescan.
        await pilot.press("down")
        await pilot.pause()
        pictures.append(app.export_screenshot().upper())
        panels.append(_panel_plain(app))
        await pilot.press("f10")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        pictures.append(app.export_screenshot().upper())
        panels.append(_panel_plain(app))

    for picture in pictures:
        assert not [hue for hue in printed if hue in picture], "the printed palette reached the filesystem page"
    # The control for the two passes added above: the panel really drew the finding's sentence.
    assert all("98% full" in text for text in panels), "the panel's finding block was never swept"
    # The controls: the mount draws the finding's marker and both fills draw the
    # legend for the unread cell, in interactive hues, so a page drawing no
    # colour at all cannot satisfy the loop above.
    assert all(tui_palette.PALETTE.warning.upper() in picture for picture in pictures), (
        "the control: the marker's hue was missing from the mount picture or the rescan picture"
    )
    assert all(tui_palette.PALETTE.unknown.upper() in picture for picture in pictures), "the control: no legend hue"


@pytest.mark.asyncio
async def test_a_read_but_empty_filesystem_list_says_none_was_found() -> None:
    app = LsdskApp(read_machine())
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        note = app.query_one("#filesystem-note", Static)
        shown = (note.display, str(note.content))
        table_shown = app.query_one("#filesystem-table", DataTable).display

    assert shown[0], "an empty list drew no sentence, which reads as fine"
    assert tables.FILESYSTEMS_NONE_SENTENCE in shown[1]
    assert tables.FILESYSTEMS_NOT_READ_SENTENCE not in shown[1]
    assert not table_shown


@pytest.mark.asyncio
async def test_a_non_empty_list_draws_neither_sentence_and_an_unread_one_only_the_not_read() -> None:
    app = LsdskApp(machine(_EXT4))
    unread = LsdskApp(machine())
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        listed_note_shown = app.query_one("#filesystem-note", Static).display
    async with unread.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        unread_note = str(unread.query_one("#filesystem-note", Static).content)

    assert not listed_note_shown
    assert tables.FILESYSTEMS_NOT_READ_SENTENCE in unread_note
    assert tables.FILESYSTEMS_NONE_SENTENCE not in unread_note


@pytest.mark.asyncio
async def test_two_filesystems_sharing_a_name_mount_and_both_rows_show() -> None:
    covered = Filesystem(kind=FilesystemKind.MOUNT, name="/mnt", fs_type="ext4", disks=("sda",))
    covering = Filesystem(kind=FilesystemKind.MOUNT, name="/mnt", fs_type="xfs", disks=("sdb",))
    app = LsdskApp(read_machine(covered, covering))
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        table = rows_of(app.query_one("#filesystem-table"))
        types = [str(table.get_row_at(index)[FILESYSTEM_COLUMNS.index("type")]) for index in range(table.row_count)]

    assert types == ["ext4", "xfs"]
