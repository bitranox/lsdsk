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
CAPTURES = (
    "linux-sas-hba",
    "linux-nvme-board",
    "linux-minimal",
    "linux-usb-ehci",
    "windows-ahci",
    "windows-usb-uas",
)

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


#: A USB rate written any way but ``UsbSpeed.figure``'s: a bare rate with a bit
#: unit, the marketing ``USB 3.2 Gen 2`` family, or the figure with a blank in it.
USB_OTHER_SPELLING = re.compile(
    r"\b\d+(\.\d+)?\s?(Gbps|Mbps|Gb/s|Mb/s|Gbit/s|Mbit/s)\b|\bUSB ?\d\.\d\b|\bGen ?\dx\d USB\b|\bUSB \d+(\.\d+)?[MG]\b"
)

#: The spelling every USB figure takes.
USB_FIGURE = re.compile(r"\bUSB(1\.5M|12M|480M|5G|10G|20G)\b")


def _usb_views(*, usb: bool) -> list[tuple[str, str]]:
    """Every view of a machine holding one USB disk (or the same disk as plain SATA, for the control)."""
    from lsdsk.domain.enums import BusType, UsbLaneRate
    from lsdsk.domain.models import Disk, InterfaceLink, Inventory, UsbLink, UsbSpeed

    link = UsbLink(
        running=UsbSpeed(lane_rate=UsbLaneRate.HIGH),
        device_max=UsbSpeed(lane_rate=UsbLaneRate.GEN2),
        port_max=UsbSpeed(lane_rate=UsbLaneRate.HIGH),
        behind_hub=False,
        on_usb2_twin=False,
    )
    disk = Disk(
        node="sdb",
        path="/dev/sdb",
        model="Portable SSD",
        bus=BusType.USB if usb else BusType.SATA,
        link=InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0),
        usb=link if usb else None,
    )
    machine = Inventory(hostname="usb-host", disks=(disk,))
    findings = diagnose(machine)
    finding_text = " ".join(f"{one.title} {one.detail} {one.action or ''}" for one in findings)
    return [
        ("the printed page", _text(render_full(machine, findings, width=200))),
        ("the disk-and-controller tree", _text(report.render_controller_disks(machine, findings, width=200))),
        ("the findings", finding_text),
        *_panels(machine, findings),
    ]


@pytest.mark.os_agnostic
def test_every_view_spells_a_usb_rate_one_way() -> None:
    """The USB figure is written by one property, so no view may grow a second spelling."""
    views = _usb_views(usb=True)
    for label, text in views:
        found = sorted({match.group(0) for match in USB_OTHER_SPELLING.finditer(text)})
        assert not found, f"{label}: writes {found}, which is not how a USB rate is spelled"
    drawn = {match.group(0) for _, text in views for match in USB_FIGURE.finditer(text)}
    assert {"USB10G", "USB480M"} <= drawn, f"the views drew {sorted(drawn)}, so the rule above checked too little"


@pytest.mark.os_agnostic
def test_a_machine_with_no_usb_disk_draws_no_usb_figure() -> None:
    """The control: the figures above come from the USB link, not from the page's furniture."""
    drawn = {match.group(0) for _, text in _usb_views(usb=False) for match in USB_FIGURE.finditer(text)}
    assert not drawn


#: A USB figure carrying what it is worth, the closed form ADR 0002 fixes for
#: every place a figure is drawn: ``USB10G (1.21 GB/s)``.
USB_FIGURE_WITH_BANDWIDTH = re.compile(r"\bUSB(1\.5M|12M|480M|5G|10G|20G) \(\d+\.\d\d GB/s\)")


def _every_usb_speed() -> list[Any]:
    from lsdsk.domain.enums import UsbLaneRate
    from lsdsk.domain.models import UsbSpeed

    speeds = [UsbSpeed(lane_rate=rate) for rate in UsbLaneRate]
    return speeds + [UsbSpeed(lane_rate=rate, lanes=2) for rate in (UsbLaneRate.GEN1, UsbLaneRate.GEN2)]


@pytest.mark.os_agnostic
def test_a_usb_finding_spells_a_rate_exactly_as_a_column_does() -> None:
    """The USB sentence form is written in the domain, the column form in the render layer: tie them."""
    from lsdsk.domain.pcie_text import format_usb_sentence

    for speed in _every_usb_speed():
        column = report.usb_speed_text(speed, bandwidth=True)
        assert format_usb_sentence(speed) == column, f"{speed}: the sentence and the column disagree"


@pytest.mark.os_agnostic
def test_a_usb_finding_names_the_figures_the_disk_table_above_it_draws() -> None:
    """On the rendered page: every USB figure a finding writes is one the disk table draws, bandwidth and all.

    The disk runs two Gen 1 lanes on a link both ends of which do one Gen 2
    lane. Both read ``USB10G`` without their bandwidth, so a sentence carrying
    the bare figure said the link ran at what both ends support.
    """
    from lsdsk.adapters.render import tables
    from lsdsk.domain.enums import BusType, UsbLaneRate
    from lsdsk.domain.models import Disk, InterfaceLink, Inventory, UsbLink, UsbSpeed

    gen2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)
    link = UsbLink(
        running=UsbSpeed(lane_rate=UsbLaneRate.GEN1, lanes=2), device_max=gen2, port_max=gen2, on_usb2_twin=False
    )
    disk = Disk(
        node="sdb",
        path="/dev/sdb",
        model="Portable SSD",
        bus=BusType.USB,
        link=InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0),
        usb=link,
    )
    machine = Inventory(hostname="usb-host", disks=(disk,))
    findings = diagnose(machine)

    table = _text(tables.render_disks(machine, findings, width=200))
    sentences = _text(report.render_findings(findings), width=400)
    drawn = {match.group(0) for match in USB_FIGURE_WITH_BANDWIDTH.finditer(table)}
    written = {match.group(0) for match in USB_FIGURE_WITH_BANDWIDTH.finditer(sentences)}

    assert {"USB10G (1.00 GB/s)", "USB10G (1.21 GB/s)"} <= drawn, f"the table drew {sorted(drawn)}"
    assert written == {"USB10G (1.00 GB/s)", "USB10G (1.21 GB/s)"}, f"the findings wrote {sorted(written)}"
    bare = re.findall(r"\bUSB10G\b(?! \()", sentences)
    assert not bare, f"a finding writes a USB figure without its bandwidth: {sentences}"
