"""One spelling for a PCIe generation, in every view of one machine.

The slots table wrote the marketing form (``Gen3x4``) while the detail panel one
keypress away wrote the decimal one (``3.0x4``) for the SAME port, and the
topology tree, the controllers table and a finding's own sentence wrote the
decimal one too. A reader comparing two views of one port had to work out that
two different strings name one link, and a reader searching their own output for
a figure a document quotes found it in one view and not in another.

The marketing form wins, because it is what is printed on the box the part came
in. That is a judgement about what a reader recognises, not a fact about the
hardware, and it was settled by the user rather than derived here.

It is held on the RENDERED page rather than on the formatters, because a
formatter nobody draws through proves nothing about what a reader sees.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.render import detail, report
from lsdsk.adapters.render.full import render_full
from lsdsk.domain.diagnostics import diagnose

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lsdsk.domain.models import Finding, Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = ("linux-sas-hba", "linux-nvme-board", "linux-minimal", "windows-ahci")

#: The spelling this tool does NOT write, with or without the blank that used to
#: sit inside it. Both forms are caught, because a figure written ``3.0 x4`` is
#: the same drift wearing a space.
DECIMAL = re.compile(r"\b\d+\.0\s?x\d+\b")

#: The spelling it does write. Required to appear, so a page that happened to
#: draw no link at all cannot satisfy the rule above by being empty.
MARKETING = re.compile(r"\bGen\d+x\d+\b")


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _text(renderable: object, width: int = 200) -> str:
    console = Console(width=width, record=True, file=io.StringIO(), legacy_windows=False, no_color=True)
    console.print(renderable)
    return console.export_text()


def _panels(machine: Inventory, findings: Sequence[Finding]) -> list[tuple[str, str]]:
    """Every detail panel a reader can open, with a label naming which it is."""
    records = [("machine", detail.machine_detail(machine))]
    records += [(f"disk {disk.path}", detail.disk_detail(disk, machine)) for disk in machine.disks]
    records += [(f"controller {one.address}", detail.controller_detail(one, machine)) for one in machine.controllers]
    records += [(f"node {node.address}", detail.node_detail(node)) for node in machine.pci_tree]
    records += [(f"slot {slot.address}", detail.slot_detail(slot, machine)) for slot in machine.slots]
    return [(label, _text(detail.render_detail(record, findings))) for label, record in records]


def _views(host: str) -> list[tuple[str, str]]:
    """The whole printed page, plus every panel, as text a reader would read.

    The old disk-and-controller tree is a view too. It is what a capture
    carrying no PCI reading gets instead of the fabric, so ``render_full`` over
    the committed captures never reaches it - every one of them has PCI data -
    and it writes its own controller line with its own link figure. Rendering it
    directly from the same machines is what puts it under this rule.
    """
    machine = _machine(host)
    findings = diagnose(machine)
    return [
        ("the printed page", _text(render_full(machine, findings, width=200))),
        ("the disk-and-controller tree", _text(report.render_controller_disks(machine, findings, width=200))),
        *_panels(machine, findings),
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_no_view_spells_a_generation_the_decimal_way(host: str) -> None:
    """One port, one spelling, wherever a reader meets it."""
    for label, text in _views(host):
        found = sorted({match.group(0) for match in DECIMAL.finditer(text)})
        assert not found, f"{host}, {label}: writes {found}, which no other view spells that way"


@pytest.mark.os_agnostic
def test_the_sweep_reads_pages_that_carry_link_figures_at_all() -> None:
    """The control.

    Without it the rule above passes on a page that drew no link, which is what
    an exception swallowed inside a renderer would leave behind.
    """
    seen = {host: any(MARKETING.search(text) for _, text in _views(host)) for host in CAPTURES}
    carrying = [host for host, found in seen.items() if found]
    assert carrying, "no capture drew a single link figure, so the spelling rule checked nothing"


@pytest.mark.os_agnostic
def test_a_finding_spells_a_link_exactly_as_a_column_does() -> None:
    """The two spellings are written in two layers, so tie them together.

    The domain cannot reach the render layer's formatter, so ``format_pcie_sentence``
    writes the figure a second time. Catching a DECIMAL figure in a sentence is
    not enough to keep them together: a third spelling invented in either place
    would pass that check and still give a reader two answers.
    """
    from lsdsk.adapters.render import theme
    from lsdsk.domain.diagnostics import format_pcie_sentence

    for gtps, generation in ((2.5, 1), (5.0, 2), (8.0, 3), (16.0, 4), (32.0, 5), (64.0, 6)):
        for width in (1, 2, 4, 8, 16):
            column = theme.format_pcie_generation(gtps, width)
            assert format_pcie_sentence(gtps, width) == f"PCIe {column}", (
                f"generation {generation} x{width}: the sentence and the column disagree"
            )


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_a_finding_spells_a_generation_the_way_the_table_above_it_does(host: str) -> None:
    """A finding's own sentence is a view too.

    It sits directly under the table it is about, so a sentence spelling the
    link differently describes one link in two hands.
    """
    machine = _machine(host)
    for finding in diagnose(machine):
        text = f"{finding.title} {finding.detail} {finding.action or ''}"
        found = sorted({match.group(0) for match in DECIMAL.finditer(text)})
        assert not found, f"{host}: a finding writes {found}: {text}"
