"""Hints for the PCIe links no storage rule grades.

Graded once per link, at the device behind a port that faces downstream, and
never where a storage rule already grades it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import PciPortKind, Severity
from lsdsk.domain.fabric_links import (
    FabricLink,
    carrying_clause,
    diagnose_fabric_link,
    diagnose_fabric_links,
    fabric_links,
)
from lsdsk.domain.models import Controller, Disk, Finding, Inventory, PcieLink, PcieSlot, PciNode

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _machine(host: str) -> Inventory:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _link(running: tuple[float, int], capable: tuple[float, int]) -> PcieLink:
    return PcieLink(
        current_speed_gtps=running[0], current_width=running[1], max_speed_gtps=capable[0], max_width=capable[1]
    )


ROOT = PciNode(address="0000:00", name="root bus")
PORT = PciNode(
    address="0000:00:02.0",
    name="root port",
    class_code=0x060400,
    parent_address="0000:00",
    port_kind=PciPortKind.ROOT,
    link=_link((8.0, 8), (8.0, 8)),
    pcie_capability_present=True,
)


def _card(address: str = "0000:01:00.0", **fields: Any) -> PciNode:
    defaults: dict[str, Any] = {
        "name": "a card",
        "class_code": 0x030000,
        "parent_address": PORT.address,
        "link": _link((8.0, 8), (8.0, 16)),
        "pcie_capability_present": True,
    }
    return PciNode(address=address, **(defaults | fields))


@pytest.mark.os_agnostic
def test_only_root_and_downstream_ports_face_downstream() -> None:
    facing = {kind: PciNode(address="a", name="b", port_kind=kind).faces_downstream for kind in PciPortKind}
    assert facing == {
        PciPortKind.ROOT: True,
        PciPortKind.SWITCH_DOWNSTREAM: True,
        PciPortKind.SWITCH_UPSTREAM: False,
        PciPortKind.UNKNOWN: False,
    }


@pytest.mark.os_agnostic
def test_the_functions_of_one_device_are_one_link_at_its_lowest_function() -> None:
    tree = (ROOT, PORT, _card("0000:01:00.1"), _card("0000:01:00.0"))
    links = fabric_links(Inventory(hostname="h", pci_tree=tree))
    assert [(link.port.address, link.card.address, len(link.functions)) for link in links] == [
        ("0000:00:02.0", "0000:01:00.0", 2)
    ]


@pytest.mark.os_agnostic
def test_a_device_on_a_root_bus_has_no_link_to_grade() -> None:
    assert fabric_links(Inventory(hostname="h", pci_tree=(ROOT, PORT))) == ()


@pytest.mark.os_agnostic
def test_a_device_below_a_port_that_does_not_face_downstream_is_not_paired_with_it() -> None:
    upstream = PORT.with_changes(port_kind=PciPortKind.SWITCH_UPSTREAM)
    assert fabric_links(Inventory(hostname="h", pci_tree=(ROOT, upstream, _card()))) == ()


@pytest.mark.os_agnostic
def test_a_storage_controller_and_a_drive_host_are_left_to_their_own_rules() -> None:
    controller = Controller(address="0000:01:00.0", name="hba")
    host = _card("0000:02:00.0")
    tree = (ROOT, PORT, _card(), host)
    inventory = Inventory(
        hostname="h",
        pci_tree=tree,
        controllers=(controller,),
        disks=(Disk(path="/dev/sdz", node="sdz", model="m", controller_address=host.address),),
    )
    assert fabric_links(inventory) == ()


@pytest.mark.os_agnostic
def test_a_plain_card_behind_a_root_port_is_one_link() -> None:
    """The control for the skip tests above: without a skip, the same card IS graded."""
    links = fabric_links(Inventory(hostname="h", pci_tree=(ROOT, PORT, _card())))
    assert [link.card.address for link in links] == ["0000:01:00.0"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(path.stem for path in FIXTURES.glob("*.json")))
def test_no_committed_capture_grades_a_port_as_the_card_end(host: str) -> None:
    for link in fabric_links(_machine(host)):
        assert not link.card.faces_downstream, f"{host}: {link.card.address}"
        assert link.port.faces_downstream, f"{host}: {link.port.address}"


def _tree(*nodes: PciNode) -> Inventory:
    return Inventory(hostname="h", pci_tree=nodes)


def _below(parent: str, address: str, name: str, class_code: int = 0x030000) -> PciNode:
    return PciNode(address=address, name=name, class_code=class_code, parent_address=parent)


SWITCH = _card(name="switch", class_code=0x060400, port_kind=PciPortKind.SWITCH_UPSTREAM)
LEG_A = _below(SWITCH.address, "0000:02:08.0", "leg", 0x060400)
LEG_B = _below(SWITCH.address, "0000:02:10.0", "leg", 0x060400)


@pytest.mark.os_agnostic
def test_a_plain_card_carries_nothing() -> None:
    assert carrying_clause(FabricLink(port=PORT, functions=(_card(),)), _tree(ROOT, PORT, _card())) == ""


@pytest.mark.os_agnostic
def test_a_switch_names_the_devices_behind_it_grouped_and_counted() -> None:
    tree = (
        ROOT,
        PORT,
        SWITCH,
        LEG_A,
        LEG_B,
        _below(LEG_A.address, "0000:03:00.0", "GPU"),
        _below(LEG_A.address, "0000:03:00.1", "GPU audio", 0x040300),
        _below(LEG_B.address, "0000:04:00.0", "GPU"),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), _tree(*tree)) == ", carrying 2x GPU"


@pytest.mark.os_agnostic
def test_more_than_two_names_end_in_a_count() -> None:
    tree = (
        ROOT,
        PORT,
        SWITCH,
        LEG_A,
        *(_below(LEG_A.address, f"0000:0{i}:00.0", f"device {i}") for i in range(3, 7)),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), _tree(*tree)) == (
        ", carrying device 3, device 4 and 2 more"
    )


@pytest.mark.os_agnostic
def test_every_function_of_a_bridge_card_contributes_what_it_carries() -> None:
    first = _card("0000:01:00.0", name="bridge", class_code=0x060400)
    second = _card("0000:01:00.2", name="bridge", class_code=0x060400)
    tree = (
        ROOT,
        PORT,
        first,
        second,
        _below(first.address, "0000:02:04.0", "NIC"),
        _below(second.address, "0000:03:05.0", "NIC"),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(first, second)), _tree(*tree)) == ", carrying 2x NIC"


LANES = "runs on fewer lanes than both ends support"
CAPPED = "is capped by its slot"

#: (capture, card address, which hint) for every hint the committed captures raise.
EXPECTED: tuple[tuple[str, str, str], ...] = (
    ("linux-minimal", "0000:01:00.0", CAPPED),
    ("linux-nvme-board", "0000:01:00.0", CAPPED),
    ("linux-sas-hba", "0000:01:00.0", CAPPED),
    ("linux-sas-hba-later", "0000:01:00.0", CAPPED),
    ("linux-usb-ehci", "0000:01:00.0", LANES),
    ("linux-usb-ehci", "0000:04:00.0", CAPPED),
)


def _hints(inventory: Inventory) -> list[tuple[str, str]]:
    return [
        (finding.subject, LANES if LANES in finding.title else CAPPED) for finding in diagnose_fabric_links(inventory)
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(path.stem for path in FIXTURES.glob("*.json")))
def test_every_committed_capture_raises_exactly_the_hints_measured_for_it(host: str) -> None:
    expected = [(card, kind) for capture, card, kind in EXPECTED if capture == host]
    assert _hints(_machine(host)) == expected


@pytest.mark.os_agnostic
def test_every_fabric_hint_is_a_hint_and_reaches_the_findings() -> None:
    machine = _machine("linux-usb-ehci")
    ours = [finding for finding in diagnose(machine) if LANES in finding.title or CAPPED in finding.title]
    assert [finding.subject for finding in ours] == ["0000:01:00.0", "0000:04:00.0"]
    assert {finding.severity for finding in ours} == {Severity.HINT}


@pytest.mark.os_agnostic
def test_the_hd_7990_is_sent_to_the_free_x16_slot() -> None:
    capped = next(f for f in diagnose_fabric_links(_machine("linux-usb-ehci")) if f.subject == "0000:04:00.0")
    assert "carrying 2x" in capped.title and "Radeon HD 7990" in capped.title
    assert "PCIe Gen3x16" in capped.detail and "0000:00:02.2" in capped.detail
    assert "which caps it at PCIe Gen3x8 (7.88 GB/s)" in capped.detail
    assert capped.title.endswith("[Radeon HD 7990/8990 OEM], is capped by its slot")
    assert capped.action is not None
    assert capped.action.startswith("Move it to the free slot at 0000:00:03.0 (PCIe Gen3x16")


@pytest.mark.os_agnostic
def test_a_card_short_on_lanes_is_not_told_it_needs_a_faster_port() -> None:
    """The Hawaii card sits in a Gen5 x8 port: faster than the card, and too narrow.

    The figure is a FLOOR: any x16 port of Gen3 or faster runs it in full, so the
    action must not read as though only a Gen3x16 port would do.
    """
    hawaii = next(f for f in diagnose_fabric_links(_machine("linux-nvme-board")) if f.subject == "0000:01:00.0")
    assert hawaii.action == (
        "No free slot on this board would carry more; the card needs a port of at least PCIe Gen3x16 to run in full."
    )
    assert not hawaii.title.endswith(", is capped by its slot")


@pytest.mark.os_agnostic
def test_lanes_lost_names_how_many_did_not_train() -> None:
    lost = next(f for f in diagnose_fabric_links(_machine("linux-usb-ehci")) if f.subject == "0000:01:00.0")
    assert "Running PCIe Gen1x4 (1.00 GB/s)" in lost.detail
    assert "both support PCIe Gen1x8 (2.00 GB/s): 4 of 8 lanes did not train" in lost.detail


def _graded(card: PciNode, *, slots: tuple[PcieSlot, ...] = ()) -> list[Finding]:
    inventory = Inventory(hostname="h", pci_tree=(ROOT, PORT, card), slots=slots)
    (link,) = fabric_links(inventory)
    return diagnose_fabric_link(link, inventory)


@pytest.mark.os_agnostic
def test_a_speed_only_shortfall_raises_nothing() -> None:
    idle = _card(link=_link((2.5, 8), (8.0, 8)))
    assert _graded(idle) == []


@pytest.mark.os_agnostic
def test_a_link_that_lost_lanes_is_not_also_called_capped() -> None:
    both = _card(link=_link((8.0, 4), (8.0, 16)))
    titles = [finding.title for finding in _graded(both)]
    assert len(titles) == 1 and LANES in titles[0]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("unread", ["card", "port"])
def test_an_unread_end_grades_nothing(unread: str) -> None:
    card = _card(link=PcieLink(current_speed_gtps=8.0, current_width=8)) if unread == "card" else _card()
    port = PORT.with_changes(link=PcieLink(current_speed_gtps=8.0, current_width=8)) if unread == "port" else PORT
    inventory = Inventory(hostname="h", pci_tree=(ROOT, port, card))
    assert diagnose_fabric_links(inventory) == []


@pytest.mark.os_agnostic
def test_a_link_that_never_trained_is_left_to_the_none_marker() -> None:
    assert _graded(_card(link=_link((2.5, 0), (8.0, 16)))) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("connector", "advice"),
    [
        (True, "Move it to the free slot at 0000:00:03.0 (PCIe Gen3x16, slot 4)."),
        (False, "No free slot on this board would carry more"),
        (None, "Whether a free slot would carry more was not readable"),
    ],
    ids=["free-slot", "no-free-slot", "connector-unread"],
)
def test_a_capped_card_is_told_where_it_could_go_only_on_a_read_connector(connector: bool | None, advice: str) -> None:
    wide = PcieSlot(
        address="0000:00:03.0",
        link=PcieLink(max_speed_gtps=8.0, max_width=16),
        connector_present=connector,
        physical_slot_number=4,
    )
    here = PcieSlot(address=PORT.address, link=PORT.link, occupied=True, connector_present=connector)
    capped = _graded(_card(), slots=(here, wide))
    assert len(capped) == 1 and CAPPED in capped[0].title
    assert capped[0].action is not None and capped[0].action.startswith(advice)


def _capped_with(*free: PcieSlot) -> str:
    """The action of the capped hint on a board whose own port's connector WAS read."""
    here = PcieSlot(address=PORT.address, link=PORT.link, occupied=True, connector_present=True)
    capped = _graded(_card(), slots=(here, *free))
    assert len(capped) == 1 and CAPPED in capped[0].title
    assert capped[0].action is not None
    return capped[0].action


def _free_port(*, connector: bool | None, width: int = 16) -> PcieSlot:
    return PcieSlot(
        address="0000:00:03.0", link=PcieLink(max_speed_gtps=8.0, max_width=width), connector_present=connector
    )


@pytest.mark.os_agnostic
def test_a_faster_free_port_whose_connector_was_not_read_is_not_called_absent() -> None:
    """One read connector elsewhere on the board says nothing about THIS port.

    The search used to ask whether ANY connector on the board was read, so one
    read connector turned an unread, faster free port into "No free slot on
    this board would carry more" - an unread value promoted to a finding.
    """
    action = _capped_with(_free_port(connector=None))
    assert action.startswith("Whether a free slot would carry more was not readable"), action
    assert "0000:00:03.0" in action and "PCIe Gen3x16" in action


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "free",
    [_free_port(connector=False), _free_port(connector=None, width=8)],
    ids=["read-as-no-connector", "unread-but-no-faster"],
)
def test_no_free_slot_is_said_only_where_no_unread_port_could_help(free: PcieSlot) -> None:
    """The controls: a port READ as no connector, or an unread one no faster than the seat, changes nothing."""
    assert _capped_with(free).startswith("No free slot on this board would carry more")


@pytest.mark.os_agnostic
def test_a_board_with_no_connector_read_and_no_faster_free_port_says_no_free_slot() -> None:
    """An unread connector matters only on a port that would carry more; elsewhere it decides nothing."""
    here = PcieSlot(address=PORT.address, link=PORT.link, occupied=True)
    capped = _graded(_card(), slots=(here, _free_port(connector=None, width=8)))
    assert capped[0].action is not None
    assert capped[0].action.startswith("No free slot on this board would carry more")


def _ari(parent: str, bus: str, name: str, *, functions: int = 16, **fields: Any) -> tuple[PciNode, ...]:
    """One ARI device: more than eight functions, so its addresses run on into device 01."""
    return tuple(
        PciNode(address=f"{bus}:{index // 8:02x}.{index % 8}", name=name, parent_address=parent, **fields)
        for index in range(functions)
    )


@pytest.mark.os_agnostic
def test_an_ari_device_behind_one_port_is_one_link() -> None:
    """A PCIe port carries ONE device, however its function numbers spill into device 01.

    Keyed by port and bus:device, an ARI card with sixteen functions read as two
    devices and raised the same hint twice.
    """
    lost = _link((8.0, 4), (8.0, 8))
    card = _ari(PORT.address, "0000:01", "ari nic", class_code=0x020000, link=lost, pcie_capability_present=True)
    inventory = Inventory(hostname="h", pci_tree=(ROOT, PORT, *card))
    links = fabric_links(inventory)
    assert [(link.card.address, len(link.functions)) for link in links] == [("0000:01:00.0", 16)]
    assert [finding.subject for finding in diagnose_fabric_links(inventory)] == ["0000:01:00.0"]


@pytest.mark.os_agnostic
def test_an_ari_device_behind_a_switch_port_is_counted_once() -> None:
    leg = LEG_A.with_changes(port_kind=PciPortKind.SWITCH_DOWNSTREAM)
    tree = _tree(ROOT, PORT, SWITCH, leg, *_ari(leg.address, "0000:03", "ari nic", class_code=0x020000))
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), tree) == ", carrying ari nic"


@pytest.mark.os_agnostic
def test_two_devices_on_a_conventional_bus_are_still_two() -> None:
    """The control: below a bridge that is not a PCIe port, device numbers DO name separate devices."""
    tree = _tree(
        ROOT,
        PORT,
        SWITCH,
        LEG_A,
        _below(LEG_A.address, "0000:03:00.0", "NIC"),
        _below(LEG_A.address, "0000:03:01.0", "NIC"),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), tree) == ", carrying 2x NIC"
