"""Every list an inventory carries comes out in one order on both platforms.

No view sorts rows: the tables, the TUI pages, ``--format json`` and the
disk report all draw ``inventory.controllers``, ``inventory.slots`` and
``inventory.disks`` as the builder produced them. So the builder is the one
place the order is decided, and it was decided differently per platform - the
Windows builder walked PnP instance identifiers, which is vendor and device
order, and listed a physical workstation's controllers as 02:00.0, 00:17.0,
00:0e.0 and its drives as PhysicalDrive1, 0, 2.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw import snapshot
from lsdsk.adapters.hw.fabric import NodeSource, assemble
from lsdsk.domain.disk_name import disk_name_order
from lsdsk.domain.enums import PciPortKind
from lsdsk.domain.models import PcieLink
from lsdsk.domain.pci_address import pci_address_order

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


def _load(host: str) -> dict[str, Any]:
    with (FIXTURE_DIR / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


@pytest.mark.os_agnostic
def test_the_captures_include_one_the_old_order_got_wrong() -> None:
    # Without it every test below could pass on a builder that never sorts.
    assert "windows-usb-uas" in CAPTURES


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_controllers_come_out_in_address_order(host: str) -> None:
    addresses = [controller.address for controller in snapshot.build_from(_load(host)).controllers]
    assert addresses == sorted(addresses, key=pci_address_order)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_slots_come_out_in_address_order(host: str) -> None:
    addresses = [slot.address for slot in snapshot.build_from(_load(host)).slots]
    assert addresses == sorted(addresses, key=pci_address_order)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_disks_come_out_in_drive_number_order(host: str) -> None:
    inventory = snapshot.build_from(_load(host))
    for disks in (inventory.disks, inventory.virtual_disks):
        nodes = [disk.node for disk in disks]
        assert nodes == sorted(nodes, key=disk_name_order)


@pytest.mark.os_agnostic
def test_a_linux_disk_past_sdz_is_listed_after_it() -> None:
    payload = _load("linux-minimal")
    for name in ("sdz", "sdaa"):
        payload["block"][name] = copy.deepcopy(payload["block"]["sdb"])
    nodes = [disk.node for disk in snapshot.build_from(payload).disks]
    assert nodes[-3:] == ["sdb", "sdz", "sdaa"]


def _source(address: str, parent: str | None = None) -> NodeSource:
    return NodeSource(
        address=address,
        name="device",
        class_code=None,
        vendor=None,
        driver=None,
        link=PcieLink(),
        port_kind=PciPortKind.UNKNOWN,
        connector_present=None,
        physical_slot_number=None,
        parent=parent,
    )


@pytest.mark.os_agnostic
def test_the_fabric_orders_root_buses_and_siblings_by_address() -> None:
    tree = assemble(
        (
            _source("10000:e1:00.0"),
            _source("ffff:00:00.0"),
            _source("0000:00:01.0"),
            *(_source("0000:01:00.0", parent="0000:00:01.0") for _ in range(11)),
        )
    )
    roots = [node.address for node in tree if node.is_root]
    assert roots == ["0000:00", "ffff:00", "10000:e1"]
    port = next(node for node in tree if node.address == "0000:00:01.0")
    assert list(port.children) == ["0000:01:00.0", *(f"0000:01:00.0#{copy}" for copy in range(2, 12))]
    devices = [node.address for node in tree if not node.is_root]
    assert devices == sorted(devices, key=pci_address_order)
