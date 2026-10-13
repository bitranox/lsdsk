"""The filesystems page's detail panel: what it adds to the table, and what it must not claim.

The table abbreviates other mountpoints to ``+N``, cannot say why a figure is
missing, and has no room for raw inode counts or the read-only flag. The panel
is where those live, so each is asserted against the value the model holds.
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
from lsdsk.adapters.render import detail, tables, theme
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.enums import FilesystemKind
from lsdsk.domain.filesystem_fill import diagnose_filesystems
from lsdsk.domain.models import Filesystem, FreeSpace, Inventory, RefusedReading

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
GIB = 1024**3


def _drawn(renderable: object, width: int = 118) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _panel(filesystem: Filesystem) -> str:
    return _drawn(detail.render_detail(detail.filesystem_detail(filesystem), []))


def _values(filesystem: Filesystem) -> dict[str, str]:
    """Every label the record carries as ``group.label``, mapped to its cell text."""
    record = detail.filesystem_detail(filesystem)
    return {f"{group.label.value}.{name}": cell[0] for group in record.groups for name, cell in group.values}


def _mount(**fields: Any) -> Filesystem:
    base: dict[str, Any] = {
        "kind": FilesystemKind.MOUNT,
        "name": "/srv",
        "fs_type": "ext4",
        "disks": ("sda",),
        "size_bytes": 100 * GIB,
        "used_bytes": 40 * GIB,
        "free_bytes": 60 * GIB,
        "inodes_total": 1000,
        "inodes_free": 250,
    }
    base.update(fields)
    return Filesystem(**base)


def _hidden(name: str = "/data") -> Filesystem:
    return Filesystem(
        kind=FilesystemKind.MOUNT,
        name=name,
        readings_refused=(RefusedReading(reading="statvfs", reason="hidden by a mount over it"),),
    )


def _zfs_machine() -> Inventory:
    payload: dict[str, Any] = json.loads((FIXTURES / "linux-zfs-filesystems.json").read_text(encoding="utf-8"))
    return build_from(payload)


@pytest.mark.os_agnostic
def test_every_other_mountpoint_is_named_where_the_table_says_plus_n() -> None:
    """The table writes `+2`; the panel owes the two names."""
    one = _mount(other_mounts=("/mnt/a", "/mnt/b"))

    assert _values(one)["identity.other mounts"] == "/mnt/a, /mnt/b"
    assert "+2" in tables.filesystem_table_row(one, both_scales=True)["filesystem"][0]


@pytest.mark.os_agnostic
def test_a_mount_with_no_other_mounts_says_so_rather_than_drawing_a_marker() -> None:
    """Nothing to list is a reading, not a missing one, and not the no-link marker either."""
    assert _values(_mount())["identity.other mounts"] == "(none)"


@pytest.mark.os_agnostic
def test_a_fixed_inode_table_shows_the_raw_counts_and_the_percentage_the_domain_computes() -> None:
    """Counts as read; the percentage is the model's, 750 used of 1000."""
    one = _mount()
    values = _values(one)

    assert (values["inodes.total"], values["inodes.free"]) == ("1000", "250")
    assert values["inodes.use%"] == f"{one.inodes_used_percent}%" == "75%"


@pytest.mark.os_agnostic
def test_a_dynamic_inode_table_is_not_applicable_even_when_the_capture_carries_counts() -> None:
    """The kind of table decides, so a count on a btrfs mount cannot read as a ceiling."""
    one = _mount(fs_type="btrfs")
    values = _values(one)

    assert [values[f"inodes.{name}"] for name in ("total", "free", "use%")] == [theme.NOT_APPLICABLE] * 3
    assert theme.NOT_APPLICABLE in _panel(one)


@pytest.mark.os_agnostic
def test_the_space_figures_use_the_tables_own_two_scale_writing() -> None:
    """Size, used and free are the table's text, and use% is read from the model."""
    one = _mount()
    row = tables.filesystem_table_row(one, both_scales=True)
    values = _values(one)

    assert (values["space.size"], values["space.used"], values["space.free"]) == (
        row["size"][0],
        row["used"][0],
        row["free"][0],
    )
    assert values["space.use%"] == row["use"][0]


@pytest.mark.os_agnostic
def test_a_pool_floors_its_percentage_where_a_mount_rounds_up() -> None:
    """The rule lives in the domain; the panel must print the figure the table does."""
    figures = {"size_bytes": 200, "used_bytes": 1, "free_bytes": 199}
    pool = _mount(kind=FilesystemKind.POOL, name="tank", fs_type="zfs", **figures)
    mount = _mount(**figures)

    assert _values(pool)["space.use%"] == "0%"
    assert _values(mount)["space.use%"] == "1%"


@pytest.mark.os_agnostic
def test_the_read_only_flag_is_drawn_both_ways() -> None:
    """A control with the flag clear must say no, or yes would be decoration."""
    assert _values(_mount(read_only=True))["identity.read-only"] == "yes"
    assert _values(_mount())["identity.read-only"] == "no"


@pytest.mark.os_agnostic
def test_an_unread_filesystem_shows_dashes_and_the_reason_but_no_figure() -> None:
    """A hidden mount carries no figures; the panel says why instead of inventing any."""
    hidden = _hidden()
    values = _values(hidden)
    panel = _panel(hidden)

    assert [values[f"space.{name}"] for name in ("size", "used", "free", "use%")] == ["-"] * 4
    assert values["refused.statvfs"] == "hidden by a mount over it"
    assert "hidden by a mount over it" in panel
    assert "GiB" not in panel


@pytest.mark.os_agnostic
def test_a_filesystem_nothing_refused_has_no_refused_group() -> None:
    """The group heading appears only when there is a refusal to name."""
    refused = _mount(readings_refused=(RefusedReading(reading="statvfs", reason="denied"),))

    assert detail.REFUSED not in [group.label for group in detail.filesystem_detail(_mount()).groups]
    assert detail.REFUSED in [group.label for group in detail.filesystem_detail(refused).groups]


@pytest.mark.os_agnostic
def test_the_legend_names_only_the_markers_the_panel_drew() -> None:
    """A fully read fixed-inode mount draws neither marker, so it prints no legend."""
    # the row carries a fully counted free space, so the legend test sees no marker it did not mean to draw
    counted = FreeSpace(
        fragmentation_percent=10, free_extent_count=4, free_extent_bytes=4 * 2**20, largest_free_extent_bytes=2**20
    )
    clean = _panel(_mount(free_space=counted))
    dynamic = _panel(_mount(fs_type="btrfs"))
    unread = _panel(_hidden())

    assert "not read" not in clean
    assert theme.NO_LINK not in _values(_mount()).values()
    assert "no link trained" not in clean
    assert "does not apply" not in clean
    assert "does not apply" in dynamic
    assert "not read" not in dynamic
    assert "not read" in unread


@pytest.mark.os_agnostic
def test_the_scope_is_the_name_the_fill_findings_use() -> None:
    """Fill findings name the filesystem by ``name``, so that is the scope."""
    assert [scope.subject for scope in detail.filesystem_detail(_mount()).scopes] == ["/srv"]


@pytest.mark.os_agnostic
def test_a_row_that_read_no_fill_figure_claims_no_finding_of_the_mount_covering_it() -> None:
    """Fill findings are keyed by name; the hidden half of an overmount shares its name."""
    covering = _mount(name="/data", used_bytes=98 * GIB, free_bytes=2 * GIB)
    findings = diagnose_filesystems(_zfs_machine().with_changes(filesystems=(covering,)))
    assert findings, "the control: the covering mount raised no fill finding"

    hidden_panel = _drawn(detail.render_detail(detail.filesystem_detail(_hidden("/data")), findings))
    covering_panel = _drawn(detail.render_detail(detail.filesystem_detail(covering), findings))

    assert detail.filesystem_detail(_hidden("/data")).scopes == ()
    assert findings[0].title not in hidden_panel
    assert findings[0].title in covering_panel


def _filesystems(machine: Inventory) -> list[Filesystem]:
    return list(machine.filesystems or [])


def _panel_text(app: LsdskApp) -> str:
    buffer = io.StringIO()
    Console(width=118, file=buffer, no_color=True).print(app.query_one("#detail-body", Static).content)
    return buffer.getvalue()


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_arriving_on_page_nine_fills_the_panel_for_the_highlighted_pool() -> None:
    """Key 9, nothing else pressed: the panel is already about the row under the cursor."""
    machine = _zfs_machine()
    first = _filesystems(machine)[0]
    app = LsdskApp(machine)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        text = _panel_text(app)

    assert "Nothing selected." not in text
    assert first.name in text
    assert theme.format_size_both(first.size_bytes) in text


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_moving_the_cursor_moves_the_panel_and_a_rescan_keeps_it_filled() -> None:
    """Driven by the keys a reader presses: down, then f10."""
    machine = _zfs_machine()
    first, second = _filesystems(machine)[:2]
    assert first.name != second.name
    app = LsdskApp(machine)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        before = _panel_text(app)
        await pilot.press("down")
        await pilot.pause()
        moved = _panel_text(app)
        await pilot.press("f10")
        await pilot.pause()
        rescanned = _panel_text(app)
        table = rows_of(app.query_one("#filesystem-table"))
        assert table.row_count == len(_filesystems(machine))
        assert app.query_one(TabbedContent).active == "filesystems"
        cursor = _filesystems(machine)[app.query_one("#filesystem-table", DataTable).cursor_row]

    assert first.name in before.splitlines()[0]
    assert second.name in moved.splitlines()[0]
    assert cursor.name in rescanned.splitlines()[0], "the rescan left the panel answering for another row"


def _overmounted_machine() -> tuple[Inventory, Filesystem, Filesystem]:
    """Two rows of one name: the mount over it first, the hidden one it covers second."""
    covering = _mount(name="/data", used_bytes=98 * GIB, free_bytes=2 * GIB)
    return _zfs_machine().with_changes(filesystems=(covering, _hidden("/data"))), covering, _hidden("/data")


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_two_rows_sharing_a_name_each_answer_with_their_own_record() -> None:
    """Press 9 and walk the cursor over an overmount: the panel flips between the two rows.

    The rows are keyed by position, so a lookup by name would answer for the
    covering mount twice. The hidden row also must not borrow the covering
    mount's fill finding, which is keyed by the name they share.
    """
    machine, covering, hidden = _overmounted_machine()
    finding_title = diagnose_filesystems(machine)[0].title
    app = LsdskApp(machine)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        first = _panel_text(app)
        await pilot.press("down")
        await pilot.pause()
        second = _panel_text(app)
        await pilot.press("up")
        await pilot.pause()
        back = _panel_text(app)

    assert theme.format_size_both(covering.size_bytes) in first
    assert finding_title in first
    assert theme.format_size_both(covering.size_bytes) not in second
    assert "hidden by a mount over it" in second
    assert finding_title not in second
    assert theme.format_size_both(covering.size_bytes) in back
    assert hidden.readings_refused[0].reason not in back


def test_a_counted_filesystem_shows_its_free_space_layout() -> None:
    space = FreeSpace(
        fragmentation_percent=37, free_extent_count=4, free_extent_bytes=4 * 2**20, largest_free_extent_bytes=2 * 2**20
    )
    values = _values(
        Filesystem(kind=FilesystemKind.MOUNT, name="/", fs_type="ext4", free_space=space),
    )
    assert values["free space.fragmentation"] == "37%"
    assert values["free space.free extents"] == "4"
    assert values["free space.largest"] == theme.format_size_both(2 * 2**20)
    assert values["free space.average"] == theme.format_size_both(2**20)


def test_a_pool_shows_its_score_and_n_a_for_what_zfs_does_not_publish() -> None:
    pool = Filesystem(
        kind=FilesystemKind.POOL, name="tank", fs_type="zfs", free_space=FreeSpace(fragmentation_percent=22)
    )
    values = _values(pool)
    assert values["free space.fragmentation"] == "22%"
    assert {values[f"free space.{label}"] for label in ("free extents", "largest", "average")} == {theme.NOT_APPLICABLE}


def test_an_uncounted_ext4_shows_dashes_and_a_vfat_shows_n_a() -> None:
    unread = _values(Filesystem(kind=FilesystemKind.MOUNT, name="/", fs_type="ext4"))
    impossible = _values(Filesystem(kind=FilesystemKind.MOUNT, name="/e", fs_type="vfat"))
    labels = ("fragmentation", "free extents", "largest", "average")
    assert {unread[f"free space.{label}"] for label in labels} == {theme.NOT_READ}
    assert {impossible[f"free space.{label}"] for label in labels} == {theme.NOT_APPLICABLE}


def test_a_full_filesystem_with_no_free_extents_draws_not_applicable_where_no_figure_can_exist() -> None:
    zero = FreeSpace(free_extent_count=0, free_extent_bytes=0, largest_free_extent_bytes=0, histogram=(0,) * 21)
    full = _mount(free_space=zero)
    values = _values(full)
    assert values["free space.fragmentation"] == theme.NOT_APPLICABLE
    assert values["free space.average"] == theme.NOT_APPLICABLE
    assert values["free space.free extents"] == "0"
    assert theme.NOT_READ not in {
        values[f"free space.{label}"] for label in ("fragmentation", "free extents", "largest", "average")
    }
    assert "not read" not in _panel(full)
