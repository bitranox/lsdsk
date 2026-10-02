"""Where a card could sit: the one search for a better seat.

Asked by the storage rules for a controller and by the fabric-link rules for any
other card, so both are held to the same evidence: a slot is a move target only
where its connector bit was read, and an unread end is never a capable one.

System Role:
    Domain layer. Pure: reads the inventory's ports, returns one of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DomainModel
from .models import PcieLink, pcie_bandwidth_gbps
from .text import OptionalDeviceText

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import Controller, Inventory, PcieSlot


class Seat(DomainModel, frozen=True):
    """Where a card sits: its own link registers and the port it sits behind.

    Attributes:
        link: The card's own link registers.
        port: The port's link registers, or ``None`` where the port was never
            identified.
        port_address: The port's PCI address, which a search for a better seat
            never offers back.
    """

    link: PcieLink
    port: PcieLink | None = None
    port_address: OptionalDeviceText = None


def seat_of(controller: Controller) -> Seat:
    """The seat a storage controller sits in.

    Args:
        controller: The controller.

    Returns:
        Its link, the port above it, and that port's address.
    """
    return Seat(link=controller.link, port=controller.upstream, port_address=controller.upstream_address)


def achievable_pcie(seat: Seat) -> tuple[float | None, int | None]:
    """Return what the port a card currently sits in can give it.

    This is the *current* seat, not the best the board can offer; the two
    differ whenever a faster port exists but is occupied.

    Args:
        seat: The card and its port.

    Returns:
        The speed and width both ends support, or ``(None, None)`` when either
        end's capability was not read.
    """
    own_speed = seat.link.max_speed_gtps
    own_width = seat.link.max_width
    upstream = seat.port
    # BOTH ends or nothing. Taking the device's own maximum when the port was not
    # read makes an unmeasured port look at least as fast as the device, which
    # turns "not measured" into "the port is fine" and bills the shortfall to a
    # cable. Measured: a Gen5 drive in a Gen4 socket, on a platform that exposes
    # no link properties for PCIe bridges at all, was reported as a negotiation
    # fault with advice to reseat it.
    if own_speed is None or own_width is None:
        return None, None
    if upstream is None or upstream.max_speed_gtps is None or upstream.max_width is None:
        return None, None
    return min(own_speed, upstream.max_speed_gtps), min(own_width, upstream.max_width)


def _lower(left: float | None, right: float | None) -> float | None:
    """Return the smaller of two optional numbers, ignoring unknowns."""
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _lower_int(left: int | None, right: int | None) -> int | None:
    """Return the smaller of two optional integers, ignoring unknowns."""
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def gain_in(slot: PcieSlot, seat: Seat) -> float | None:
    """What a card would get in one slot, in GB/s.

    A slot that does not report its own speed and width is never a candidate.
    Legacy PCI bridges report neither, and treating an unknown capability as an
    unlimited one made every such bridge look like the fastest slot in the
    machine, which produced a confident recommendation to move a card into a
    slot slower than the one it already occupied.

    Args:
        slot: The port the card would move to.
        seat: The card, whose own capability bounds what any port gives it.

    Returns:
        The bandwidth of the lower of the two ends, or ``None`` when the slot's
        own capability was not read.
    """
    if slot.link.max_speed_gtps is None or slot.link.max_width is None:
        return None
    return pcie_bandwidth_gbps(
        _lower(slot.link.max_speed_gtps, seat.link.max_speed_gtps),
        _lower_int(slot.link.max_width, seat.link.max_width),
    )


def best_slot(seat: Seat, inventory: Inventory, candidates: Callable[[PcieSlot], bool]) -> PcieSlot | None:
    """Find the slot passing a test that would serve a card best.

    Args:
        seat: The card and the port it sits behind now.
        inventory: The machine whose ports are searched.
        candidates: The search's own test of a port.

    Returns:
        The admitted port that would give the card the most, or ``None`` when
        none beats its current seat or that seat's figure was not read.
    """
    current = pcie_bandwidth_gbps(*achievable_pcie(seat))
    if current is None:
        return None
    best: PcieSlot | None = None
    best_bandwidth = current
    # The port this card already sits behind is skipped by the address it
    # records for that port. Matching the port's OCCUPANT instead missed it
    # whenever a sibling represented the port, and then offered the card the
    # seat it is already in as somewhere better to be. The machine hands over
    # one port per group of interchangeable ones rather than every port, which
    # is what keeps this search from costing the whole port list per card.
    for slot in inventory.placement_candidates(besides=seat.port_address, admits=candidates):
        gain = gain_in(slot, seat)
        if gain is not None and gain > best_bandwidth:
            best, best_bandwidth = slot, gain
    return best


def free_slot_for(seat: Seat, inventory: Inventory) -> PcieSlot | None:
    """Find an empty slot with a real connector that would serve a card better.

    Args:
        seat: The card and the port it sits behind now.
        inventory: The machine whose ports are searched.

    Returns:
        The best free slot, or ``None``.
    """
    return best_slot(seat, inventory, lambda slot: slot.is_move_target)


def unread_free_slot_for(seat: Seat, inventory: Inventory) -> PcieSlot | None:
    """Find an empty port that would serve a card better but whose connector bit nobody read.

    Asked where :func:`free_slot_for` found nothing, so that a port which might
    be a slot is never reported as absent. Judged per port: one connector read
    elsewhere on the board says nothing about this one.

    Args:
        seat: The card and the port it sits behind now.
        inventory: The machine whose ports are searched.

    Returns:
        The best such port, or ``None``.
    """
    return best_slot(seat, inventory, lambda slot: not slot.occupied and slot.connector_present is None)


__all__ = ["Seat", "achievable_pcie", "best_slot", "free_slot_for", "gain_in", "seat_of", "unread_free_slot_for"]
