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

from typing import TYPE_CHECKING

from .base import DomainModel
from .models import PciNode

if TYPE_CHECKING:
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


__all__ = ["FabricLink", "fabric_links"]
