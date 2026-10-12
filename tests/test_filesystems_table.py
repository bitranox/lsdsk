"""The printed filesystems table: one row per filesystem on an inventoried disk."""

from __future__ import annotations

from rich.console import Console

from lsdsk.adapters.render import tables, theme
from lsdsk.adapters.render.tables import filesystem_table_row, render_filesystems
from lsdsk.domain.enums import FilesystemKind, Severity
from lsdsk.domain.models import Filesystem, Finding, Inventory


def _inventory(*rows: Filesystem) -> Inventory:
    return Inventory(hostname="box", filesystems=rows)


def _text_of(inventory: Inventory, findings: tuple[Finding, ...] = (), width: int = 160) -> str:
    console = Console(width=width, record=True, color_system=None)
    console.print(render_filesystems(inventory, findings, width=width))
    return console.export_text()


def _text(*rows: Filesystem, width: int = 160) -> str:
    return _text_of(_inventory(*rows), width=width)


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
)
_POOL = Filesystem(
    kind=FilesystemKind.POOL,
    name="tank",
    fs_type="zfs",
    disks=("sdb", "sdc"),
    size_bytes=100,
    used_bytes=10,
    free_bytes=90,
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
