"""The printed filesystems table: one row per filesystem on an inventoried disk."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from rich.cells import cell_len
from rich.console import Console
from textual.widgets import Static

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables, theme
from lsdsk.adapters.render.layout import ELLIPSIS
from lsdsk.adapters.render.tables import (
    FILESYSTEM_COLUMNS,
    FILESYSTEM_FRAG_NOT_APPLICABLE,
    FILESYSTEM_NOT_APPLICABLE_LEGEND,
    filesystem_legend_for,
    filesystem_table_row,
    render_filesystems,
)
from lsdsk.adapters.tui import LsdskApp
from lsdsk.domain.enums import FilesystemKind, Severity
from lsdsk.domain.models import Filesystem, Finding, FreeSpace, Inventory, RefusedReading


def _inventory(*rows: Filesystem) -> Inventory:
    return Inventory(hostname="box", filesystems=rows)


def _text_of(inventory: Inventory, findings: tuple[Finding, ...] = (), width: int = 160) -> str:
    console = Console(width=width, record=True, color_system=None)
    console.print(render_filesystems(inventory, findings, width=width))
    return console.export_text()


def _text(*rows: Filesystem, width: int = 160) -> str:
    return _text_of(_inventory(*rows), width=width)


# The row carries a fragmentation figure so the legend test sees no marker it did not mean to draw.
_EXT4 = Filesystem(
    kind=FilesystemKind.MOUNT,
    name="/var",
    fs_type="ext4",
    disks=("sda",),
    size_bytes=100,
    used_bytes=50,
    free_bytes=50,
    inodes_total=10,
    inodes_free=5,
    free_space=FreeSpace(fragmentation_percent=10),
)
# The row carries a fragmentation figure so the legend test sees no marker it did not mean to draw.
_POOL = Filesystem(
    kind=FilesystemKind.POOL,
    name="tank",
    fs_type="zfs",
    disks=("sdb", "sdc"),
    size_bytes=100,
    used_bytes=10,
    free_bytes=90,
    free_space=FreeSpace(fragmentation_percent=20),
)
_UNREAD = Filesystem(kind=FilesystemKind.MOUNT, name="/data", fs_type="ext4", disks=("sdd",))


def test_a_row_carries_every_figure_under_its_own_heading() -> None:
    fs = Filesystem(
        kind=FilesystemKind.MOUNT,
        name="/var",
        fs_type="ext4",
        disks=("sda",),
        size_bytes=20_000_000_000,
        used_bytes=19_100_000_000,
        free_bytes=900_000_000,
        inodes_total=100,
        inodes_free=10,
    )
    row = filesystem_table_row(fs, both_scales=False)
    assert {key: row[key][0] for key in ("filesystem", "type", "use", "inodes", "disks")} == {
        "filesystem": "/var",
        "type": "ext4",
        "use": "96%",
        "inodes": "90%",
        "disks": "sda",
    }


def test_a_dynamic_inode_table_reads_not_applicable_and_an_unread_figure_reads_a_dash() -> None:
    fs = Filesystem(kind=FilesystemKind.POOL, name="tank", fs_type="zfs", disks=("sda",))
    row = filesystem_table_row(fs, both_scales=False)
    assert (row["inodes"][0], row["use"][0], row["size"][0]) == (
        theme.NOT_APPLICABLE,
        theme.NOT_READ,
        theme.NOT_READ,
    )


def test_other_mounts_are_counted_not_listed() -> None:
    fs = Filesystem(kind=FilesystemKind.MOUNT, name="/", other_mounts=("/a", "/b"), disks=("sda",))
    assert filesystem_table_row(fs, both_scales=False)["filesystem"][0] == "/ +2"


def test_sizes_name_both_scales_in_the_rich_form_and_one_in_the_plain() -> None:
    fs = Filesystem(
        kind=FilesystemKind.MOUNT, name="/", size_bytes=500_107_862_016, used_bytes=1, free_bytes=1, disks=("sda",)
    )
    assert filesystem_table_row(fs, both_scales=True)["size"][0] == theme.format_size_both(500_107_862_016)
    assert filesystem_table_row(fs, both_scales=False)["size"][0] == theme.format_size(500_107_862_016)


def test_several_disks_are_joined_with_a_space() -> None:
    assert filesystem_table_row(_POOL, both_scales=False)["disks"][0] == "sdb sdc"


def test_the_legend_names_only_the_markers_drawn() -> None:
    plain = _text(_EXT4)
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND not in plain
    assert tables.FILESYSTEM_NOT_READ_LEGEND not in plain
    with_pool = _text(_EXT4, _POOL)
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND in with_pool
    assert tables.FILESYSTEM_NOT_READ_LEGEND not in with_pool
    assert tables.FILESYSTEM_NOT_READ_LEGEND in _text(_EXT4, _POOL, _UNREAD)


def test_the_legend_is_not_written_for_a_marker_whose_column_was_dropped() -> None:
    wide = _text(_EXT4, _POOL, width=160)
    assert "inodes%" in wide
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND in wide
    narrow = _text(_EXT4, _POOL, width=60)
    assert "inodes%" not in narrow
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND not in narrow


def test_an_inventory_that_read_no_filesystems_says_so() -> None:
    assert "Filesystems were not read." in _text_of(Inventory(hostname="box", filesystems=None))


def test_an_inventory_that_read_them_does_not_say_they_were_not() -> None:
    assert "Filesystems were not read." not in _text(_EXT4)


def test_the_title_names_the_machine() -> None:
    assert "Filesystems on box" in _text(_EXT4)


def test_a_finding_on_a_filesystem_marks_its_row() -> None:
    finding = Finding(severity=Severity.WARNING, subject="/var", title="/var is at or above 95% full")
    marked = _text_of(_inventory(_EXT4), (finding,))
    unmarked = _text_of(_inventory(_EXT4))
    assert theme.marker_for(Severity.WARNING) in marked.splitlines()[2]
    assert theme.marker_for(Severity.WARNING) not in unmarked.splitlines()[2]


def test_filesystem_use_and_disks_survive_every_width() -> None:
    dropped = False
    for width in range(200, 39, -1):
        rendered = _text(_EXT4, _POOL, width=width)
        for heading in ("filesystem", "use%", "disks"):
            assert heading in rendered, f"{heading} lost at width {width}"
        present = "inodes%" in rendered
        assert not (dropped and present), f"inodes% came back at width {width}"
        header = rendered.splitlines()[1]
        assert not ("frag" in header and "inodes%" not in header), f"frag outlived inodes% at width {width}"
        dropped = dropped or not present


def test_a_read_but_empty_list_says_none_was_found_and_not_that_it_was_unread() -> None:
    text = _text()
    assert tables.FILESYSTEMS_NONE_SENTENCE in text
    assert tables.FILESYSTEMS_NOT_READ_SENTENCE not in text


def test_a_non_empty_list_draws_no_none_sentence() -> None:
    assert tables.FILESYSTEMS_NONE_SENTENCE not in _text(_EXT4)


def test_an_unread_list_does_not_claim_none_was_found() -> None:
    assert tables.FILESYSTEMS_NONE_SENTENCE not in _text_of(Inventory(hostname="box", filesystems=None))


def test_two_filesystems_sharing_a_name_both_draw() -> None:
    covered = Filesystem(kind=FilesystemKind.MOUNT, name="/mnt", fs_type="ext4", disks=("sda",))
    covering = Filesystem(kind=FilesystemKind.MOUNT, name="/mnt", fs_type="xfs", disks=("sdb",))
    text = _text(covered, covering)
    assert "ext4" in text
    assert "xfs" in text


_FRAGMENTED = FreeSpace(
    fragmentation_percent=37, free_extent_count=10, free_extent_bytes=40960, largest_free_extent_bytes=8192
)


@pytest.mark.parametrize(
    ("filesystem", "cell"),
    [
        (Filesystem(kind=FilesystemKind.MOUNT, name="/", fs_type="ext4", free_space=_FRAGMENTED), "37%"),
        (Filesystem(kind=FilesystemKind.MOUNT, name="/", fs_type="ext4"), theme.NOT_READ),
        (Filesystem(kind=FilesystemKind.MOUNT, name="/boot/efi", fs_type="vfat"), theme.NOT_APPLICABLE),
        (
            Filesystem(
                kind=FilesystemKind.POOL, name="tank", fs_type="zfs", free_space=FreeSpace(fragmentation_percent=None)
            ),
            theme.NOT_APPLICABLE,
        ),
        (
            Filesystem(
                kind=FilesystemKind.POOL, name="tank", fs_type="zfs", free_space=FreeSpace(fragmentation_percent=22)
            ),
            "22%",
        ),
    ],
)
def test_the_frag_cell_says_figure_unread_or_impossible(filesystem: Filesystem, cell: str) -> None:
    assert filesystem_table_row(filesystem, both_scales=True)["frag"][0] == cell


def test_the_n_a_legend_names_each_column_that_drew_it() -> None:
    legend = filesystem_legend_for({"inodes": {theme.NOT_APPLICABLE}, "frag": {theme.NOT_APPLICABLE}})
    assert "no fixed inode table on this filesystem (inodes%)" in legend
    assert f"{FILESYSTEM_FRAG_NOT_APPLICABLE} (frag)" in legend


def test_one_column_drawing_n_a_keeps_the_plain_sentence() -> None:
    assert filesystem_legend_for({"inodes": {theme.NOT_APPLICABLE}}) == FILESYSTEM_NOT_APPLICABLE_LEGEND


def test_frag_is_the_first_column_to_drop() -> None:
    order = sorted((c for c in FILESYSTEM_COLUMNS if c.priority > 0), key=lambda c: -c.priority)
    assert [c.key for c in order][:2] == ["frag", "inodes"]


# A long mountpoint with a bind mount beside it, the case where the count matters most and was lost.
_LONG_BOUND = Filesystem(
    kind=FilesystemKind.MOUNT,
    name="/srv/a mount with spaces and a very long name that goes on",
    other_mounts=("/srv/bind",),
    fs_type="ext4",
    disks=("sdb",),
    size_bytes=100,
    used_bytes=50,
    free_bytes=50,
    inodes_total=10,
    inodes_free=5,
    free_space=FreeSpace(fragmentation_percent=10),
)
# Eight disks, so the disks cell is the widest thing on the row and the first flexible one a squeeze reaches.
_WIDE_POOL = Filesystem(
    kind=FilesystemKind.POOL,
    name="tank",
    fs_type="zfs",
    disks=tuple(f"sd{letter}" for letter in "abcdefgh"),
    size_bytes=100,
    used_bytes=10,
    free_bytes=90,
    free_space=FreeSpace(fragmentation_percent=20),
)
# Every column the table may drop, in the order it drops them.
_DROPPABLE_TITLES = ("frag", "inodes%", "type", "size", "used", "free")


def test_the_other_mounts_count_survives_every_width_and_only_the_name_is_clipped() -> None:
    for width in range(200, 29, -1):
        row = _text(_LONG_BOUND, width=width).splitlines()[2]
        assert " +1 " in row, f"the +1 was lost at width {width}: {row!r}"
        if _LONG_BOUND.name not in row:
            assert f"{ELLIPSIS} +1 " in row, f"the name was cut without its mark at width {width}: {row!r}"


def test_the_disks_cell_is_cut_only_once_every_droppable_column_is_gone() -> None:
    whole = " ".join(_WIDE_POOL.disks)
    cut_at: list[int] = []
    for width in range(200, 29, -1):
        rendered = _text(_EXT4, _WIDE_POOL, width=width)
        if whole in rendered:
            continue
        cut_at.append(width)
        header = rendered.splitlines()[1].split()
        standing = [title for title in _DROPPABLE_TITLES if title in header]
        assert not standing, f"disks cut at width {width} while {standing} still stand"
    assert cut_at, "the arm never reached a width that cuts the disks cell, so it asserted nothing"


def test_the_capture_s_pool_keeps_its_disks_whole_while_a_droppable_column_stands() -> None:
    fixture = Path(__file__).parent / "fixtures" / "hw" / "linux-zfs-filesystems.json"
    machine = build_from(json.loads(fixture.read_text(encoding="utf-8")))
    pool = next(one for one in machine.filesystems or () if len(one.disks) > 1)
    whole = " ".join(pool.disks)
    for width in range(200, 29, -1):
        lines = _text_of(machine, width=width).splitlines()
        # The marker column and its padding take the first four characters of every row.
        row = next(line for line in lines[2:] if line[4:].startswith(f"{pool.name} "))
        header = lines[1].split()
        standing = [title for title in _DROPPABLE_TITLES if title in header]
        assert whole in row or not standing, f"{pool.name}'s disks cut at width {width} while {standing} stand"


# Two names that differ only after a long shared prefix, beside a pool whose disk list is the widest cell.
_TWIN_NAMES = tuple(
    Filesystem(
        kind=FilesystemKind.MOUNT,
        name=f"/mnt/pool-{letter}/backups/daily",
        fs_type="ext4",
        disks=(f"sd{letter}",),
        size_bytes=4_000_000_000_000,
        used_bytes=1_500_000_000_000,
        free_bytes=2_500_000_000_000,
        inodes_total=10,
        inodes_free=5,
        free_space=FreeSpace(fragmentation_percent=10),
    )
    for letter in "ab"
)
_TWELVE_DISK_POOL = Filesystem(
    kind=FilesystemKind.POOL,
    name="tank",
    fs_type="zfs",
    disks=tuple(f"sd{letter}" for letter in "abcdefghijkl"),
    size_bytes=4_000_000_000_000,
    used_bytes=1_500_000_000_000,
    free_bytes=2_500_000_000_000,
    free_space=FreeSpace(fragmentation_percent=20),
)


def test_a_filesystem_name_is_cut_only_once_every_droppable_column_is_gone() -> None:
    """A cut name can read the same as its neighbour's, which a dropped column or a second size scale never does."""
    cut_at: list[int] = []
    for width in range(220, 19, -1):
        rendered = _text(*_TWIN_NAMES, _TWELVE_DISK_POOL, width=width)
        if all(one.name in rendered for one in _TWIN_NAMES):
            continue
        cut_at.append(width)
        lines = rendered.splitlines()
        header = next(line for line in lines if line.startswith(" ")).split()
        standing = [title for title in _DROPPABLE_TITLES if title in header]
        assert not standing, f"names cut at width {width} while {standing} still stand:\n{rendered}"
    assert cut_at, "the arm never reached a width that cuts a name, so it asserted nothing"


# A mountpoint in CJK, every character of which takes two terminal cells, with a bind mount beside it.
_WIDE_CHARACTERS = Filesystem(
    kind=FilesystemKind.MOUNT,
    name="/media/user/写真とビデオのバックアップ領域",
    other_mounts=("/srv/bind",),
    fs_type="ext4",
    disks=("sdb",),
    size_bytes=4_000_000_000_000,
    used_bytes=1_500_000_000_000,
    free_bytes=2_500_000_000_000,
    inodes_total=10,
    inodes_free=5,
    free_space=FreeSpace(fragmentation_percent=10),
)


def test_a_name_in_wide_characters_is_drawn_whole_where_there_is_room() -> None:
    row = _text(_WIDE_CHARACTERS, _EXT4, width=120).splitlines()[2]
    assert f"{_WIDE_CHARACTERS.name} +1 " in row, row


def test_a_name_in_wide_characters_is_cut_by_cells_and_keeps_its_count() -> None:
    """Widths are terminal cells: counted in characters, Rich cut the name with its own mark and took the count."""
    for width in range(220, 29, -1):
        rendered = _text(_WIDE_CHARACTERS, _EXT4, width=width)
        assert "\u2026" not in rendered, f"Rich cut a cell at width {width}:\n{rendered}"
        assert all(cell_len(line) <= width for line in rendered.splitlines()), f"wider than {width}:\n{rendered}"
        row = rendered.splitlines()[2]
        assert " +1 " in row, f"the +1 was lost at width {width}: {row!r}"


def test_the_drop_order_comment_names_every_droppable_column_in_the_order_they_drop() -> None:
    """The comment above FILESYSTEM_COLUMNS is the approved order; it once left `free` out."""
    source = Path(tables.__file__).read_text(encoding="utf-8")
    block = source[: source.index("FILESYSTEM_COLUMNS: tuple[Column, ...] = (")].rsplit("\n\n", 1)[1]
    prose = " ".join(line.lstrip("# ").strip() for line in block.splitlines())
    stated = re.search(r"The approved drop order as the terminal narrows: (.+?)\.", prose)
    assert stated, prose
    droppable = sorted((column for column in FILESYSTEM_COLUMNS if column.priority > 0), key=lambda c: -c.priority)
    assert stated.group(1).split(", then ") == [column.title for column in droppable]


def _header_drawn(text: str) -> bool:
    """Whether a line of `text` is the column header: every heading word on one line."""
    headings = [column.title for column in FILESYSTEM_COLUMNS]
    return any(all(heading in line.split() for heading in headings) for line in text.splitlines())


@pytest.mark.parametrize(
    ("filesystems", "sentence"),
    [(None, tables.FILESYSTEMS_NOT_READ_SENTENCE), ((), tables.FILESYSTEMS_NONE_SENTENCE)],
    ids=["not-read", "none-found"],
)
def test_a_list_with_no_rows_draws_its_sentence_without_a_column_header(
    filesystems: tuple[Filesystem, ...] | None, sentence: str
) -> None:
    """A header names the columns of rows; over a list that has none it stands for nothing.

    The trend section's empty state and the interactive page both draw the
    sentence alone, so the printed table was the one view with a header row
    over a list that was not read.
    """
    text = _text_of(Inventory(hostname="box", filesystems=filesystems))
    assert "Filesystems on box" in text
    assert sentence in text
    assert not _header_drawn(text), text
    assert _header_drawn(_text(_EXT4)), "the control: a listed filesystem keeps its header"


_REFUSED = RefusedReading(reading="zfs-pools", reason="pool tank: stats refused")


@pytest.mark.parametrize("filesystems", [None, ()], ids=["not-read", "none-found"])
def test_a_refusal_is_named_on_the_printed_view_when_no_row_was_drawn(
    filesystems: tuple[Filesystem, ...] | None,
) -> None:
    """The refusal note is keyed on the record, never on whether rows were drawn.

    Neither builder records a filesystem refusal without rows beside it today,
    so the inventory is built by hand: the interactive page draws the note
    whenever the record holds one, and the printed view returned before it.
    """
    inventory = Inventory(hostname="box", filesystems=filesystems, unread_filesystems=(_REFUSED,))
    assert "Not read: zfs-pools (pool tank: stats refused)." in _text_of(inventory)
    assert "Not read:" not in _text_of(Inventory(hostname="box", filesystems=filesystems))


@pytest.mark.asyncio
@pytest.mark.parametrize("filesystems", [None, ()], ids=["not-read", "none-found"])
async def test_the_interactive_page_names_the_same_refusal_when_no_row_was_drawn(
    filesystems: tuple[Filesystem, ...] | None,
) -> None:
    """The view the printed one is aligned to: page 9 draws the note under a hidden table."""
    app = LsdskApp(Inventory(hostname="box", filesystems=filesystems, unread_filesystems=(_REFUSED,)))
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        legend = app.query_one("#filesystem-legend", Static)
        assert legend.display
        assert "Not read: zfs-pools (pool tank: stats refused)." in str(legend.content)
