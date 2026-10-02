"""Hints for the PCIe links no storage rule grades.

The storage rules grade a controller's link with the drives behind it in mind.
Every other card - a graphics card, a network card, a switch carrying either -
was drawn in the topology and judged nowhere, so a card that lost lanes or sits
in a slot narrower than itself went unremarked. These rules grade them, as
hints: nothing here costs a drive anything.

A speed-only shortfall is deliberately not graded. A graphics card lowers its
link speed while idle and retrains under load, so one reading cannot tell power
saving from a fault; width and capability do not move with load.

System Role:
    Domain layer. Pure: reads the inventory, returns findings.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from .base import DomainModel
from .enums import Severity
from .models import Finding, PciNode, pcie_bandwidth_gbps
from .pcie_text import format_gbytes, format_pcie_sentence
from .placement import Seat, free_slot_for

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .models import Inventory


class FabricLink(DomainModel, frozen=True):
    """One PCIe link no storage rule grades: a port and the device behind it.

    Attributes:
        port: The port the link leaves from, which faces downstream.
        functions: Every function of the device behind it, which share the link.
    """

    port: PciNode
    functions: tuple[PciNode, ...]

    @property
    def card(self) -> PciNode:
        """The function whose registers stand for the link: the lowest address."""
        return min(self.functions, key=lambda node: node.address)


def _device_of(address: str) -> str:
    """The bus:device part of an address, which every function of it shares."""
    return address.rpartition(".")[0]


def _graded_elsewhere(inventory: Inventory) -> frozenset[str]:
    """Addresses a storage rule already grades: controllers, and what a drive hangs off."""
    hosts = {disk.controller_address for disk in inventory.disks if disk.controller_address is not None}
    return frozenset({controller.address for controller in inventory.controllers} | hosts)


def fabric_links(inventory: Inventory) -> tuple[FabricLink, ...]:
    """Every link these rules grade, one per device behind a downstream-facing port.

    Args:
        inventory: The machine.

    Returns:
        The links, ordered by the address of the device behind each port.
    """
    by_address = {node.address: node for node in inventory.pci_tree}
    grouped: dict[tuple[str, str], list[PciNode]] = {}
    for node in inventory.pci_tree:
        port = by_address.get(node.parent_address) if node.parent_address is not None else None
        if port is None or not port.faces_downstream:
            continue
        grouped.setdefault((port.address, _device_of(node.address)), []).append(node)
    skipped = _graded_elsewhere(inventory)
    links = [
        FabricLink(port=by_address[port_address], functions=tuple(functions))
        for (port_address, _device), functions in grouped.items()
        if not any(function.address in skipped for function in functions)
    ]
    return tuple(sorted(links, key=lambda link: link.card.address))


#: How many distinct names a carrying clause spells out before it counts the rest.
_NAMES_SPELLED_OUT = 2


def _is_function_zero(node: PciNode) -> bool:
    """Whether a node is a device's first function, which stands for the device."""
    return node.address.rpartition(".")[2].partition("#")[0] == "0"


def _devices_below(link: FabricLink, tree: Sequence[PciNode]) -> list[PciNode]:
    """The end devices below the card, walked from an explicit stack, first functions only."""
    children: dict[str, list[PciNode]] = {}
    for node in tree:
        if node.parent_address is not None:
            children.setdefault(node.parent_address, []).append(node)
    found: list[PciNode] = []
    stack = [function.address for function in link.functions]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child.is_bridge_family:
                stack.append(child.address)
            elif _is_function_zero(child):
                found.append(child)
    return sorted(found, key=lambda node: node.address)


def carrying_clause(link: FabricLink, tree: Sequence[PciNode]) -> str:
    """What a bridge or switch card carries, for the title of its finding.

    A switch or a bridge chip is the device at the card end of the link, and its
    name is one a reader has never seen on the box: the HD 7990 reads as a PLX
    switch. So the title names the end devices behind it, grouped by name.

    Args:
        link: The link whose card end is described.
        tree: The machine's whole PCI tree.

    Returns:
        ``""`` for a card with nothing behind it, otherwise ``", carrying "``
        and the names, at most two spelled out and the rest counted.
    """
    counted = Counter(node.name for node in _devices_below(link, tree))
    if not counted:
        return ""
    names = [f"{count}x {name}" if count > 1 else name for name, count in counted.items()]
    spelled = ", ".join(names[:_NAMES_SPELLED_OUT])
    rest = len(names) - _NAMES_SPELLED_OUT
    return f", carrying {spelled}" + (f" and {rest} more" if rest > 0 else "")


class _Shape(NamedTuple):
    """A link's generation and width, both read."""

    speed_gtps: float
    width: int


def _both_support(link: FabricLink) -> _Shape | None:
    """What both ends of a link support, or ``None`` when either end was not read."""
    card, port = link.card.link, link.port.link
    # BOTH ends or nothing, as for a storage controller: an unread port filled in
    # from the card would make "not measured" read as "the port is fine".
    if card.max_speed_gtps is None or card.max_width is None:
        return None
    if port.max_speed_gtps is None or port.max_width is None:
        return None
    return _Shape(min(card.max_speed_gtps, port.max_speed_gtps), min(card.max_width, port.max_width))


def diagnose_fabric_link(link: FabricLink, inventory: Inventory) -> list[Finding]:
    """Grade one link: lanes lost first, then a slot that caps the card.

    Args:
        link: The link.
        inventory: The machine, for the tree the title names and the free slots.

    Returns:
        At most one hint; none where either end's capability or the running
        width was not read, where the link never trained, or where the only
        shortfall is speed.
    """
    achievable = _both_support(link)
    running_width = link.card.link.current_width
    # A width of 0 is a link that never trained, which the tree already draws
    # as "none"; a lanes finding there would count every lane as lost.
    if achievable is None or running_width is None or running_width == 0:
        return []
    carrying = carrying_clause(link, inventory.pci_tree)
    # The clause is parenthetical, so it is closed again before the verb.
    name = f"{link.card.name}{carrying}," if carrying else link.card.name
    # Lanes first: a card that lost lanes in a slot that also caps it needs the
    # contact checked before a move would show what the slot gives it.
    if running_width < achievable.width:
        return [_lanes_lost(link, name=name, achievable=achievable, running_width=running_width)]
    capped = pcie_bandwidth_gbps(achievable.speed_gtps, achievable.width)
    own = link.card.link.max_bandwidth_gbps
    if capped is None or own is None or capped >= own:
        return []
    return [_slot_capped(link, name=name, achievable=achievable, inventory=inventory)]


def _lanes_lost(link: FabricLink, *, name: str, achievable: _Shape, running_width: int) -> Finding:
    """The hint for a link running narrower than both of its ends support."""
    running = link.card.link
    return Finding(
        severity=Severity.HINT,
        subject=link.card.address,
        title=f"{name} runs on fewer lanes than both ends support",
        detail=(
            f"Running {format_pcie_sentence(running.current_speed_gtps, running_width)} "
            f"({format_gbytes(running.current_bandwidth_gbps)}) where the card and the port at "
            f"{link.port.address} both support {format_pcie_sentence(achievable.speed_gtps, achievable.width)} "
            f"({format_gbytes(pcie_bandwidth_gbps(achievable.speed_gtps, achievable.width))}): "
            f"{achievable.width - running_width} of {achievable.width} lanes did not train."
        ),
        action="Reseat the card and check the slot and any riser; a lane that does not train is usually a contact.",
    )


def _slot_capped(link: FabricLink, *, name: str, achievable: _Shape, inventory: Inventory) -> Finding:
    """The hint for a card its slot holds below what the card can do."""
    card, port = link.card.link, link.port.link
    return Finding(
        severity=Severity.HINT,
        subject=link.card.address,
        title=f"{name} is capped by its slot",
        detail=(
            f"The card can do {format_pcie_sentence(card.max_speed_gtps, card.max_width)} "
            f"({format_gbytes(card.max_bandwidth_gbps)}); the port at {link.port.address} offers "
            f"{format_pcie_sentence(port.max_speed_gtps, port.max_width)} ({format_gbytes(port.max_bandwidth_gbps)}), "
            f"which caps it at {format_pcie_sentence(achievable.speed_gtps, achievable.width)} "
            f"({format_gbytes(pcie_bandwidth_gbps(achievable.speed_gtps, achievable.width))})."
        ),
        action=_where_it_could_go(link, inventory),
    )


def _where_it_could_go(link: FabricLink, inventory: Inventory) -> str:
    """Name a free slot that would carry more, or say why none is named."""
    seat = Seat(link=link.card.link, port=link.port.link, port_address=link.port.address)
    free = free_slot_for(seat, inventory)
    if free is not None:
        number = "" if free.physical_slot_number is None else f", slot {free.physical_slot_number}"
        figure = format_pcie_sentence(free.link.max_speed_gtps, free.link.max_width)
        return (
            f"Move it to the free slot at {free.address} ({figure}{number}). "
            "Check the slot is mechanically long enough or open-ended first."
        )
    # A connector bit nobody read is not a missing slot: without root no port
    # is known to end in one, and "no free slot" would claim what was not seen.
    if not any(slot.connector_present is not None for slot in inventory.slots):
        return (
            "Whether a free slot would carry more was not readable: telling a slot from an internal port "
            "needs the PCIe capability, which takes root to read."
        )
    # Named by the card's own figure rather than "a faster port": a card short on
    # lanes can sit in a port that is already the faster of the two. The figure
    # is a floor, not a match: any port at least that wide and that fast runs
    # the card in full.
    own = format_pcie_sentence(link.card.link.max_speed_gtps, link.card.link.max_width)
    return f"No free slot on this board would carry more; the card needs a port of at least {own} to run in full."


def diagnose_fabric_links(inventory: Inventory) -> list[Finding]:
    """Grade every link no storage rule grades.

    Args:
        inventory: The machine.

    Returns:
        One hint per link that lost lanes or is capped by its slot.
    """
    return [finding for link in fabric_links(inventory) for finding in diagnose_fabric_link(link, inventory)]


__all__ = ["FabricLink", "carrying_clause", "diagnose_fabric_link", "diagnose_fabric_links", "fabric_links"]
