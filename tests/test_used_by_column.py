"""What a disk is used for, drawn in the table, the panel and the legend.

The domain already resolves `Disk.usage` to a boot flag and a tuple of uses,
or to `None` where nothing could be read; this is only the rendering - one
cell for the disks table, the same cell repeated in full in the detail panel,
and the legend that says what an unread marker means.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from lsdsk.adapters.render import detail, tables, theme
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import UseKind
from lsdsk.domain.models import Disk, DiskUsage, DiskUse, Inventory
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _drawn(renderable: object, width: int) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True, force_terminal=False).print(renderable)
    return buffer.getvalue()


def _stripped(text: str) -> str:
    return "".join(text.split())


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("usage", "text"),
    [
        (None, "-"),
        (DiskUsage(), "not mounted"),
        (DiskUsage(boot=True, uses=(DiskUse(kind=UseKind.ZFS, name="rpool"),)), "boot zfs:rpool"),
        (DiskUsage(boot=True, uses=(DiskUse(kind=UseKind.LETTER, mounts=("C:\\",)),)), "boot C:"),
        (DiskUsage(uses=(DiskUse(kind=UseKind.LVM, name="vg0", mounts=("/var", "/srv")),)), "lvm:vg0 -> /var, /srv"),
        (DiskUsage(uses=(DiskUse(kind=UseKind.MOUNT, mounts=("/",)), DiskUse(kind=UseKind.SWAP))), "/, swap"),
        (DiskUsage(boot=True), "boot"),
    ],
)
def test_a_usage_is_written_in_one_short_cell(usage: DiskUsage | None, text: str) -> None:
    assert theme.format_usage(usage)[0] == text


@pytest.mark.os_agnostic
def test_an_unread_usage_is_styled_as_unknown() -> None:
    """``-`` is dimmed because nobody read it; ``not mounted`` is a real reading and is not."""
    assert theme.format_usage(None)[1] == theme.STYLE_UNKNOWN
    assert theme.format_usage(DiskUsage())[1] != theme.STYLE_UNKNOWN


def _disk(node: str, usage: DiskUsage | None) -> Disk:
    return Disk(node=node, path=f"/dev/{node}", model="demo drive", usage=usage)


@pytest.mark.os_agnostic
def test_the_disks_table_heads_a_used_by_column_with_each_disk_s_cell() -> None:
    inventory = Inventory(
        hostname="demo",
        disks=(
            _disk("sdd", None),
            _disk("sde", DiskUsage(boot=True, uses=(DiskUse(kind=UseKind.ZFS, name="rpool"),))),
        ),
    )
    table = tables.render_disks(inventory, (), width=200)
    printed = _drawn(table, 200)
    assert "usedby" in _stripped(printed)
    rows = printed.splitlines()
    sde_row = next(line for line in rows if "sde" in line)
    assert "bootzfs:rpool" in _stripped(sde_row)


@pytest.mark.os_agnostic
def test_the_used_by_column_is_the_first_dropped_on_a_narrow_page() -> None:
    """At the tool's own piped default, ``used by`` (priority 8) gives way before ``size`` (priority 4)."""
    from lsdsk.adapters.config.tunables import DEFAULT_PIPED_WIDTH

    machine = _machine("linux-nvme-board")
    rich = [tables.disk_table_row(disk, machine.port_link_for(disk), bandwidth=True) for disk in machine.disks]
    plain = [tables.disk_table_row(disk, machine.port_link_for(disk)) for disk in machine.disks]
    texts_rich = [{key: value[0] for key, value in row.items()} for row in rich]
    texts_plain = [{key: value[0] for key, value in row.items()} for row in plain]
    layout = tables.Layout.preferring(tables.DISK_COLUMNS, texts_rich, texts_plain, DEFAULT_PIPED_WIDTH)
    chosen = {column.key for column in layout.columns}
    assert "used_by" not in chosen
    assert "size" in chosen


@pytest.mark.os_agnostic
def test_the_detail_panel_carries_the_whole_usage_and_names_the_unread_marker() -> None:
    unread = _disk("sdd", None)
    inventory_unread = Inventory(hostname="demo", disks=(unread,))
    panel_unread = _drawn(
        detail.render_detail(
            detail.disk_detail(unread, inventory_unread, thresholds=DEFAULT_THRESHOLDS), diagnose(inventory_unread)
        ),
        118,
    )
    assert "used by -" in panel_unread
    assert detail.UNREAD_LEGEND in panel_unread

    resolved = _disk(
        "sde",
        DiskUsage(uses=(DiskUse(kind=UseKind.LVM, name="vg0", mounts=("/var", "/srv")),)),
    )
    inventory_resolved = Inventory(hostname="demo", disks=(resolved,))
    panel_resolved = _drawn(
        detail.render_detail(
            detail.disk_detail(resolved, inventory_resolved, thresholds=DEFAULT_THRESHOLDS),
            diagnose(inventory_resolved),
        ),
        118,
    )
    assert "lvm:vg0->/var,/srv" in _stripped(panel_resolved)


#: The capture sections the usage reader writes, one per platform. A capture
#: holding neither predates that reader, so its disks' usage is undecidable.
_USAGE_SECTIONS = ("volumes", "mounts")


def _took_a_usage_reading(host: str) -> bool:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return any(payload.get(section) is not None for section in _USAGE_SECTIONS)


@pytest.mark.os_agnostic
def test_a_committed_fixture_reads_not_read_never_not_mounted() -> None:
    """A capture taken before the usage reader carries ``None`` for every disk's usage.

    A capture without the new fields must render the unread dash, never the
    real-reading word - ``not mounted`` would claim a reading these fixtures
    never took. Held over the captures that carry no usage section, and at
    least one must remain, or the check inspects nothing.
    """
    pre_usage = [path.stem for path in sorted(FIXTURES.glob("*.json")) if not _took_a_usage_reading(path.stem)]
    assert pre_usage, "no committed capture predates the usage reader, so this check reads nothing"
    for host in pre_usage:
        table = tables.render_disks(_machine(host), (), width=250)
        printed = _drawn(table, 250)
        assert "not mounted" not in printed, f"{host}: a pre-usage fixture claimed a reading it never took"


@pytest.mark.os_agnostic
def test_a_capture_that_read_its_volumes_draws_what_each_disk_is_used_for() -> None:
    """The other side: a capture that took the reading draws it, not the unread dash.

    windows-chipset-switch read its volumes: the system drive holds C: and is
    the boot disk, the SATA drive holds D: and X:, and a mounted image file
    holds no volume at all, which is the real reading ``not mounted``.
    """
    host = "windows-chipset-switch"
    assert _took_a_usage_reading(host)
    machine = _machine(host)
    usage = {disk.node: theme.format_usage(disk.usage)[0] for disk in (*machine.disks, *machine.virtual_disks)}

    assert usage == {"PhysicalDrive0": "D:, X:", "PhysicalDrive1": "boot C:", "PhysicalDrive2": "not mounted"}, usage
