"""A serial link running at the drive's own maximum proves the port can do at least that.

The port end of a SATA link is often unread - on Windows it is never read, and on
Linux it needs a register some hosts refuse - so the ``port`` cell used to be a
dash even where the running link had already answered half the question: a link
that trained at 6 Gb/s sits in a port that supports 6 Gb/s. That half is drawn as
``>= 6G``, from one value on the link model, and is explained wherever it is drawn.

The other half stays unknown on purpose. ``port_max_gbps`` and ``achievable_gbps``
keep requiring both ends, because rule 3 says an unread end is never a capable
one, so no finding may move. The last test holds that over every committed capture.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import detail, report, tables, theme
from lsdsk.adapters.render.tree import render_fabric
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.typed_table import rows_of
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import BusType, ControllerKind, DiskKind
from lsdsk.domain.models import Controller, Disk, InterfaceLink, Inventory
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

#: The proven lower bound as every view draws it, bandwidth included.
FLOOR_CELL = f"{theme.AT_LEAST} 6G (0.60 GB/s)"


def _disk(node: str, link: InterfaceLink) -> Disk:
    return Disk(
        node=node,
        path=f"/dev/{node}",
        model=f"Drive {node}",
        kind=DiskKind.SSD,
        bus=BusType.SATA,
        controller_address="0000:03:00.0",
        link=link,
    )


#: The subject: running at the drive's maximum, port unread.
AT_DRIVE_MAX = _disk("sda", InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0))
#: Control: running BELOW the drive's maximum. The port may be what holds it
#: back, so the running rate proves nothing beyond itself and nothing is drawn.
BELOW_DRIVE_MAX = _disk("sdb", InterfaceLink(negotiated_gbps=3.0, drive_max_gbps=6.0))
#: Control: both ends read. The port's own figure is drawn, never a bound.
BOTH_READ = _disk("sdc", InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0, port_max_gbps=6.0))


def _machine(*disks: Disk) -> Inventory:
    controller = Controller(address="0000:03:00.0", name="AHCI controller", kind=ControllerKind.AHCI)
    return Inventory(hostname="example", controllers=(controller,), disks=disks)


def _drawn(renderable: object, width: int = 200) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _capture(name: str) -> Inventory:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return build_from(payload)


@pytest.mark.os_agnostic
def test_the_lower_bound_is_the_running_rate_only_where_it_equals_the_drive_maximum() -> None:
    assert AT_DRIVE_MAX.link.port_floor_gbps == 6.0
    assert BELOW_DRIVE_MAX.link.port_floor_gbps is None
    assert BOTH_READ.link.port_floor_gbps is None
    assert InterfaceLink(negotiated_gbps=6.0).port_floor_gbps is None, "no drive maximum, nothing to compare with"
    # The two-ended figures are untouched: rule 3 still holds.
    assert AT_DRIVE_MAX.link.port_max_gbps is None
    assert AT_DRIVE_MAX.link.achievable_gbps is None


@pytest.mark.os_agnostic
def test_the_disk_table_draws_the_bound_and_explains_it() -> None:
    machine = _machine(AT_DRIVE_MAX, BOTH_READ)
    drawn = _drawn(tables.render_disks(machine, diagnose(machine), width=200))
    assert FLOOR_CELL in drawn
    assert theme.AT_LEAST_LEGEND in drawn, "the bound was drawn and never explained"


@pytest.mark.os_agnostic
def test_a_table_with_no_bound_on_it_carries_no_legend_for_one() -> None:
    machine = _machine(BELOW_DRIVE_MAX, BOTH_READ)
    drawn = _drawn(tables.render_disks(machine, diagnose(machine), width=200))
    assert theme.AT_LEAST not in drawn
    assert theme.AT_LEAST_LEGEND not in drawn


@pytest.mark.os_agnostic
def test_the_detail_panel_draws_the_bound_and_the_proven_pairing() -> None:
    """``port`` is a bound; ``achievable`` is exact, because min(drive, port >= drive) is the drive."""
    machine = _machine(AT_DRIVE_MAX, BELOW_DRIVE_MAX)
    findings = diagnose(machine)

    subject = detail.disk_detail(AT_DRIVE_MAX, machine, thresholds=DEFAULT_THRESHOLDS)
    values = {name: text for group in subject.groups for name, (text, _style) in group.values}
    assert values["port"] == FLOOR_CELL
    assert values["achievable"] == "6G (0.60 GB/s)"
    assert theme.AT_LEAST_LEGEND in _drawn(detail.render_detail(subject, findings))

    control = detail.disk_detail(BELOW_DRIVE_MAX, machine, thresholds=DEFAULT_THRESHOLDS)
    values = {name: text for group in control.groups for name, (text, _style) in group.values}
    assert values["port"] == theme.NOT_READ
    assert values["achievable"] == theme.NOT_READ
    assert theme.AT_LEAST_LEGEND not in _drawn(detail.render_detail(control, findings))


@pytest.mark.os_agnostic
def test_both_trees_explain_the_bound_they_draw() -> None:
    """The old tree from a built machine with no PCI reading; the fabric from a capture that has one."""
    machine = _machine(AT_DRIVE_MAX)
    board = _capture("windows-chipset-switch")
    assert board.pci_tree, "the fabric arm needs a PCI reading, or it draws the old tree a second time"
    for name, renderable in (
        ("controller tree", report.render_tree(machine, diagnose(machine), width=200, thresholds=DEFAULT_THRESHOLDS)),
        ("fabric", render_fabric(board, diagnose(board), width=200, thresholds=DEFAULT_THRESHOLDS)),
    ):
        drawn = _drawn(renderable)
        assert f"{theme.AT_LEAST} 6G" in drawn, f"{name}: the bound is not drawn"
        assert theme.AT_LEAST_LEGEND in drawn, f"{name}: the bound was drawn and never explained"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_interactive_disk_page_explains_the_bound() -> None:
    machine = _machine(AT_DRIVE_MAX, BOTH_READ)
    app = LsdskApp(machine)
    async with app.run_test(size=(200, 45)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table = rows_of(app.query_one("#disk-table"))
        cells = [str(cell) for row in range(table.row_count) for cell in table.get_row_at(row)]
        assert FLOOR_CELL in cells
        legend = app.query_one("#disk-legend")
        assert legend.display
        assert theme.AT_LEAST_LEGEND in str(legend.render())


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", sorted(path.stem for path in FIXTURES.glob("*.json")))
def test_no_finding_depends_on_the_bound(name: str) -> None:
    """The bound is drawn, never judged: removing it from every link changes no finding.

    The bound is injected away through the data rather than by patching the
    rules, so a rule that ever starts reading it - and so starts treating a
    one-ended reading as a capable port - turns this red.
    """
    machine = _capture(name)
    without = machine.with_changes(
        disks=tuple(disk.with_changes(link=_NoFloor(**disk.link.model_dump())) for disk in machine.disks)
    )
    bounded = any(d.link.port_floor_gbps is not None for d in machine.disks)
    assert bounded is (name not in _NO_BOUND_CAPTURES), "the list of captures with no bound has gone stale"
    assert diagnose(without) == diagnose(machine)


class _NoFloor(InterfaceLink, frozen=True):
    """A link that never offers a lower bound, everything else equal."""

    @property
    def port_floor_gbps(self) -> float | None:
        return None


#: The captures with no drive in the bounded position, so the parametrized
#: control above is not vacuous for the others.
_NO_BOUND_CAPTURES = frozenset(
    {
        "linux-btrfs-raid1-zoned-xfs",
        "linux-ext4-xfs-filesystems",
        "linux-minimal",
        "linux-zfs-filesystems",
        "windows-ahci",
    }
)


@pytest.mark.os_agnostic
def test_no_rule_reads_the_bound() -> None:
    """Only the model that derives the bound may name it inside the domain.

    The capture test above cannot carry this alone: every bounded link in the
    committed captures is a healthy one, where the floor equals the drive's own
    maximum and a rule asking "could it be faster" answers no either way. A
    rule that read the bound would pass there and start treating a one-ended
    reading as a capable port on the first machine where that matters.
    """
    domain = Path(__file__).parent.parent / "src" / "lsdsk" / "domain"
    readers = sorted(
        path.name
        for path in domain.rglob("*.py")
        if path.name != "models.py" and "port_floor_gbps" in path.read_text(encoding="utf-8")
    )
    assert any(domain.rglob("diagnostics.py")), "the domain was not found, so the scan read nothing"
    assert readers == [], f"a domain module reads the drawing-only bound: {readers}"
