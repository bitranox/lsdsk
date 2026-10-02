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
from lsdsk.domain.enums import PciPortKind
from lsdsk.domain.fabric_links import FabricLink, carrying_clause, fabric_links
from lsdsk.domain.models import Controller, Disk, Inventory, PcieLink, PciNode

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


def _below(parent: str, address: str, name: str, class_code: int = 0x030000) -> PciNode:
    return PciNode(address=address, name=name, class_code=class_code, parent_address=parent)


SWITCH = _card(name="switch", class_code=0x060400, port_kind=PciPortKind.SWITCH_UPSTREAM)
LEG_A = _below(SWITCH.address, "0000:02:08.0", "leg", 0x060400)
LEG_B = _below(SWITCH.address, "0000:02:10.0", "leg", 0x060400)


@pytest.mark.os_agnostic
def test_a_plain_card_carries_nothing() -> None:
    assert carrying_clause(FabricLink(port=PORT, functions=(_card(),)), (ROOT, PORT, _card())) == ""


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
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), tree) == ", carrying 2x GPU"


@pytest.mark.os_agnostic
def test_more_than_two_names_end_in_a_count() -> None:
    tree = (
        ROOT,
        PORT,
        SWITCH,
        LEG_A,
        *(_below(LEG_A.address, f"0000:0{i}:00.0", f"device {i}") for i in range(3, 7)),
    )
    assert carrying_clause(FabricLink(port=PORT, functions=(SWITCH,)), tree) == (
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
    assert carrying_clause(FabricLink(port=PORT, functions=(first, second)), tree) == ", carrying 2x NIC"
