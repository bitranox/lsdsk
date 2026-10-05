"""A controller or a drive finds a better seat without walking every port in the machine.

Three searches used to scan the whole machine once per subject, so a machine
cost the PRODUCT of its subjects and its ports:

* a controller capped by its port looked for a free slot, a swappable slot and
  the best port on the board, and asked the board's generation, each a walk of
  every PCIe port;
* a controller at the PCIe floor looked up the port holding it, whether that
  port's bus hangs behind a bridge, and every port beside it, each a walk of
  every port - so a switch with many functions on it cost the square of them;
* a drive capped by its port walked every controller for a free, faster one.

Two properties are held here, and the second is what makes the first safe: the
work grows linearly with the machine, and each indexed query answers exactly
what the scan it replaces answered. The scans are kept below as the oracles,
because they are the plainest statement of which port or controller each rule
means.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from workcount import work_to_run

from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import BusType, ControllerKind, Severity
from lsdsk.domain.models import (
    Controller,
    Disk,
    InterfaceLink,
    Inventory,
    PcieLink,
    PcieSlot,
    pci_bus_of,
    pcie_bandwidth_gbps,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: How many times the doubled machine may cost the single one, in calls plus
#: lines executed. A walk of every port per controller multiplied the work by
#: 3.0 to 3.7 on a doubling at these sizes before the indexes existed; a search
#: through them doubles it. Counted rather than timed, so a loaded runner
#: cannot move it.
_ACCEPTABLE_GROWTH = 2.5

_GEN3X4 = PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=8.0, max_width=4)
_GEN4X4_AT_GEN3 = PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=16.0, max_width=4)
_FLOOR = PcieLink(current_speed_gtps=2.5, current_width=1, max_speed_gtps=2.5, max_width=1)
_SATA_6G = InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0, port_max_gbps=6.0)


def _sata(index: int, controller: str, link: InterfaceLink = _SATA_6G) -> Disk:
    return Disk(
        node=f"sd{index}",
        path=f"/dev/sd{index}",
        model="m",
        serial=f"S{index}",
        bus=BusType.SATA,
        controller_address=controller,
        link=link,
    )


def _capped_cards(count: int) -> Inventory:
    """``count`` bridges, each holding a Gen4 x4 AHCI card that a Gen3 x4 port caps."""
    slots: list[PcieSlot] = []
    controllers: list[Controller] = []
    disks: list[Disk] = []
    for index in range(count):
        port, card = f"{index:04x}:00:01.0", f"{index:04x}:01:00.0"
        slots.append(
            PcieSlot(
                address=port,
                link=_GEN3X4,
                occupied=True,
                connector_present=True,
                occupant_address=card,
                occupant_link=_GEN4X4_AT_GEN3,
            )
        )
        controllers.append(
            Controller(
                address=card,
                name=f"AHCI {index}",
                kind=ControllerKind.AHCI,
                link=_GEN4X4_AT_GEN3,
                upstream=_GEN3X4,
                upstream_address=port,
            )
        )
        disks.append(_sata(index, card))
    return Inventory(hostname="capped", controllers=tuple(controllers), disks=tuple(disks), slots=tuple(slots))


def _floor_functions_on_one_switch(count: int) -> Inventory:
    """``count`` chipset SATA functions at the PCIe floor behind ONE switch, beside one real link."""
    maker = 0x1022
    slots = [
        PcieSlot(address="0000:03:00.0", link=_GEN3X4, occupied=True, occupant_address="0000:04:00.0"),
        PcieSlot(
            address="0000:04:00.0",
            link=_GEN3X4,
            occupied=True,
            occupant_address="0000:05:00.0",
            occupant_link=_GEN3X4,
            vendor=maker,
            occupant_vendor=0x10EC,
        ),
    ]
    controllers: list[Controller] = []
    disks: list[Disk] = []
    for index in range(count):
        port, function = f"0000:04:{index // 8 + 1:02x}.{index % 8}", f"{index + 16:04x}:00:00.0"
        slots.append(
            PcieSlot(
                address=port,
                link=_FLOOR,
                occupied=True,
                occupant_address=function,
                occupant_link=_FLOOR,
                vendor=maker,
                occupant_vendor=maker,
            )
        )
        controllers.append(
            Controller(
                address=function,
                name=f"SATA {index}",
                kind=ControllerKind.AHCI,
                link=_FLOOR,
                upstream=_FLOOR,
                upstream_address=port,
                vendor=maker,
            )
        )
        disks.append(_sata(index, function))
    return Inventory(hostname="switch", controllers=tuple(controllers), disks=tuple(disks), slots=tuple(slots))


def _drives_capped_by_their_ports(count: int) -> Inventory:
    """``count`` 32-port AHCI controllers, each with one 6 Gb/s drive on a 3 Gb/s port."""
    capped = InterfaceLink(negotiated_gbps=3.0, drive_max_gbps=6.0, port_max_gbps=3.0)
    controllers = tuple(
        Controller(address=f"{index:04x}:00:00.0", name=f"AHCI {index}", port_count=32, ports_used=1)
        for index in range(count)
    )
    disks = tuple(_sata(index, controller.address, capped) for index, controller in enumerate(controllers))
    return Inventory(hostname="limited", controllers=controllers, disks=disks)


def _work_to_diagnose(machine: Inventory, *, expected_title: str) -> int:
    findings, work = work_to_run(lambda: diagnose(machine))
    # The control: every subject reached the rule this arm is named for, so a
    # machine the search never ran on cannot pass for one it searched cheaply.
    subjects = len(machine.controllers)
    reached = [finding for finding in findings if expected_title in finding.title]
    assert len(reached) == subjects, f"only {len(reached)} of {subjects} subjects reached '{expected_title}'"
    assert work > 0, "no work was counted, so the counter is not wired"
    return work


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("build", "size", "expected_title"),
    [
        (_capped_cards, 100, "is capped by the mainboard, not by itself"),
        (_floor_functions_on_one_switch, 200, "publishes the PCIe floor as its link"),
        (_drives_capped_by_their_ports, 200, "is held back by its controller"),
    ],
    ids=["port-capped-controllers", "floor-functions-on-one-switch", "port-capped-drives"],
)
def test_doubling_the_machine_does_not_quadruple_the_placement_search(
    build: Callable[[int], Inventory], size: int, expected_title: str
) -> None:
    """Twice the subjects costs about twice the work, whichever search the rule makes."""
    single = _work_to_diagnose(build(size), expected_title=expected_title)
    doubled = _work_to_diagnose(build(2 * size), expected_title=expected_title)
    assert doubled / single <= _ACCEPTABLE_GROWTH, (
        f"doubling {size} subjects took {doubled} steps against {single}, "
        f"{doubled / single:.2f} times the work: a search is walking the whole machine per subject"
    )


# --------------------------------------------------------------------------
# Identical answers
# --------------------------------------------------------------------------

#: Every speed the lane table knows, some it does not, and the non-finite
#: values a crafted capture can carry.
_SPEED = st.one_of(
    st.none(),
    st.sampled_from([2.5, 5.0, 8.0, 16.0, 32.0, 64.0]),
    st.sampled_from([0.0, 7.0, 100.0, math.inf, -math.inf, math.nan]),
)
_WIDTH = st.one_of(st.none(), st.sampled_from([0, 1, 2, 4, 8, 16]), st.integers(min_value=-2, max_value=40))
_LINK = st.builds(PcieLink, current_speed_gtps=_SPEED, current_width=_WIDTH, max_speed_gtps=_SPEED, max_width=_WIDTH)
_FLOORISH = st.one_of(st.just(_FLOOR), st.just(_GEN3X4), _LINK)
#: Few enough addresses that two ports share one, and buses shared by several.
_ADDRESS = st.sampled_from(
    [f"{bus}:{device:02x}.0" for bus in ("0000:00", "0000:04", "0001:00") for device in range(4)]
)
_VENDOR = st.sampled_from([None, 0x1022, 0x8086])


@st.composite
def _ports(draw: st.DrawFn) -> tuple[PcieSlot, ...]:
    return tuple(
        PcieSlot(
            address=draw(_ADDRESS),
            link=draw(_FLOORISH),
            occupied=draw(st.booleans()),
            connector_present=draw(st.sampled_from([None, True, False])),
            occupant_address=draw(st.one_of(st.none(), _ADDRESS)),
            occupant_class=draw(st.sampled_from([None, 0x010601, 0x030000])),
            occupant_link=draw(st.one_of(st.none(), _FLOORISH)),
            vendor=draw(_VENDOR),
            occupant_vendor=draw(_VENDOR),
        )
        for _ in range(draw(st.integers(min_value=0, max_value=16)))
    )


def _lower(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _lower_int(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _gain(slot: PcieSlot, card: PcieLink) -> float | None:
    if slot.link.max_speed_gtps is None or slot.link.max_width is None:
        return None
    return pcie_bandwidth_gbps(
        _lower(slot.link.max_speed_gtps, card.max_speed_gtps), _lower_int(slot.link.max_width, card.max_width)
    )


def _first_better_than(
    slots: Sequence[PcieSlot],
    card: PcieLink,
    *,
    floor: float,
    besides: str | None,
    admits: Callable[[PcieSlot], bool],
    below: frozenset[str] = frozenset(),
) -> PcieSlot | None:
    """The oracle for the free and the swap search: the first port that beats the running seat most."""
    best: PcieSlot | None = None
    for slot in slots:
        if slot.address == besides or slot.address in below or not admits(slot):
            continue
        gain = _gain(slot, card)
        if gain is not None and gain > floor:
            best, floor = slot, gain
    return best


def _most_capable(slots: Sequence[PcieSlot], card: PcieLink, *, besides: str | None) -> PcieSlot | None:
    """The oracle for the best port on the board: most gain, then most capability, earliest first."""
    best: tuple[PcieSlot, float] | None = None
    for slot in slots:
        if slot.address == besides:
            continue
        gain = _gain(slot, card)
        if gain is None:
            continue
        if best is None or (gain, slot.capability_gbps or 0.0) > (best[1], best[0].capability_gbps or 0.0):
            best = (slot, gain)
    return None if best is None else best[0]


#: Tests that depend on the port alone, as every search's admission does.
_ADMITS: tuple[Callable[[PcieSlot], bool], ...] = (
    lambda slot: True,
    lambda slot: slot.is_move_target,
    lambda slot: slot.is_swap_candidate and (slot.occupant_need_gbps or 0.0) < 2.0,
    # The search for a free port whose connector nobody read: it reads the
    # occupancy and the connector bit, which is why both are in the grouping.
    lambda slot: not slot.occupied and slot.connector_present is None,
)


@pytest.mark.os_agnostic
@given(
    ports=_ports(),
    card=_LINK,
    besides=st.one_of(st.none(), _ADDRESS),
    floor=st.sampled_from([0.0, 0.25, 3.94, 7.88]),
    admits=st.sampled_from(_ADMITS),
    inside=st.frozensets(_ADDRESS, max_size=3),
)
@settings(max_examples=500, deadline=None)
def test_the_candidates_hold_every_port_a_search_could_choose(
    ports: tuple[PcieSlot, ...],
    card: PcieLink,
    besides: str | None,
    floor: float,
    admits: Callable[[PcieSlot], bool],
    inside: frozenset[str],
) -> None:
    """A search over one port per group names the port a search over all of them names.

    ``inside`` is a card's own functions, skipped port by port: dropping a whole
    group because its first port is the card's own would lose an equal port
    elsewhere on the board.
    """
    machine = Inventory(hostname="generated", slots=ports)
    candidates = machine.placement_candidates(besides=besides, admits=admits, inside=inside)
    assert _first_better_than(
        candidates, card, floor=floor, besides=besides, admits=admits, below=inside
    ) is _first_better_than(ports, card, floor=floor, besides=besides, admits=admits, below=inside)
    if admits is _ADMITS[0] and not inside:
        assert _most_capable(candidates, card, besides=besides) is _most_capable(ports, card, besides=besides)


@pytest.mark.os_agnostic
@given(ports=_ports(), address=_ADDRESS, maker=st.sampled_from([0x1022, 0x8086]))
@settings(max_examples=300, deadline=None)
def test_every_port_lookup_answers_what_a_walk_of_the_ports_answers(
    ports: tuple[PcieSlot, ...], address: str, maker: int
) -> None:
    """The port at an address, the buses behind a bridge, and the two readings a switch is judged by."""
    machine = Inventory(hostname="generated", slots=ports)
    bus = pci_bus_of(address)
    assert bus is not None
    assert machine.slot_at(address) is next((slot for slot in ports if slot.address == address), None)
    assert machine.bus_is_behind_a_bridge(bus) == any(pci_bus_of(slot.occupant_address or "") == bus for slot in ports)
    on_bus = [slot for slot in ports if pci_bus_of(slot.address) == bus]
    assert list(machine.floor_functions_on(bus, maker)) == [
        slot
        for slot in on_bus
        if slot.occupant_link is not None
        and slot.occupant_link.is_at_floor
        and slot.switch_function_maker == maker
        and not slot.capability_denies_a_function
    ]
    assert list(machine.ports_running_above_floor_on(bus)) == [
        slot for slot in on_bus if slot.occupant_link is not None and slot.occupant_link.is_running_above_floor
    ]
    speeds = [slot.link.max_speed_gtps for slot in ports if slot.link.max_speed_gtps is not None]
    expected = max(speeds) if speeds else None
    assert repr(machine.fastest_slot_speed_gtps) == repr(expected)


_RATE = st.one_of(st.none(), st.sampled_from([1.5, 3.0, 6.0, 12.0, 22.5, 0.0, math.inf, math.nan]))
_CONTROLLER_ADDRESS = st.sampled_from([f"0000:0{index}:00.0" for index in range(5)])


@st.composite
def _machines_of_controllers(draw: st.DrawFn) -> Inventory:
    controllers = tuple(
        Controller(
            address=draw(_CONTROLLER_ADDRESS),
            name=f"c{index}",
            port_count=draw(st.sampled_from([None, 0, 1, 8])),
            ports_used=draw(st.sampled_from([None, 0, 1, 8])),
        )
        for index in range(draw(st.integers(min_value=0, max_value=8)))
    )
    disks = tuple(
        Disk(
            node=f"sd{index}",
            path=f"/dev/sd{index}",
            model="m",
            controller_address=draw(st.one_of(st.none(), _CONTROLLER_ADDRESS)),
            link=InterfaceLink(port_max_gbps=draw(_RATE)),
        )
        for index in range(draw(st.integers(min_value=0, max_value=12)))
    )
    return Inventory(hostname="generated", controllers=controllers, disks=disks)


def _fastest_rate_by_walking_disks(machine: Inventory, controller_address: str | None) -> float | None:
    """The best port rate on one controller, read straight off the disks.

    Computed independently of :meth:`Inventory.fastest_port_rate_on`, which is
    the index under test: an oracle that calls the same method agrees with any
    mutation made to it. ``max`` over the matching rates in inventory order
    reproduces the running-best loop the index itself uses, NaN and all, so the
    two can disagree only when the index's own arithmetic is wrong.
    """
    rates = [
        disk.link.port_max_gbps
        for disk in machine.disks
        if disk.controller_address == controller_address and disk.link.port_max_gbps is not None
    ]
    return max(rates) if rates else None


def _first_faster_by_walking(machine: Inventory, rate: float, besides: str | None) -> Controller | None:
    """The oracle: the first other controller with a free port faster than ``rate``."""
    for controller in machine.controllers:
        if controller.address == besides or not controller.ports_free:
            continue
        best = _fastest_rate_by_walking_disks(machine, controller.address)
        if best is not None and best > rate:
            return controller
    return None


@pytest.mark.os_agnostic
@given(
    machine=_machines_of_controllers(),
    rate=st.sampled_from([0.0, 1.5, 3.0, 6.0, 12.0, math.inf, -math.inf, math.nan]),
    besides=st.one_of(st.none(), _CONTROLLER_ADDRESS),
)
@settings(max_examples=500, deadline=None)
def test_the_faster_free_port_is_the_one_a_walk_of_the_controllers_finds(
    machine: Inventory, rate: float, besides: str | None
) -> None:
    """The same controller, the earliest one, skipping the drive's own."""
    found = machine.first_controller_with_a_free_port_faster_than(rate, besides=besides)
    assert found is _first_faster_by_walking(machine, rate, besides)


@pytest.mark.os_agnostic
def test_a_rate_nobody_can_beat_does_not_hide_the_controller_beside_it() -> None:
    """A NaN rate beats nothing, and must not stand in for the faster controller after it.

    Pinned by hand because a generated machine rarely puts a NaN directly
    before the answer: a max taken over a NaN first returns the NaN, and a
    search trusting it would report no faster port where the walk finds one.
    """
    nan_rate = InterfaceLink(port_max_gbps=math.nan)
    fast_rate = InterfaceLink(port_max_gbps=6.0)
    unread = Controller(address="a", name="unread", port_count=4, ports_used=1)
    fast = Controller(address="b", name="fast", port_count=4, ports_used=1)
    machine = Inventory(
        hostname="h",
        controllers=(unread, fast),
        disks=(
            Disk(node="sda", path="/dev/sda", model="m", controller_address="a", link=nan_rate),
            Disk(node="sdb", path="/dev/sdb", model="m", controller_address="b", link=fast_rate),
        ),
    )
    assert _first_faster_by_walking(machine, 3.0, None) is fast
    assert machine.first_controller_with_a_free_port_faster_than(3.0, besides=None) is fast


@pytest.mark.os_agnostic
def test_a_capped_drive_is_told_to_move_to_the_faster_free_port_the_rule_finds() -> None:
    """``diagnose`` itself raises the move warning, naming the controller it found.

    ``test_doubling_the_machine_does_not_quadruple_the_placement_search`` only
    exercises the HINT branch (no faster port free anywhere), so this is the
    first end-to-end proof that the WARNING branch - a faster free port really
    exists - fires through the real rule rather than only through the index's
    own unit tests.
    """
    capped = InterfaceLink(negotiated_gbps=3.0, drive_max_gbps=6.0, port_max_gbps=3.0)
    slowest = InterfaceLink(negotiated_gbps=1.0, drive_max_gbps=1.0, port_max_gbps=1.0)
    slow = Controller(address="0000:00:1f.2", name="slow AHCI", port_count=1, ports_used=1)
    # Two occupied ports plus one free: a port rate taken as the MINIMUM of
    # the two, rather than the maximum the rule is written for, reads 1.0 Gb/s
    # here - slower than the capped drive's own port - and the warning would
    # never fire, which is what makes this fixture catch that mutation.
    fast = Controller(address="0000:01:00.0", name="fast HBA", port_count=3, ports_used=2)
    machine = Inventory(
        hostname="h",
        controllers=(slow, fast),
        disks=(
            _sata(0, slow.address, capped),
            _sata(1, fast.address, slowest),
            _sata(2, fast.address, _SATA_6G),
        ),
    )
    findings = diagnose(machine)
    moves = [finding for finding in findings if "is on a port slower than the drive" in finding.title]
    assert len(moves) == 1, f"expected one move warning, got {[f.title for f in findings]}"
    move = moves[0]
    assert move.severity is Severity.WARNING
    assert move.subject == "/dev/sd0"
    assert move.action is not None
    assert f"{fast.name} at {fast.address}" in move.action
