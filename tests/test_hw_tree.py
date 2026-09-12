"""Tree assembly over captures taken from real machines.

The fabric is the shared half of the platform mapping, so like the builders it
is tested through the production path: a capture recorded by the real reader
goes through the real builder. The Linux mapping is thereby tested on Windows
and the Windows mapping on Linux; the hand-built cases beside them cover what
no committed fixture carries, a parent cycle and a parent outside the capture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw import snapshot
from lsdsk.adapters.hw.fabric import NodeSource, assemble, port_kind_of
from lsdsk.domain.enums import CliCommand, PciPortKind
from lsdsk.domain.models import PcieLink

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"
ALL_HOSTS = ("linux-sas-hba", "linux-sas-hba-later", "linux-minimal", "linux-nvme-board", "windows-ahci")
LINUX_HOSTS = [host for host in ALL_HOSTS if host.startswith("linux-")]


def load(host: str) -> dict[str, Any]:
    """Load one captured machine."""
    with (FIXTURE_DIR / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", ALL_HOSTS)
def test_a_fixture_builds_a_tree_of_devices_plus_its_root_buses(host: str) -> None:
    """Verify node count devices + roots on every committed capture.

    One synthetic root per ROOT BUS, so a tree over n devices with r root
    buses holds n + r nodes, and the tree loses nothing the capture carried.
    """
    capture = snapshot.parse_capture(load(host))
    inventory = snapshot.build_from(capture)
    device_nodes = [node for node in inventory.pci_tree if not node.is_root]
    roots = [node for node in inventory.pci_tree if node.is_root]

    devices = (capture.pci if hasattr(capture, "pci") else {}) or {}
    expected_devices = len(devices)
    assert len(device_nodes) == expected_devices
    assert len(roots) >= 1
    assert len(inventory.pci_tree) == expected_devices + len(roots)


@pytest.mark.os_agnostic
def test_a_linux_machine_with_two_root_complexes_gets_two_roots() -> None:
    """Verify two root buses become two synthetic roots, not one flat list."""
    inventory = snapshot.build_from(load("linux-sas-hba"))
    roots = [node.address for node in inventory.pci_tree if node.is_root]

    assert roots == ["0000:00", "0000:ff"]


@pytest.mark.os_agnostic
def test_a_single_bus_linux_machine_gets_one_root() -> None:
    """Verify one root bus where there is one."""
    inventory = snapshot.build_from(load("linux-nvme-board"))
    roots = [node.address for node in inventory.pci_tree if node.is_root]

    assert roots == ["0000:00"]


@pytest.mark.os_agnostic
def test_a_parentless_device_lands_under_its_own_bus_root() -> None:
    """Verify a device the capture leaves unplaced is still in the tree.

    ``parent_address is None`` is reserved for the synthetic roots alone - the
    whole contract the tree is assembled on - so an unplaced device must carry
    its bus label as its parent rather than borrow the root's sentinel value.
    Every fixture node honours that, so a real capture in hand being clean,
    the negative case is hand-built.
    """
    tree = assemble(
        [
            NodeSource("0000:05:07.0", "orphan", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, None),
            NodeSource(
                "0000:ff:00.0", "other bus", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, None
            ),
        ]
    )

    by_address = {node.address: node for node in tree}
    root, orphan = by_address["0000:05"], by_address["0000:05:07.0"]
    assert root.is_root
    assert orphan.parent_address == "0000:05"
    assert by_address["0000:ff:07.0".replace("07.0", "00.0")].parent_address == "0000:ff"
    # Roots are exactly the non-device addresses; devices always carry a parent.
    assert {node.parent_address for node in tree if node.is_root} == {None}
    assert all(not node.is_root for node in tree if node.address not in {"0000:05", "0000:ff"})


@pytest.mark.os_agnostic
def test_a_parent_cycle_is_broken_with_every_member_kept() -> None:
    """Verify the cycle the fabric doctest covers, built through a builder-shaped source list."""
    tree = assemble(
        [
            NodeSource(
                "0000:02:00.0", "a", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, "0000:02:00.1"
            ),
            NodeSource(
                "0000:02:00.1", "b", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, "0000:02:00.0"
            ),
        ]
    )
    by_address = {node.address: node for node in tree}

    assert by_address["0000:02:00.0"].parent_address == "0000:02:00.1"
    assert by_address["0000:02:00.1"].parent_address == "0000:02"
    assert by_address["0000:02:00.0"].is_root is False


@pytest.mark.os_agnostic
def test_a_parent_outside_the_capture_falls_to_the_bus_root() -> None:
    """Verify a named parent the capture does not carry is treated as unknown.

    A parent absent from the source data cannot be handed a child, but the
    child must not vanish or dangle: it attaches to the synthetic root of its
    own bus, which is also what keeps ``parent_address`` from borrowing the
    root sentinel.
    """
    tree = assemble(
        [
            NodeSource(
                "0000:03:00.0",
                "child",
                None,
                None,
                None,
                PcieLink(),
                PciPortKind.UNKNOWN,
                None,
                None,
                "0000:9f:00.0",  # not among the sources
            )
        ]
    )
    node = next(n for n in tree if n.address == "0000:03:00.0")

    assert node.parent_address == "0000:03"
    assert len(tree) == 2  # the root of 0000:03 plus the one device
    assert next(n for n in tree if n.is_root).address == "0000:03"


@pytest.mark.os_agnostic
def test_a_port_type_maps_to_its_port_kind() -> None:
    """Verify the recovered ``pcie_port_type`` leg, over real fixture values.

    The reader has always written the register; the parse used to discard it.
    """
    assert port_kind_of(4) is PciPortKind.ROOT
    assert port_kind_of(5) is PciPortKind.SWITCH_UPSTREAM
    assert port_kind_of(6) is PciPortKind.SWITCH_DOWNSTREAM
    # An endpoint value such as 0x9 is not a leg of the fabric at all.
    assert port_kind_of(9) is PciPortKind.UNKNOWN
    assert port_kind_of(None) is PciPortKind.UNKNOWN
    assert port_kind_of(0x1F) is PciPortKind.UNKNOWN


@pytest.mark.os_agnostic
def test_a_linux_capture_carries_port_types_in_its_tree() -> None:
    """Verify the fixture that holds a 9 also holds mapped kinds beside it."""
    inventory = snapshot.build_from(load("linux-sas-hba"))
    by_address = {node.address: node for node in inventory.pci_tree}

    # 0000:00:1c.0 is a real root port on the board this capture came from.
    assert by_address["0000:00:1c.0"].port_kind is PciPortKind.ROOT
    kinds = {node.port_kind for node in inventory.pci_tree}
    assert PciPortKind.ROOT in kinds
    assert PciPortKind.UNKNOWN in kinds


@pytest.mark.os_agnostic
def test_a_windows_bridge_carries_its_children_and_no_port_kinds() -> None:
    """Verify the Windows tree keeps the fabric shape the capture itself describes.

    Windows exposes no PCIe capability port type without a kernel driver, so
    EVERY port reads unknown there, and the children a bridge records are where
    the shape comes from.
    """
    inventory = snapshot.build_from(load("windows-ahci"))
    by_address = {node.address: node for node in inventory.pci_tree}

    roots = [node.address for node in inventory.pci_tree if node.is_root]
    assert roots == ["0000:00"]
    bridge = by_address["0000:05:01.0"]
    assert bridge.is_bridge
    assert set(bridge.children) == {"0000:06:03.0", "0000:06:07.0", "0000:06:08.0", "0000:06:12.0"}
    assert bridge.children == tuple(sorted(bridge.children))
    for child in bridge.children:
        assert by_address[child].parent_address == "0000:05:01.0"
    assert all(not node.is_port or node.port_kind is PciPortKind.UNKNOWN for node in inventory.pci_tree)


@pytest.mark.os_agnostic
def test_the_envelope_round_trips_the_tree_through_json() -> None:
    """Verify pci_tree reaches a JSON consumer with its structure intact.

    Built at the envelope boundary rather than with a hand-built tree, because
    the field serialises frozen dataclasses directly and the whole tree must
    survive the trip, roots and parent pointers alike.
    """
    from lsdsk.adapters.cli.commands.scan import build_envelope
    from lsdsk.domain.diagnostics import diagnose

    inventory = snapshot.build_from(load("linux-nvme-board"))
    findings = diagnose(inventory)
    envelope = build_envelope(inventory, findings, CliCommand.TOPOLOGY)

    parsed = (
        build_envelope(inventory, findings, CliCommand.TOPOLOGY).model_validate_json(envelope.model_dump_json()).data
    )
    assert len(parsed.pci_tree) == len(inventory.pci_tree)
    assert [node.address for node in parsed.pci_tree] == [node.address for node in inventory.pci_tree]
    assert [node.parent_address for node in parsed.pci_tree] == [node.parent_address for node in inventory.pci_tree]
    assert [node.children for node in parsed.pci_tree] == [node.children for node in inventory.pci_tree]
    assert parsed.pci_tree[0].is_root
