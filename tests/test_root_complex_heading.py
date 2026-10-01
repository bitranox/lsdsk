"""Every root complex is drawn as its own heading, with its devices one level below.

The board line names the root complexes, and the tree drew their devices as one
column straight under it. On a machine with two, the first one's last device
closed the column with its last-turn glyph and the second one's devices carried
straight on in it with a branch glyph - a column that had ended, starting again
with nothing above it to start from. Three of the committed captures have two
root complexes, so this was the drawing on most Linux machines here, not a
corner of one.

The root bus is a node of the tree like any other, so it draws its own rules:
a heading on every machine, a single root complex included, because a drawing
whose shape depends on how many there are is two drawings (user decision,
2026-10-02, over a heading only when there are two and over one continuous
list).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.hw.fabric import UNPLACED_ROOT
from lsdsk.adapters.render.tree import Fabric, FabricView, fabric_lines
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import TreeDensity
from lsdsk.domain.models import PcieLink, PciNode

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))
WIDTH = 160
MARKER = 3
BRANCHES = frozenset("├└")
LAST = "└"
PIPE = "│"
HEADING = re.compile(r"root complex (\S+)$")


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _spines(inventory: Inventory, density: TreeDensity) -> list[str]:
    """The rule region of every line the section draws below the board line."""
    view = FabricView(density=density)
    spine = Fabric.of(inventory, WIDTH, view).spine
    lines = fabric_lines(inventory, diagnose(inventory), WIDTH, view)
    board = next(index for index, line in enumerate(lines) if line.subject is inventory)
    return [line.text.plain[MARKER : MARKER + spine] for line in lines[board + 1 :]]


def _reopened_columns(spines: list[str]) -> list[str]:
    """Every place a rule is drawn in a column a last-turn glyph already ended.

    A column ends at a last-turn and stays ended until a branch in a SHALLOWER
    column starts a new subtree, which is the only thing that may open it again.
    """
    closed: set[int] = set()
    found: list[str] = []
    for row, spine in enumerate(spines):
        for column, glyph in enumerate(spine):
            if (glyph in BRANCHES or glyph == PIPE) and column in closed:
                found.append(f"row {row} column {column}: {spine!r}")
            if glyph in BRANCHES:
                closed = {done for done in closed if done < column}
                if glyph == LAST:
                    closed.add(column)
    return found


@pytest.mark.os_agnostic
@pytest.mark.parametrize("density", list(TreeDensity), ids=str)
@pytest.mark.parametrize("host", CAPTURES)
def test_no_rule_continues_in_a_column_that_already_ended(host: str, density: TreeDensity) -> None:
    reopened = _reopened_columns(_spines(_machine(host), density))

    assert not reopened, f"{host} {density.value}: {reopened[:3]}"


@pytest.mark.os_agnostic
def test_the_column_check_fires_on_a_column_drawn_again_after_it_ended() -> None:
    """The control: the check above must be able to fail."""
    ended_then_drawn = ["└─── ", "├─── "]
    reopened_by_a_shallower_branch = ["│ └─ ", "└─── ", "  ├─ "]

    assert _reopened_columns(ended_then_drawn)
    assert not _reopened_columns(reopened_by_a_shallower_branch)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("density", list(TreeDensity), ids=str)
@pytest.mark.parametrize("host", CAPTURES)
def test_every_root_complex_drawn_has_one_heading(host: str, density: TreeDensity) -> None:
    """On every machine, a single root complex included."""
    inventory = _machine(host)
    view = FabricView(density=density)
    lines = fabric_lines(inventory, diagnose(inventory), WIDTH, view)
    headings = [match.group(1) for line in lines if (match := HEADING.search(line.text.plain.rstrip()))]
    under_a_root = {node.parent_address for node, _level in Fabric.of(inventory, WIDTH, view).drawn()}
    drawn_roots = sorted(node.address for node in inventory.pci_tree if node.is_root and node.address in under_a_root)

    assert sorted(headings) == drawn_roots
    assert drawn_roots, f"{host}: the capture drew no root complex at all"


@pytest.mark.os_agnostic
def test_a_root_complex_heading_names_no_device_to_select() -> None:
    """A heading describes the section; the detail panel has nothing to say of a synthetic bus."""
    inventory = _machine("linux-usb-ehci")
    lines = fabric_lines(inventory, diagnose(inventory), WIDTH, FabricView(density=TreeDensity.FULL))
    headings = [line for line in lines if HEADING.search(line.text.plain.rstrip())]

    assert len(headings) == 2
    assert all(line.subject is None for line in headings)


@pytest.mark.os_agnostic
def test_devices_with_no_bus_address_are_headed_as_unplaced_rather_than_as_a_root_complex() -> None:
    nodes = (
        PciNode(address=UNPLACED_ROOT, name="unplaced"),
        PciNode(address=r"PCI\VEN_1AF4&DEV_1000\3&13c0b0c5&0&50", name="virtio", parent_address=UNPLACED_ROOT),
    )
    fabric = Fabric(nodes, WIDTH, FabricView(density=TreeDensity.FULL), drives_on=())
    plain = [fabric.row(node, {}).plain.rstrip() for node, _level in fabric.drawn()]

    assert not any(HEADING.search(line) for line in plain)
    assert any(line.endswith("no bus address") for line in plain), plain


@pytest.mark.os_agnostic
def test_a_root_complex_heading_adds_no_symbol_to_the_hop_legend() -> None:
    """The heading carries no link, so it cannot be what the legend explains."""
    read = PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=8.0, max_width=4)
    nodes = (
        PciNode(address="0000:00", name="root bus"),
        PciNode(
            address="0000:00:01.0",
            name="port",
            parent_address="0000:00",
            class_code=0x060400,
            link=read,
            pcie_capability_present=True,
        ),
    )
    fabric = Fabric(nodes, WIDTH, FabricView(density=TreeDensity.FULL), drives_on=())

    assert fabric.hop_legend() == ""
