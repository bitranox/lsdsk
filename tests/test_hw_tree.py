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
from workcount import work_to_run

from lsdsk.adapters.hw import snapshot
from lsdsk.adapters.hw.fabric import UNPLACED_ROOT, NodeSource, assemble, port_kind_of
from lsdsk.domain.enums import CliCommand, PciPortKind
from lsdsk.domain.models import PcieLink
from lsdsk.domain.pci_address import pci_address_order

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"
ALL_HOSTS = (
    "linux-sas-hba",
    "linux-sas-hba-later",
    "linux-minimal",
    "linux-nvme-board",
    "linux-usb-ehci",
    "windows-ahci",
    "windows-usb-uas",
)
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
    assert bridge.children == tuple(sorted(bridge.children, key=pci_address_order))
    for child in bridge.children:
        assert by_address[child].parent_address == "0000:05:01.0"
    assert all(not node.is_port or node.port_kind is PciPortKind.UNKNOWN for node in inventory.pci_tree)


@pytest.mark.os_agnostic
def test_the_envelope_round_trips_the_tree_through_json() -> None:
    """Verify pci_tree reaches a JSON consumer with its structure intact.

    Built at the envelope boundary rather than with a hand-built tree, because
    the field serialises the frozen domain models directly and the whole tree
    must survive the trip, roots and parent pointers alike.
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


# --------------------------------------------------------------------------
# Untrusted input: a capture can carry an address the assembly did not expect
# --------------------------------------------------------------------------


def _source(address: str, parent: str | None = None, name: str = "device") -> NodeSource:
    """One device as a builder hands it over, with everything unread."""
    return NodeSource(
        address=address,
        name=name,
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
def test_a_domain_wider_than_four_digits_keeps_its_children_under_it() -> None:
    """An Intel VMD re-enumerates its drives into domain 0x10000.

    The sysfs path parser matched a FIXED four hex digits with nothing to its
    left, so the parent of `10000:e1:00.0` resolved to `0000:e0:06.0` - the
    last four digits of a domain that is five - which is in no capture. The
    drive then attached to a synthetic root of its own, so a storage tool
    reported a phantom root complex for the very drives it exists to place.
    """
    capture = {
        "schema": 2,
        "platform": "linux",
        "hostname": "example",
        "kernel": "6.1.0",
        "pci": {
            "0000:00:0e.0": {
                "class": "0x010400",
                "path": "/sys/devices/pci0000:00/0000:00:0e.0",
            },
            "10000:e0:06.0": {
                "class": "0x060400",
                "path": "/sys/devices/pci0000:00/0000:00:0e.0/pci10000:e0/10000:e0:06.0",
            },
            "10000:e1:00.0": {
                "class": "0x010802",
                "path": "/sys/devices/pci0000:00/0000:00:0e.0/pci10000:e0/10000:e0:06.0/10000:e1:00.0",
            },
        },
        "block": {},
    }

    tree = {node.address: node for node in snapshot.build_from(capture).pci_tree}

    assert tree["10000:e1:00.0"].parent_address == "10000:e0:06.0", "the drive left its port"
    assert tree["10000:e0:06.0"].parent_address == "0000:00:0e.0", "the VMD port left the device it sits on"
    assert [node.address for node in tree.values() if node.is_root] == ["0000:00"], "a phantom root complex"


@pytest.mark.os_agnostic
def test_a_device_in_a_five_digit_domain_with_no_parent_roots_on_its_own_bus() -> None:
    """A VMD port with no parent in the capture is still an ADDRESS, not an identifier.

    The test above holds the path parser; this one holds the assembly's own
    notion of what an address looks like, which decides where a device the
    capture leaves parentless is rooted. A shape that accepts only a four-digit
    domain reads `10000:e0:06.0` as one of Windows' address-less instance
    identifiers and files it under the `unplaced` root - so the drives behind a
    VMD whose own parent was not captured would be listed as devices with no
    bus at all, merged with whatever else the platform could not place.
    """
    capture = {
        "schema": 2,
        "platform": "linux",
        "hostname": "example",
        "kernel": "6.1.0",
        "pci": {
            "10000:e0:06.0": {
                "class": "0x060400",
                "path": "/sys/devices/pci10000:e0/10000:e0:06.0",
            },
            "10000:e1:00.0": {
                "class": "0x010802",
                "path": "/sys/devices/pci10000:e0/10000:e0:06.0/10000:e1:00.0",
            },
        },
        "block": {},
    }

    tree = {node.address: node for node in snapshot.build_from(capture).pci_tree}

    roots = sorted(node.address for node in tree.values() if node.is_root)
    assert roots == ["10000:e0"], f"the VMD port was rooted under {roots}, not on its own bus 10000:e0"
    assert tree["10000:e0:06.0"].parent_address == "10000:e0"
    assert tree["10000:e1:00.0"].parent_address == "10000:e0:06.0", "the drive left its port"
    # The control: an identifier that genuinely carries no bus still goes to
    # the unplaced root, so the assertion above is not satisfied by a shape
    # that accepts everything.
    unplaced = assemble([_source(r"PCI\VEN_1AF4&DEV_1000\3&13c0b0c5&0&50")])
    assert [node.address for node in unplaced if node.is_root] == [UNPLACED_ROOT]


@pytest.mark.os_agnostic
def test_a_device_with_no_pci_address_is_placed_apart_rather_than_on_a_bus() -> None:
    """Windows publishes no address for some devices.

    The builder falls back to the instance identifier, which has no bus in it
    to derive.

    Splitting one on its last colon produced the empty string, so every such
    device shared one root labelled with nothing at all - a blank line in the
    root-complex list - and devices from different buses were merged into it.
    """
    instance = r"PCI\VEN_1AF4&DEV_1000&SUBSYS_00011AF4&REV_00\3&13c0b0c5&0&50"
    tree = assemble([_source("0000:00:01.0"), _source(instance)])

    roots = [node for node in tree if node.is_root]
    labels = [node.address for node in roots]

    assert "" not in labels, f"a root labelled with nothing: {labels}"
    assert all(label.strip() for label in labels), labels
    placed = {node.parent_address for node in tree if not node.is_root}
    assert len(placed) == len(roots), f"devices merged into one root: {placed} against {labels}"


@pytest.mark.os_agnostic
def test_two_entries_at_one_address_both_reach_the_tree() -> None:
    """A capture is untrusted input, and the fabric loses nothing it carries.

    Keying the sources by address collapsed a duplicate silently, last writer
    wins: on a hand-built pair the second device simply vanished and the first
    was drawn under the other's name, with nothing in the output saying a
    device had been dropped.
    """
    tree = assemble([_source("0000:06:03.0", name="first"), _source("0000:06:03.0", name="second")])

    devices = [node for node in tree if not node.is_root]

    assert len(devices) == 2, f"a device was dropped: {[node.address for node in devices]}"
    assert {node.name for node in devices} == {"first", "second"}
    assert len({node.address for node in devices}) == 2, "two nodes cannot share one address"


#: Enough devices for a quadratic to be unmistakable. The arms count work
#: rather than time it, so the size need not outrun a slow runner's noise - and
#: must not be large, because every line a quadratic executes is counted.
_CRAFTED_DEVICE_COUNT = 4_000

#: How many times the duplicate path may cost what the ordinary one does. A
#: ratio against a control in the same test rather than a ceiling, because the
#: claim is about COMPLEXITY. Counted end to end through ``assemble``, which
#: does linear work of its own around the keying (2026-09-28, 4,000 devices):
#: the source before the fix read 28.1 times the control, the fixed one 1.17.
#: Ten sits well clear of each.
_ACCEPTABLE_RATIO = 10


def _crafted(count: int, *, colliding: bool) -> list[NodeSource]:
    """Devices that all claim one address, or each its own."""
    return [
        NodeSource(
            "0000:06:03.0" if colliding else f"0000:{index // 256:02x}:{(index // 8) % 32:02x}.{index % 8}",
            f"device {index}",
            None,
            None,
            None,
            PcieLink(),
            PciPortKind.UNKNOWN,
            None,
            None,
            None,
        )
        for index in range(count)
    ]


def _work_to_assemble(sources: list[NodeSource]) -> int:
    """The work one assembly of those sources does, the building excluded.

    Counted by :mod:`workcount` rather than timed. Two wall-clock figures
    compared against each other move with whatever else the runner is doing,
    and a pause in the control makes the ratio look better than it is; a count
    is the same on every run.
    """
    nodes, work = work_to_run(lambda: assemble(sources))
    placed = [node for node in nodes if not node.is_root]
    assert len(placed) == len(sources), "the assembly dropped a device, so the count is of the wrong work"
    return work


@pytest.mark.os_agnostic
def test_a_capture_whose_devices_all_claim_one_address_is_keyed_in_the_same_pass() -> None:
    """A crafted capture cannot make the keying cost the square of its size.

    Every duplicate was placed by probing upward from copy 2 until a free key
    appeared, so N devices at one address cost N-squared over 2 probes. A
    capture is untrusted input and the size guard admits one up to 64 MB, which
    is hundreds of thousands of entries, so the ceiling on this is hours rather
    than the seconds the size bound was sized for. Only a Windows capture can
    reach it: the Linux builder keys off sysfs dict keys, which are unique by
    construction.

    The control is the same count at addresses of their own, counted in the
    same test, so what is asserted is the RATIO between the two paths rather
    than a figure that depends on how large the arms were chosen. It is
    measured through ``assemble`` rather than the keying alone, which is the
    seam a capture actually arrives at - and it is why the ratio is a tight one:
    the linear work around the keying lands in BOTH arms and shrinks the figure,
    so a loose ceiling here passes against the quadratic it exists to catch.
    """
    ordinary = _work_to_assemble(_crafted(_CRAFTED_DEVICE_COUNT, colliding=False))
    crafted = _work_to_assemble(_crafted(_CRAFTED_DEVICE_COUNT, colliding=True))

    assert ordinary > _CRAFTED_DEVICE_COUNT, "the control counted less than a unit per device, so it is not wired"
    assert crafted < ordinary * _ACCEPTABLE_RATIO, (
        f"{_CRAFTED_DEVICE_COUNT} devices at one address took {crafted} units of work against {ordinary} "
        f"for the same count at their own, a factor of {crafted / ordinary:.1f}"
    )


@pytest.mark.os_agnostic
def test_a_source_whose_own_address_carries_the_duplicate_mark_still_reaches_the_tree() -> None:
    """The case a remembered copy number alone would drop.

    The mark is chosen because no real PCI address holds it, and a capture is
    untrusted input, so an address that holds it anyway is exactly what has to
    be handled rather than assumed away. Keying the third device below straight
    into the copy number its base has never been given would land it on the key
    the second device already took, and the second would vanish silently, taking
    whatever hangs below it.
    """
    crafted = [
        NodeSource("0000:06:03.0", "first", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, None),
        NodeSource("0000:06:03.0", "second", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, None),
        NodeSource("0000:06:03.0#2", "third", None, None, None, PcieLink(), PciPortKind.UNKNOWN, None, None, None),
    ]

    placed = [node for node in assemble(crafted) if not node.is_root]

    assert len(placed) == len(crafted), f"a device was dropped: {sorted(node.address for node in placed)}"
    assert sorted(node.name for node in placed) == ["first", "second", "third"]


#: How many times the cycle-bearing path may cost the same devices in a chain.
#: Counted at 4,000 devices (2026-09-28): the source before the fix read 14.4
#: times the control, the fixed one 1.04. Cutting an edge is real work the
#: control does not do, so this one cannot be as loose as a ratio against an
#: idle path would allow.
_ACCEPTABLE_CYCLE_RATIO = 5


def _in_pairs(count: int, *, looping: bool) -> list[NodeSource]:
    """Devices paired off, each pair a two-node parent cycle or two roots."""

    def address(index: int) -> str:
        return f"0000:{index // 256:02x}:{(index // 8) % 32:02x}.{index % 8}"

    return [
        NodeSource(
            address(index),
            f"device {index}",
            None,
            None,
            None,
            PcieLink(),
            PciPortKind.UNKNOWN,
            None,
            None,
            address(index + 1 if index % 2 == 0 else index - 1) if looping else None,
        )
        for index in range(count)
    ]


@pytest.mark.os_agnostic
def test_a_capture_full_of_small_parent_cycles_is_cut_in_one_pass() -> None:
    """Many disjoint cycles cost what many devices cost, not their square.

    Breaking a cycle re-scanned every node from the top, so a capture holding
    one cycle per pair of devices paid that scan once per cycle. The module's
    own comment named it and nothing bounded it. A replay capture is untrusted
    input by this repo's own topology rules, and the 64 MB input bound admits
    hundreds of thousands of devices.

    The control is the same devices with no parents at all, so the difference
    between the arms is the cycles rather than the size, and the assertion is a
    ratio of the two counts.
    """
    ordinary = _work_to_assemble(_in_pairs(_CRAFTED_DEVICE_COUNT, looping=False))
    looping = _work_to_assemble(_in_pairs(_CRAFTED_DEVICE_COUNT, looping=True))

    assert ordinary > _CRAFTED_DEVICE_COUNT, "the control counted less than a unit per device, so it is not wired"
    assert looping < ordinary * _ACCEPTABLE_CYCLE_RATIO, (
        f"{_CRAFTED_DEVICE_COUNT} devices in {_CRAFTED_DEVICE_COUNT // 2} cycles took {looping} units of work "
        f"against {ordinary} for the same devices in none, a factor of {looping / ordinary:.1f}"
    )
