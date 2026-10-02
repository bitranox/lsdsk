"""Pure rules that turn an inventory into findings.

No I/O, no formatting, no platform knowledge.  Given the same inventory these
functions always produce the same findings, which is what makes them testable
against hand-built cases and against snapshots captured from real machines.

The grading principle: a link running below a device's own maximum is only a
fault when the machine could actually do better.  Every speed rule therefore
weighs three numbers, what the device can do, what the other end can do, and
what was negotiated, and only calls it a warning when something on this machine
can be changed.  Where the platform is the ceiling, the finding says which
upgrade would lift it and whether the attached load would even notice.  A
faster slot is weighed the same way: moving or swapping a card is a warning
only when the drives on it could use the difference, and a hint naming the
slot otherwise.

System Role:
    The analytical core.  Renderers and the CLI consume its output; nothing
    here consumes theirs.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, NamedTuple

from .enums import Severity
from .fabric_links import diagnose_fabric_links
from .history import CounterKind, History, identity_of, trend_for
from .models import (
    Controller,
    Disk,
    Finding,
    Inventory,
    PcieSlot,
    pci_bus_of,
    pci_class_name,
    pcie_bandwidth_gbps,
    pcie_generation,
    serial_bandwidth_gbps,
    shared_maker,
)
from .pcie_text import format_gbytes, format_pcie_sentence
from .placement import achievable_pcie, best_slot, free_slot_for, gain_in, seat_of
from .thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .history import DiskSeries, Trend
    from .models import UsbLink, UsbSpeed
    from .thresholds import Thresholds


def interface_demand_gbytes(disk: Disk) -> float | None:
    """Return the bandwidth one disk can actually pull, in GB/s.

    Args:
        disk: The disk to measure.

    Returns:
        Usable bandwidth in GB/s, or ``None`` when the link is unknown.

    Example:
        >>> from lsdsk.domain.models import Disk, InterfaceLink
        >>> interface_demand_gbytes(
        ...     Disk(
        ...         node="sda",
        ...         path="/dev/sda",
        ...         model="m",
        ...         link=InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0, port_max_gbps=6.0),
        ...     )
        ... )
        0.6
    """
    if disk.pcie is not None:
        return disk.pcie.current_bandwidth_gbps
    return serial_bandwidth_gbps(disk.link.negotiated_gbps)


def _board_generation(controller: Controller, inventory: Inventory) -> int | None:
    """Return the highest PCIe generation any port on this board supports.

    Capability only. Whether a port is a usable connector, and whether anything
    already occupies it, are separate questions answered elsewhere: a port that
    cannot be proposed as a move target is still evidence of what the board can
    do. Conflating the two once read an occupied Gen4 port as an absent one and
    advised buying a PCIe 4.0 board for a machine that was already PCIe 5.0.
    """
    # The machine holds its fastest port, rather than every capped controller
    # walking the ports for it. Folded with the upstream last, as a max over the
    # ports followed by the upstream always folded it.
    upstream = None if controller.upstream is None else controller.upstream.max_speed_gtps
    speeds = [speed for speed in (inventory.fastest_slot_speed_gtps, upstream) if speed is not None]
    return pcie_generation(max(speeds)) if speeds else None


def _best_port_for(controller: Controller, inventory: Inventory) -> tuple[PcieSlot, float] | None:
    """Return the port on this board that would give this controller the most, with what it would give.

    Every port counts, occupied or not, for the reason :func:`_board_generation`
    gives. Each is judged by what it would give THIS controller, the lower of the
    two ends on speed and on width, never by its own capability: a PCIe 3.0 x16
    port gives a PCIe 4.0 x4 card PCIe 3.0 x4, so ranking ports by their own lanes
    calls a port faster that the card cannot use, and the fastest speed of one
    port beside the widest width of another describes a port the board does not
    have. Where two ports would give the same, the more capable one is named,
    since it says more about what the board is.
    """
    best: tuple[PcieSlot, float] | None = None
    for slot in inventory.placement_candidates(besides=controller.upstream_address, admits=_any_port):
        gain = gain_in(slot, seat_of(controller))
        if gain is None:
            continue
        if best is None or (gain, slot.capability_gbps or 0.0) > (best[1], best[0].capability_gbps or 0.0):
            best = (slot, gain)
    return best


def _any_port(_slot: PcieSlot) -> bool:
    """Admit every port, for the search that judges the board rather than a free seat."""
    return True


def _swap_slot_for(controller: Controller, inventory: Inventory) -> PcieSlot | None:
    """Find an occupied slot whose card would lose nothing by moving out.

    Only proposes a swap when the current occupant demonstrably cannot use the
    bandwidth it is sitting on, and needs less than this controller does, so the
    trade is a strict improvement rather than a shuffle.
    """
    needed = pcie_bandwidth_gbps(controller.link.max_speed_gtps, controller.link.max_width)

    def is_worthwhile(slot: PcieSlot) -> bool:
        if not slot.is_swap_candidate:
            return False
        occupant_need = slot.occupant_need_gbps
        if occupant_need is None:
            return False
        current = pcie_bandwidth_gbps(*achievable_pcie(seat_of(controller)))
        # The displaced card must fit in the slot this controller vacates.
        if current is not None and occupant_need > current:
            return False
        return needed is None or occupant_need < needed

    return best_slot(seat_of(controller), inventory, is_worthwhile)


def attached_demand_gbytes(controller: Controller, inventory: Inventory) -> float | None:
    """Sum what the drives on one controller can pull, in GB/s, counting an NVMe drive once per drive.

    Args:
        controller: The controller to total up.
        inventory: The machine the controller belongs to.

    Returns:
        Aggregate demand in GB/s, or ``None`` when there is nothing to total:
        either no drives sit on this controller, or none of them had a link
        that could be read. The second case is why the answer is not ``0.0``.
        A controller whose drives nobody could measure has not been shown to be
        idle, and the views draw a dash for ``None`` and a figure for a zero.
    """
    # Truthiness rather than `is not None`, so a demand of 0.0 is counted as
    # unread too: no link carries nothing, so a zero here is a reading that
    # failed, and letting it into the list would turn the dash into a 0.0.
    demands = [demand for disk in inventory.drives_on(controller.address) if (demand := interface_demand_gbytes(disk))]
    return round(sum(demands), 3) if demands else None


def diagnose_controller_link(controller: Controller, inventory: Inventory) -> list[Finding]:
    """Grade one controller's PCIe link against what the machine can offer.

    Args:
        controller: The controller to examine.
        inventory: The machine it sits in, used to look for a better slot.

    Returns:
        Findings, empty when the link is at the machine's ceiling.
    """
    link = controller.link
    if link.is_dead:
        return [
            Finding(
                severity=Severity.CRITICAL,
                subject=controller.address,
                title=f"{controller.name} link never trained",
                detail="The device is present on the bus but negotiated a width of zero lanes.",
                action="Reseat the card, try another slot, and check the riser or backplane.",
            )
        ]

    achievable_speed, achievable_width = achievable_pcie(seat_of(controller))
    negotiated = link.current_bandwidth_gbps
    achievable = pcie_bandwidth_gbps(achievable_speed, achievable_width)

    if negotiated is not None and achievable is not None and negotiated < achievable:
        return [
            Finding(
                severity=Severity.WARNING,
                subject=controller.address,
                title=f"{controller.name} negotiated below what this machine offers",
                detail=(
                    f"Running {format_pcie_sentence(link.current_speed_gtps, link.current_width)} "
                    f"({format_gbytes(negotiated)}) where both ends support "
                    f"{format_pcie_sentence(achievable_speed, achievable_width)} ({format_gbytes(achievable)})."
                ),
                action="Reseat the card, check the riser and cabling, and look for a slot speed override in the BIOS.",
            )
        ]

    own_max = link.max_bandwidth_gbps
    if achievable is None:
        return _unmeasured_port_finding(controller, negotiated, own_max)
    if own_max is None or achievable >= own_max:
        return []

    return [_platform_limited_finding(controller, inventory, achievable, own_max)]


def _unmeasured_port_finding(
    controller: Controller,
    negotiated: float | None,
    own_max: float | None,
) -> list[Finding]:
    """Report a shortfall that cannot be attributed, because one end was unread.

    A device below its own maximum is worth saying out loud even when the port
    was never measured - but it is not a fault, because a socket wired a
    generation below the device produces exactly this reading. Naming a cause
    here is what once sent somebody to reseat a soldered-down M.2 drive.
    """
    link = controller.link
    if negotiated is None or own_max is None or negotiated >= own_max:
        return []
    # The port's name is quoted, never parsed. Some vendors put the width and
    # generation in it, which answers exactly what the missing registers left
    # open - but it comes from a driver package, so it is evidence for a reader
    # to weigh, not a measurement to grade against.
    named = f' The port is named "{controller.upstream_name}".' if controller.upstream_name else ""
    return [
        Finding(
            severity=Severity.WARNING,
            subject=controller.address,
            title=f"{controller.name} runs below its own maximum, and the port was not measured",
            detail=(
                f"Running {format_pcie_sentence(link.current_speed_gtps, link.current_width)} "
                f"({format_gbytes(negotiated)}) where the device alone could do "
                f"{format_pcie_sentence(link.max_speed_gtps, link.max_width)} ({format_gbytes(own_max)}). "
                "What the port can carry was not readable, so this is not attributable: a socket "
                f"built one generation below the device reads exactly the same as a fault.{named}"
            ),
            action=(
                "Check what the port is specified for before suspecting the device, the cable or "
                "the slot. A full-width link at a lower speed is usually the port's ceiling."
            ),
        )
    ]


def _platform_limited_finding(
    controller: Controller,
    inventory: Inventory,
    achievable: float,
    own_max: float,
) -> Finding:
    """Build the finding for a controller capped by the machine, not by itself."""
    achievable_speed, achievable_width = achievable_pcie(seat_of(controller))
    shortfall = _slot_shortfall(controller)

    move = free_slot_for(seat_of(controller), inventory)
    if move is not None:
        advice = Finding(
            severity=Severity.WARNING,
            subject=controller.address,
            title=f"{controller.name} is in a slot narrower or slower than it needs",
            detail=(
                f"This slot gives it {format_gbytes(achievable)} and falls short on {shortfall}; "
                f"the card itself can do {format_gbytes(own_max)}."
            ),
            action=(
                f"Move it to the free slot at {move.address} "
                f"({format_pcie_sentence(move.link.max_speed_gtps, move.link.max_width)}). "
                "Check the slot is mechanically long enough or open-ended first."
            ),
        )
        unneeded = f"{controller.name} has a faster slot free, though nothing on it needs one today"
        return _graded_by_the_drives(controller, inventory, achievable, advice=advice, unneeded_title=unneeded)

    swap = _swap_slot_for(controller, inventory)
    if swap is not None:
        advice = Finding(
            severity=Severity.WARNING,
            subject=controller.address,
            title=f"{controller.name} is in a slot narrower or slower than it needs",
            detail=(
                f"This slot gives it {format_gbytes(achievable)} and falls short on {shortfall}. "
                f"The slot at {swap.address} is faster and holds a {swap.occupant_description}, which can only "
                f"use {format_gbytes(swap.occupant_need_gbps)} of the {format_gbytes(swap.capability_gbps)} "
                "it offers, so that card loses nothing in a narrower slot."
            ),
            action=(
                f"Swap the two cards over: this controller into {swap.address}, "
                f"the {swap.occupant_description} into {controller.address}."
            ),
        )
        unneeded = f"{controller.name} could swap into a faster slot, though nothing on it needs one today"
        return _graded_by_the_drives(controller, inventory, achievable, advice=advice, unneeded_title=unneeded)

    best = _best_port_for(controller, inventory)
    if best is not None and best[1] > achievable:
        port, gain = best
        port_pcie = format_pcie_sentence(port.link.max_speed_gtps, port.link.max_width)
        detail = (
            f"The card is {format_pcie_sentence(controller.link.max_speed_gtps, controller.link.max_width)} capable; "
            f"the port it sits in gives it {format_pcie_sentence(achievable_speed, achievable_width)}, short on "
            f"{shortfall}. This board has faster ports ({port_pcie}) but none of them is free or swappable. "
            f"{_headroom_sentence(controller, inventory, achievable)}"
        )
        action = (
            f"Freeing a {port_pcie} port would take this link to {format_gbytes(gain)}; "
            "the board itself does not need replacing."
        )
    else:
        detail = (
            f"The card is {format_pcie_sentence(controller.link.max_speed_gtps, controller.link.max_width)} capable; "
            f"the port it sits in gives it {format_pcie_sentence(achievable_speed, achievable_width)}, short on "
            f"{shortfall}, and no port on this board would give it more. "
            f"{_headroom_sentence(controller, inventory, achievable)}"
        )
        action = _upgrade_sentence(controller, inventory, achievable, own_max)

    return Finding(
        severity=Severity.HINT,
        subject=controller.address,
        title=f"{controller.name} is capped by the mainboard, not by itself",
        detail=detail,
        action=action,
    )


def _graded_by_the_drives(
    controller: Controller,
    inventory: Inventory,
    achievable: float,
    *,
    advice: Finding,
    unneeded_title: str,
) -> Finding:
    """Keep a move or a swap a warning only where the drives on the controller would notice it.

    A faster seat is worth opening the machine for when what is attached can use
    it. Where the drives fit the slot the controller already has, the same advice
    becomes a hint that says so and keeps the faster slot named for when more
    drives are added.
    """
    if _drives_would_notice(controller, inventory, achievable):
        return advice
    return advice.with_changes(
        severity=Severity.HINT,
        title=unneeded_title,
        detail=f"{advice.detail} {_headroom_sentence(controller, inventory, achievable)}",
        action=f"Only once more drives are added. {advice.action}",
    )


def _peak_demand_gbytes(disk: Disk) -> float | None:
    """Return the most one disk can pull, in GB/s, which is what a faster seat is judged by.

    A PCIe drive's running link can drop while it is idle and retrain when work
    arrives, so what its link can carry is read rather than the figure it rests
    at: judged by the resting figure, a drive that fills its link under load
    reads as one that would not notice a faster one. A serial link does not idle
    down that way, so a SATA or SAS drive's negotiated rate is its peak.

    Example:
        >>> from lsdsk.domain.models import Disk, PcieLink
        >>> resting = Disk(
        ...     node="nvme0n1",
        ...     path="/dev/nvme0n1",
        ...     model="m",
        ...     pcie=PcieLink(current_speed_gtps=2.5, current_width=4, max_speed_gtps=8.0, max_width=4),
        ... )
        >>> format_gbytes(_peak_demand_gbytes(resting))
        '3.94 GB/s'
    """
    if disk.pcie is not None:
        return disk.pcie.max_bandwidth_gbps
    return interface_demand_gbytes(disk)


def _drive_peaks(controller: Controller, inventory: Inventory) -> tuple[list[float], int]:
    """Return the peak demand of each attached drive whose link was read, and how many were not read."""
    peaks = [_peak_demand_gbytes(disk) for disk in inventory.drives_on(controller.address)]
    known = [peak for peak in peaks if peak]
    return known, len(peaks) - len(known)


def _unread_demand_sentence(unread: int, count: int) -> str:
    """Say that what some attached drives can pull was not read, so whether they feel a cap is unknown.

    Example:
        >>> _unread_demand_sentence(1, 1)
        'What the attached drive can pull was not read, so whether it feels this cap is not known.'
        >>> _unread_demand_sentence(1, 2)
        'What 1 of the 2 attached drives can pull was not read, so whether they feel this cap is not known.'
    """
    if count == 1:
        return "What the attached drive can pull was not read, so whether it feels this cap is not known."
    which = f"the {count} attached drives" if unread == count else f"{unread} of the {count} attached drives"
    return f"What {which} can pull was not read, so whether they feel this cap is not known."


def _drives_would_notice(controller: Controller, inventory: Inventory, achievable: float) -> bool:
    """Whether the drives on a controller could use more than the seat it sits in gives.

    Nothing attached can use nothing. A drive whose link was not read cannot be
    shown to fit, so it counts as one that would notice: calling a move unneeded
    on a reading nobody took would bury advice that may be real.
    """
    known, unread = _drive_peaks(controller, inventory)
    if unread:
        return True
    return bool(known) and sum(known) >= achievable


def _slot_shortfall(controller: Controller) -> str:
    """Name the dimension in which a controller's slot falls short."""
    if controller.upstream is None:
        return "bandwidth"
    return controller.upstream.shortfall_against(controller.link) or "bandwidth"


def _headroom_sentence(controller: Controller, inventory: Inventory, achievable: float) -> str:
    """Say whether the attached drives can actually feel the cap, judged by the most they can pull.

    A drive whose link was not read is named as unknown rather than left out:
    leaving it out calls a controller with a drive on it empty, or lets the
    drives that were read stand for all of them.
    """
    known, unread = _drive_peaks(controller, inventory)
    count = len(known) + unread
    if not count:
        return "Nothing is attached to it yet."
    if unread:
        return _unread_demand_sentence(unread, count)
    demand = round(sum(known), 3)
    if demand < achievable:
        needed = f"The {count} attached drives need" if count > 1 else "The attached drive needs"
        return (
            f"{needed} about {format_gbytes(demand)}, so this link is not the bottleneck today; revisit if more "
            "drives are added."
        )
    wanted = "The attached drives already want" if count > 1 else "The attached drive already wants"
    return f"{wanted} about {format_gbytes(demand)}, at or beyond this link."


def _upgrade_sentence(controller: Controller, inventory: Inventory, achievable: float, own_max: float) -> str:
    """Say which upgrade would lift the cap, and by how much.

    A board is named only where no port on this one reaches the card's own
    generation, and it is named at that generation, because that is the one
    that delivers the figure quoted. Where the board has the generation but no
    port that gives this card more, the card needs one port with both its speed
    and its width, and that is what is named: a newer board or a wider slot
    alone points at ports this board already has.
    """
    own_generation = pcie_generation(controller.link.max_speed_gtps)
    board_generation = _board_generation(controller, inventory)
    if board_generation is not None and own_generation is not None and own_generation > board_generation:
        return (
            f"A PCIe {own_generation}.0 board would take this link from {format_gbytes(achievable)} "
            f"to {format_gbytes(own_max)}."
        )
    wanted = format_pcie_sentence(controller.link.max_speed_gtps, controller.link.max_width)
    return f"A {wanted} port would take this link from {format_gbytes(achievable)} to {format_gbytes(own_max)}."


class _UsbCeiling(NamedTuple):
    """The slowest thing in front of a USB disk, and whether it is a hub rather than the socket."""

    speed: UsbSpeed
    hub: bool


def diagnose_usb_link(disk: Disk) -> list[Finding]:
    """Grade a USB disk's link to the machine, the way every other link is graded.

    The drive's own link behind the bridge is `diagnose_disk_link`'s; this rule
    judges only the USB side, in the order the ADR fixes: a SuperSpeed disk that
    came up at USB 2 speed, a link below what both read ends support, a socket
    or hub that is the ceiling, and a shortfall whose port was never read. Every
    one of them compares against what the device declares, so a device whose
    capability was not read raises nothing.

    Args:
        disk: The disk to judge.

    Returns:
        At most one finding.

    Example:
        >>> diagnose_usb_link(Disk(node="sda", path="/dev/sda", model="m"))
        []
    """
    link = disk.usb
    if link is None or link.running is None or link.device_max is None:
        return []
    running, device = link.running, link.device_max
    if link.fell_back_to_usb2:
        return [_usb2_fallback(disk, running, device)]
    achievable = link.achievable
    if achievable is not None and link.is_underperforming:
        return [_usb_link_fault(disk, running, achievable)]
    ceiling = _usb_ceiling(link, device)
    if ceiling is not None:
        return [_usb_capped(disk, device, ceiling)]
    if link.port_max is None and running.bandwidth_gbps < device.bandwidth_gbps:
        return [_usb_unattributed(disk, running, device)]
    return []


def _usb_ceiling(link: UsbLink, device: UsbSpeed) -> _UsbCeiling | None:
    """The socket or hub slower than the device's own capability, the slowest of them first."""
    candidates = [
        _UsbCeiling(speed, hub) for speed, hub in ((link.port_max, False), (link.upstream, True)) if speed is not None
    ]
    if not candidates:
        return None
    slowest = min(candidates, key=lambda ceiling: ceiling.speed.bandwidth_gbps)
    if slowest.speed.bandwidth_gbps >= device.bandwidth_gbps:
        return None
    return slowest


def _usb_cap_noticed(disk: Disk, ceiling: UsbSpeed) -> bool:
    """Whether the drive behind the bridge can pull more than the ceiling carries; unread counts as yes."""
    demand = interface_demand_gbytes(disk)
    return demand is None or demand > ceiling.bandwidth_gbps


def _usb_headroom(disk: Disk, ceiling: UsbSpeed) -> str:
    """Say whether the drive behind the bridge can feel the cap, judged by what its own link pulls."""
    demand = interface_demand_gbytes(disk)
    carries = format_gbytes(ceiling.bandwidth_gbps)
    if demand is None:
        return "What the drive behind the bridge can pull was not read, so whether it feels this cap is not known."
    if demand > ceiling.bandwidth_gbps:
        return (
            f"The drive behind the bridge can pull {format_gbytes(demand)}, "
            f"more than {ceiling.figure} carries ({carries})."
        )
    return (
        f"The drive behind the bridge pulls about {format_gbytes(demand)}, which {ceiling.figure} "
        "already carries, so this cap costs nothing today."
    )


def _usb2_fallback(disk: Disk, running: UsbSpeed, device: UsbSpeed) -> Finding:
    """The finding for a SuperSpeed disk that came up on the USB 2 half of a USB 3 socket."""
    return Finding(
        severity=Severity.WARNING,
        subject=disk.path,
        title=f"{disk.model} is running at {running.figure} on the USB 2 side of a USB 3 port",
        detail=(
            f"The drive can do {device.figure} and the port has a USB 3 side, but the link came up at "
            "USB 2 speed. That is almost always a USB 2 cable or extension, a USB 2 hub in between, or a plug "
            "that is not fully seated."
        ),
        action="Connect it with a USB 3 cable, seated fully, with no USB 2 hub or extension in between.",
    )


def _usb_link_fault(disk: Disk, running: UsbSpeed, achievable: UsbSpeed) -> Finding:
    """The finding for a link below what the device, the socket and any hub above all support."""
    return Finding(
        severity=Severity.WARNING,
        subject=disk.path,
        title=f"{disk.model} is running at {running.figure} but both ends support {achievable.figure}",
        detail=(
            "Both the drive and the port it is in were read, so the slower link is a fault of what connects "
            "them: the cable, a hub, or the plug."
        ),
        action="Reseat the plug and try another cable before suspecting the drive.",
    )


def _usb_capped(disk: Disk, device: UsbSpeed, ceiling: _UsbCeiling) -> Finding:
    """The finding for a socket or hub that holds the disk below its own capability."""
    if ceiling.hub:
        where = f"a hub between it and the machine runs at {ceiling.speed.figure}"
        action = f"Connect it directly to the machine, or through a hub that runs at {device.figure}."
    else:
        where = f"its port only offers {ceiling.speed.figure}"
        action = f"A port that offers {device.figure} would recover the difference."
    return Finding(
        severity=Severity.WARNING if _usb_cap_noticed(disk, ceiling.speed) else Severity.HINT,
        subject=disk.path,
        title=f"{disk.model} can do {device.figure} but {where}",
        detail=_usb_headroom(disk, ceiling.speed),
        action=action,
    )


def _usb_unattributed(disk: Disk, running: UsbSpeed, device: UsbSpeed) -> Finding:
    """The finding for a shortfall whose port was never read, which is not yet a fault."""
    return Finding(
        severity=Severity.WARNING,
        subject=disk.path,
        title=f"{disk.model} is running at {running.figure}, below its own {device.figure}",
        detail=(
            "What the port can carry was not read, so this is not yet a fault: a port that only offers "
            f"{running.figure} explains it exactly as well as a bad cable does."
        ),
        action="Establish what the port offers before treating it as a link fault.",
    )


def diagnose_disk_link(disk: Disk, inventory: Inventory) -> list[Finding]:
    """Grade one disk's interface speed against both ends' capability.

    Args:
        disk: The disk to examine.
        inventory: The machine, used to suggest a faster free port.

    Returns:
        Findings, empty when the disk runs as fast as the pairing allows.
    """
    link = disk.link
    if link.is_underperforming:
        return [
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} is linked at {link.negotiated_gbps:g} Gb/s but both ends support "
                f"{link.achievable_gbps:g} Gb/s",
                detail="A link that trains below both ends' capability is almost always the cable, the backplane "
                "slot, or a marginal connector.",
                action="Reseat the drive, try another bay, and replace the cable before suspecting the drive.",
            )
        ]
    if link.is_below_drive_capability and link.port_max_gbps is None:
        return [
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} is linked at {link.negotiated_gbps:g} Gb/s, below its own "
                f"{link.drive_max_gbps:g} Gb/s",
                detail="What the port can carry was not read, so this is not yet a fault: a port that only offers "
                f"{link.negotiated_gbps:g} Gb/s explains it exactly as well as a bad cable does.",
                action="Establish the port's capability first, then treat it as a link fault only if the port is "
                "the faster of the two.",
            )
        ]
    if not link.is_port_limited:
        return []

    faster = _faster_free_port(disk, inventory)
    if faster is not None:
        return [
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} is on a port slower than the drive",
                detail=f"The port tops out at {link.port_max_gbps:g} Gb/s; the drive can do "
                f"{link.drive_max_gbps:g} Gb/s.",
                action=f"Move it to {faster}, which has a free port at the drive's full speed.",
            )
        ]
    return [
        Finding(
            severity=Severity.HINT,
            subject=disk.path,
            title=f"{disk.model} is held back by its controller",
            detail=f"The port tops out at {link.port_max_gbps:g} Gb/s; the drive can do "
            f"{link.drive_max_gbps:g} Gb/s, and no faster port is free in this machine.",
            action=f"A host bus adapter with {link.drive_max_gbps:g} Gb/s phys would recover the difference.",
        )
    ]


def _faster_free_port(disk: Disk, inventory: Inventory) -> str | None:
    """Find a controller with a free port faster than the disk's current one."""
    port_max = disk.link.port_max_gbps
    drive_max = disk.link.drive_max_gbps
    if port_max is None or drive_max is None:
        return None
    # The machine indexes its controllers by their fastest port, rather than
    # every capped drive walking all of them.
    found = inventory.first_controller_with_a_free_port_faster_than(port_max, besides=disk.controller_address)
    return None if found is None else f"{found.name} at {found.address}"


def diagnose_port_allocation(inventory: Inventory) -> list[Finding]:
    """Find drives sitting in the wrong seats.

    A drive running at its own maximum is not a fault, so nothing alerts on it,
    and that is exactly how a machine ends up with an old 3 Gb/s drive occupying
    its only 6 Gb/s port while a 6 Gb/s drive runs at half speed on a slow one.
    Both drives are individually fine. The arrangement is not, and swapping them
    costs nothing but a cable.

    Only proposed when the trade is a strict improvement: the drive giving up
    the fast port must not be able to use it anyway.

    Args:
        inventory: The machine to examine.

    Returns:
        One finding per swap worth making.
    """
    starved = [disk for disk in inventory.disks if disk.link.is_port_limited]
    findings: list[Finding] = []
    # Indexed once for the machine rather than searched once per starved drive:
    # a scan of every candidate for every starved drive makes the rule cost the
    # product of the two, and a replay is untrusted input that can carry
    # thousands of each.
    partners = _SwapPartners(_drives_holding_more_port_than_they_can_use(inventory))

    for disk in starved:
        port_max, drive_max = disk.link.port_max_gbps, disk.link.drive_max_gbps
        if port_max is None or drive_max is None:
            continue
        partner = partners.take(needs=drive_max, offers=port_max)
        if partner is None:
            continue
        findings.append(
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} and {partner.model} are in the wrong ports",
                detail=(
                    f"{disk.path} can do {drive_max:g} Gb/s but sits on a {port_max:g} Gb/s port, while "
                    f"{partner.path} tops out at {partner.link.drive_max_gbps:g} Gb/s and is holding a "
                    f"{partner.link.port_max_gbps:g} Gb/s one it cannot use."
                ),
                action=f"Swap the two drives over. {partner.path} loses nothing and {disk.path} gains.",
            )
        )
    return findings


class _Holder(NamedTuple):
    """A drive that holds more port than it can use, with both figures read.

    Attributes:
        disk: The drive.
        drive_max: The fastest it can go.
        port_max: The fastest the port it holds can go, above ``drive_max``.
    """

    disk: Disk
    drive_max: float
    port_max: float


def _drives_holding_more_port_than_they_can_use(inventory: Inventory) -> tuple[_Holder, ...]:
    """Narrow the machine to the drives that could ever be the partner in a swap.

    The condition is the part of the swap test that depends on the candidate
    alone: both figures read, and the drive slower than the port it occupies.
    Everything else the test asks is about the pair.

    Args:
        inventory: The machine to search.

    Returns:
        The drives worth testing against a starved one, in inventory order.
    """
    return tuple(
        _Holder(disk, disk.link.drive_max_gbps, disk.link.port_max_gbps)
        for disk in inventory.disks
        if disk.link.drive_max_gbps is not None
        and disk.link.port_max_gbps is not None
        and disk.link.drive_max_gbps < disk.link.port_max_gbps
    )


class _FirstPortAtLeast:
    """Holders of one drive speed, naming the first whose port is fast enough.

    A max tree over their port speeds in inventory order: the first port of at
    least a given speed is found by stepping into the left half whenever that
    half holds one, so a question costs the height of the tree rather than a
    walk along the list. A taken holder's leaf drops to minus infinity, which no
    starved drive's need can meet - that need is above the port the drive sits
    on, so it is above minus infinity.

    Example:
        >>> ports = _FirstPortAtLeast(positions=(4, 7, 9), ports=(3.0, 12.0, 6.0))
        >>> ports.first_at_least(6.0)
        7
        >>> ports.remove(1)
        >>> ports.first_at_least(6.0)
        9
        >>> ports.first_at_least(24.0) is None
        True
    """

    def __init__(self, positions: Sequence[int], ports: Sequence[float]) -> None:
        """Build the tree over one group of holders.

        Args:
            positions: Each holder's place among all holders, in inventory order.
            ports: Each holder's port speed, in the same order.
        """
        self._positions = tuple(positions)
        self._leaves = 1 << max(len(ports) - 1, 0).bit_length()
        self._best = [-math.inf] * (2 * self._leaves)
        self._best[self._leaves : self._leaves + len(ports)] = ports
        for node in range(self._leaves - 1, 0, -1):
            self._best[node] = max(self._best[2 * node], self._best[2 * node + 1])

    def first_at_least(self, needs: float) -> int | None:
        """The place of the first untaken holder whose port gives ``needs``, or ``None``."""
        # Minus infinity marks a taken or padding leaf, so a need that low would
        # land on one. No starved drive needs that little, and NaN meets nothing.
        if not self._best[1] >= needs > -math.inf:
            return None
        node = 1
        while node < self._leaves:
            node = 2 * node if self._best[2 * node] >= needs else 2 * node + 1
        return self._positions[node - self._leaves]

    def remove(self, leaf: int) -> None:
        """Take one holder out, by its index within this group."""
        node = self._leaves + leaf
        self._best[node] = -math.inf
        while node > 1:
            node //= 2
            self._best[node] = max(self._best[2 * node], self._best[2 * node + 1])


class _SwapPartners:
    """Every possible swap partner, indexed so a starved drive finds its match without a scan.

    The answer is the one a first-match scan in inventory order gives: the
    EARLIEST untaken holder whose port gives the starved drive what it needs
    and whose own speed fits the port it would be handed. The holders are
    grouped by their own speed, which SATA's IDENTIFY decoding allows three
    values of, and each group answers "first port fast enough" from a tree, so
    a question costs a tree walk per group rather than a read per holder.

    A promise is made per NODE, as the scan's ``taken`` set made it: a capture
    is untrusted and can carry two drives of one name, and promising one of
    them promises both.
    """

    def __init__(self, holders: Sequence[_Holder]) -> None:
        """Group and index the holders.

        Args:
            holders: The possible partners, from
                :func:`_drives_holding_more_port_than_they_can_use`.
        """
        self._holders = tuple(holders)
        grouped: dict[float, list[int]] = {}
        for position, holder in enumerate(self._holders):
            grouped.setdefault(holder.drive_max, []).append(position)
        groups: list[tuple[float, _FirstPortAtLeast]] = []
        self._leaf_of: dict[int, tuple[_FirstPortAtLeast, int]] = {}
        for drive in sorted(grouped):
            members = grouped[drive]
            tree = _FirstPortAtLeast(members, [self._holders[at].port_max for at in members])
            groups.append((drive, tree))
            self._leaf_of.update({at: (tree, leaf) for leaf, at in enumerate(members)})
        self._groups = tuple(groups)
        self._by_node: dict[str, list[int]] = {}
        for position, holder in enumerate(self._holders):
            self._by_node.setdefault(holder.disk.node, []).append(position)

    def take(self, *, needs: float, offers: float) -> Disk | None:
        """Promise the first fitting partner to a starved drive, or find none.

        Args:
            needs: The speed the starved drive wants from the port it would take.
            offers: The speed of the port the starved drive would hand over.

        Returns:
            A drive that would lose nothing by trading places, or ``None``.
        """
        found = self._first_fitting(needs=needs, offers=offers)
        if found is None:
            return None
        partner = self._holders[found].disk
        for position in self._by_node.pop(partner.node):
            tree, leaf = self._leaf_of[position]
            tree.remove(leaf)
        return partner

    def _first_fitting(self, *, needs: float, offers: float) -> int | None:
        """The earliest place, across every group whose speed fits ``offers``."""
        answers = (tree.first_at_least(needs) for drive, tree in self._groups if drive <= offers)
        return min((place for place in answers if place is not None), default=None)


#: The severities most urgent first, which is `Severity`'s own declaration
#: order and is load-bearing: a step is taken along THIS tuple, and the ranking
#: a report sorts by is a position in it. Written out member by member, each of
#: the three maps below was a list of the members that existed when it was
#: written, so a member added later was a `KeyError` out of two public entry
#: points - not a type error, so nothing could see it coming.
#: `test_every_severity_can_take_a_step_in_both_directions` holds this. Public
#: with `one_step_in_severity` because the tests that tie them to the enum sit
#: outside this module, and pyright refuses a private name reached from there.
SEVERITY_RANKING: tuple[Severity, ...] = tuple(Severity)


def one_step_in_severity(severity: Severity, *, towards_urgent: bool) -> Severity:
    """Move a severity one step along the ranking, and never past its end.

    A measured rate moves a finding by exactly one step, never more. History
    refines a judgement that the counters already justified; it never
    manufactures one, and it never jumps a hint straight to critical.

    Args:
        severity: What the rule decided without the counters.
        towards_urgent: Whether the measurement supports the finding or weakens
            it.

    Returns:
        The neighbouring severity, or `severity` itself at either end.

    Example:
        >>> one_step_in_severity(Severity.HINT, towards_urgent=True)
        <Severity.WARNING: 'warning'>
        >>> one_step_in_severity(Severity.CRITICAL, towards_urgent=True)
        <Severity.CRITICAL: 'critical'>
    """
    step = -1 if towards_urgent else 1
    moved = SEVERITY_RANKING.index(severity) + step
    return SEVERITY_RANKING[min(max(moved, 0), len(SEVERITY_RANKING) - 1)]


def _trend(series: DiskSeries | None, kind: CounterKind, thresholds: Thresholds) -> Trend | None:
    """The trend for one counter, or ``None`` when nothing has been recorded."""
    return None if series is None else trend_for(series, kind, thresholds)


def refine(finding: Finding, trend: Trend | None) -> Finding:
    """Let a measured rate move a finding, and say what the measurement was.

    Without history, and whenever the samples cannot support a verdict, the
    finding is returned untouched. That is what keeps a first run, an
    unprivileged run and a run whose samples sit too close together reporting
    exactly what this tool reported before any of this existed.

    Args:
        finding: The finding the counters alone justified.
        trend: What the recorded samples say, if anything.

    Returns:
        The finding, possibly one severity step up or down, with the
        measurement appended to its detail.

    Example:
        >>> from .history import CounterKind, Trend, TrendVerdict
        >>> base = Finding(severity=Severity.WARNING, subject="/dev/sdd", title="has errors")
        >>> rising = Trend(
        ...     kind=CounterKind.CRC_ERRORS,
        ...     verdict=TrendVerdict.RISING,
        ...     latest=900,
        ...     delta=200,
        ...     span_hours=10,
        ...     per_hour=20.0,
        ...     expected_from_lifetime=None,
        ... )
        >>> refine(base, rising).severity
        <Severity.CRITICAL: 'critical'>
        >>> refine(base, None).severity
        <Severity.WARNING: 'warning'>
    """
    if trend is None:
        return finding
    if trend.is_rising and trend.per_hour is not None and trend.span_hours:
        rate = f"{trend.per_hour:.1f}" if trend.per_hour < 10 else f"{trend.per_hour:.0f}"  # noqa: PLR2004 - a decimal is noise above ten an hour
        measured = (
            f" It gained {trend.delta} in the last {trend.span_hours} power-on hours, "
            f"about {rate} an hour, so this is happening now rather than in the past."
        )
        return finding.with_changes(
            severity=one_step_in_severity(finding.severity, towards_urgent=True),
            detail=f"{finding.detail}{measured}",
        )
    if trend.is_quiet and trend.span_hours and trend.expected_from_lifetime is not None:
        measured = (
            f" None of them are recent: the count has not moved in {trend.span_hours} power-on hours, "
            f"and this drive's own lifetime rate predicted about {trend.expected_from_lifetime:.0f} "
            "in that time. Whatever caused them is not doing so now."
        )
        return finding.with_changes(
            severity=one_step_in_severity(finding.severity, towards_urgent=False),
            detail=f"{finding.detail}{measured}",
        )
    return finding


def diagnose_health(
    disk: Disk,
    series: DiskSeries | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[Finding]:
    """Turn one disk's health readings into findings.

    Args:
        disk: The disk to examine.
        series: What has been recorded for this disk before, if anything. With
            no series every rule behaves exactly as it did before history
            existed.
        thresholds: The judgement values to weigh against.

    Returns:
        Findings, empty when the disk reports nothing wrong.
    """
    health = disk.health
    if health is None:
        return []
    findings: list[Finding] = []
    findings.extend(_failed_selftest_findings(disk))
    findings.extend(_failing_attribute_findings(disk))
    findings.extend(_wear_findings(disk, _trend(series, CounterKind.PERCENT_USED, thresholds), thresholds))
    findings.extend(_sector_findings(disk, series, thresholds))
    findings.extend(_crc_findings(disk, _trend(series, CounterKind.CRC_ERRORS, thresholds), thresholds))
    findings.extend(_temperature_findings(disk))
    return findings


def _failing_attribute_findings(disk: Disk) -> list[Finding]:
    """Report an attribute the drive's own maker says has fallen below its limit.

    This is the manufacturer's verdict, not a number this tool invented: the
    normalised value has reached the threshold the drive itself publishes. It is
    a different statement from the overall self-assessment, which many drives
    keep at PASSED while individual attributes are already under their limits,
    and from the raw-count rules, which judge specific counters against figures
    chosen here.

    Without this the condition rendered as a red table row and nothing else, so
    it never reached ``findings``, never set an exit code, and vanished entirely
    down a pipe or on a NO_COLOR terminal.
    """
    health = disk.health
    if health is None:
        return []
    failing = [attribute for attribute in health.attributes if attribute.is_failing]
    if not failing:
        return []
    named = ", ".join(
        f"{attribute.name or attribute.id} at {attribute.value}/{attribute.threshold}" for attribute in failing
    )
    plural = "attributes" if len(failing) > 1 else "attribute"
    return [
        Finding(
            severity=Severity.CRITICAL,
            subject=disk.path,
            title=f"{disk.model} reports {len(failing)} SMART {plural} below the maker's own threshold",
            detail=f"Normalised value at or under the published limit: {named}.",
            action="Treat the drive as failing: check the backup, and replace it rather than investigating further.",
        )
    ]


def _failed_selftest_findings(disk: Disk) -> list[Finding]:
    """Report an overall SMART self-assessment that says the drive is failing."""
    health = disk.health
    if health is None or health.ok is not False:
        return []
    return [
        Finding(
            severity=Severity.CRITICAL,
            subject=disk.path,
            title=f"{disk.model} reports itself as failing",
            detail="The drive's own overall health self-assessment has failed.",
            action="Replace it now and verify your backups before doing anything else.",
        )
    ]


def _wear_projection(trend: Trend | None, used: int, thresholds: Thresholds) -> str:
    """Say when wear reaches 100% at the rate actually measured.

    Wear rises on every healthy drive, so a rising counter is not a fault here
    and the trend is used to date the end rather than to raise the severity.
    """
    if trend is None or not trend.is_rising or trend.per_hour is None:
        return ""
    if trend.delta is None or trend.delta < thresholds.wear_projection_min_points:
        return ""
    remaining_hours = (100 - used) / trend.per_hour
    if remaining_hours <= 0:
        return ""
    years = remaining_hours / (365 * 24)
    if years >= 1:
        return f" At the rate measured here it reaches 100% in about {years:.1f} years of power-on time."
    return f" At the rate measured here it reaches 100% in about {remaining_hours / 24:.0f} days of power-on time."


def _wear_findings(
    disk: Disk,
    trend: Trend | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[Finding]:
    """Report a drive approaching or past its rated write endurance."""
    health = disk.health
    if health is None or health.percent_used is None:
        return []
    used = health.percent_used
    if used < thresholds.wear_warning_percent:
        return []
    severity = Severity.CRITICAL if used >= thresholds.wear_critical_percent else Severity.WARNING
    written = f", {health.bytes_written / 1e12:.1f} TB written" if health.bytes_written else ""
    return [
        Finding(
            severity=severity,
            subject=disk.path,
            title=f"{disk.model} is {used}% through its rated endurance",
            detail=f"Wear indicator at {used} of 100{written}.{_wear_projection(trend, used, thresholds)}",
            action="Plan the replacement now; endurance beyond 100% is not a cliff but the warranty ends there.",
        )
    ]


def _sector_findings(
    disk: Disk,
    series: DiskSeries | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[Finding]:
    """Report reallocated, pending or uncorrectable sectors."""
    health = disk.health
    if health is None:
        return []
    findings: list[Finding] = []
    if health.reallocated_sectors:
        findings.append(
            refine(
                Finding(
                    severity=Severity.WARNING,
                    subject=disk.path,
                    title=f"{disk.model} has {health.reallocated_sectors} reallocated sectors",
                    detail="Sectors have been retired to the spare pool, which means the media is degrading.",
                    action="Watch the count. A number that climbs between scans means replace it.",
                ),
                _trend(series, CounterKind.REALLOCATED_SECTORS, thresholds),
            )
        )
    if health.pending_sectors:
        findings.append(
            refine(
                Finding(
                    severity=Severity.CRITICAL,
                    subject=disk.path,
                    title=f"{disk.model} has {health.pending_sectors} sectors pending reallocation",
                    detail="These sectors could not be read and are waiting for a write to decide their fate.",
                    action="Back up now, then rewrite the affected area or replace the drive.",
                ),
                _trend(series, CounterKind.PENDING_SECTORS, thresholds),
            )
        )
    if health.uncorrectable_sectors:
        findings.append(
            refine(
                Finding(
                    severity=Severity.CRITICAL,
                    subject=disk.path,
                    title=f"{disk.model} has {health.uncorrectable_sectors} uncorrectable sectors",
                    detail="Data in these sectors was lost and could not be recovered by the drive.",
                    action="Replace the drive and restore the affected data from backup.",
                ),
                _trend(series, CounterKind.UNCORRECTABLE_SECTORS, thresholds),
            )
        )
    if health.media_errors:
        findings.append(
            refine(
                Finding(
                    severity=Severity.WARNING,
                    subject=disk.path,
                    title=f"{disk.model} logged {health.media_errors} media errors",
                    detail="The controller could not recover these data integrity errors.",
                    action="Watch the count across scans; a rising number means replace it.",
                ),
                _trend(series, CounterKind.MEDIA_ERRORS, thresholds),
            )
        )
    return findings


def _crc_findings(
    disk: Disk,
    trend: Trend | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[Finding]:
    """Report frames corrupted on the interface and retransmitted.

    These are the one health counter that is not about the media at all. The
    drive is fine; the path to it is not, so the remedy is a cable, a connector
    or a backplane slot rather than a replacement drive. SATA also downshifts a
    link that keeps erroring, so a high count next to a link running below both
    ends is the same fault seen twice.
    """
    health = disk.health
    if health is None or not health.crc_errors:
        return []

    count = health.crc_errors
    downshifted = disk.link.is_underperforming
    if count < thresholds.crc_errors_significant and not downshifted:
        return [
            refine(
                Finding(
                    severity=Severity.HINT,
                    subject=disk.path,
                    title=f"{disk.model} has logged {count} interface CRC errors",
                    detail="A small count can come from a single hotplug or a reboot during a transfer.",
                    action="Note the number and compare it at the next scan. Only a rising count matters.",
                ),
                trend,
            )
        ]

    detail = (
        f"{count} frames were corrupted in transit and had to be resent. This is the cable, the "
        "connector or the backplane slot, not the drive: the media is untouched by it."
    )
    if downshifted:
        detail += " The link is also running below what both ends support, which is what SATA does when a "
        detail += "connection keeps erroring, so both symptoms point at the same physical path."
    return [
        refine(
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} has {count} interface CRC errors",
                detail=detail,
                action=(
                    "Reseat or replace the cable and try another bay. Replacing the drive changes nothing."
                    if trend is None or not trend.is_quiet
                    else "Nothing is being corrupted now, so replacing anything would fix a fault that is over."
                ),
            ),
            trend,
        )
    ]


def _temperature_findings(disk: Disk) -> list[Finding]:
    """Report a drive above its own vendor-declared temperature thresholds."""
    health = disk.health
    if health is None or health.temperature_c is None:
        return []
    temperature = health.temperature_c
    critical = health.temperature_critical_c
    warning = health.temperature_warning_c
    if critical is not None and temperature >= critical:
        return [
            Finding(
                severity=Severity.CRITICAL,
                subject=disk.path,
                title=f"{disk.model} is at {temperature} C, its own critical limit is {critical} C",
                detail="The drive is above the temperature its maker declares as critical.",
                action="Improve airflow now. Sustained overheating shortens life and triggers throttling.",
            )
        ]
    if warning is not None and temperature >= warning:
        return [
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} is at {temperature} C, above its {warning} C warning threshold",
                detail="The drive is above the temperature its maker declares as the warning point.",
                action="Check airflow and drive spacing.",
            )
        ]
    return []


def diagnose_firmware_consistency(
    inventory: Inventory,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[Finding]:
    """Report identical models running different firmware revisions.

    Args:
        inventory: The machine to check.
        thresholds: The judged figures, passed in so the domain reads no configuration itself.

    Returns:
        One finding per model that is not uniform.
    """
    by_model: dict[str, dict[str, list[str]]] = {}
    for disk in inventory.disks:
        if not disk.firmware:
            continue
        by_model.setdefault(disk.model, {}).setdefault(disk.firmware, []).append(disk.path)

    findings: list[Finding] = []
    for model, revisions in sorted(by_model.items()):
        if len(revisions) < thresholds.mixed_firmware_threshold:
            continue
        spread = ", ".join(f"{revision} on {len(paths)}" for revision, paths in sorted(revisions.items()))
        findings.append(
            Finding(
                severity=Severity.HINT,
                subject=model,
                title=f"{model} runs {len(revisions)} different firmware revisions",
                detail=f"Revisions in use: {spread}.",
                action="Level them up. Mixed firmware in one pool makes performance and bugs hard to attribute.",
            )
        )
    return findings


def _port_holding(controller: Controller, inventory: Inventory) -> PcieSlot | None:
    """Return the port a controller sits behind, or ``None`` when it names none.

    Joined from the CONTROLLER's side, by the address of the port above it. The
    port's side cannot answer this: a port record names only one of the devices
    behind it, so asking which port names this controller finds nothing whenever
    it shares its port with a device that represents it. Measured on a committed
    capture, that made the controller carrying the machine's only disk sit on no
    port at all, and the integrated-function hint it should have earned reverted
    to a warning telling the owner to replace a working card.
    """
    if controller.upstream_address is None:
        return None
    return inventory.slot_at(controller.upstream_address)


def _floor_twin(controller: Controller, inventory: Inventory) -> PcieSlot | None:
    """Find the port that shows a controller's floor link to be a register default.

    A desktop chipset used as a PCIe switch publishes 2.5 GT/s x1, as both
    running and capable, on the SATA and USB functions built into the chip,
    whatever its real uplink is. Measured: a review of one such expansion card
    put about 1.7 GB/s through four SATA drives, where reading the register as
    a ceiling claimed 0.25 GB/s and told the owner to replace the card.

    The floor value alone cannot settle it, because a dead or downtrained link
    reads the same. Three readings together can, all on one switch: the
    controller at the floor, a second device at the identical floor, and another
    running a link above the floor, which shows the switch passes real links on rather
    than being narrow everywhere. Where every device reads the floor, nothing
    tells the two cases apart, and ports on a root bus are independent slots
    rather than one switch, so neither case is excused.

    The two at the floor must also be functions of the switch itself: each
    carries the vendor identifier of the port in front of it, and that is the
    same maker for both. A separate part genuinely linked at 2.5 GT/s x1 reads
    exactly the same floor - a network controller and a FireWire controller
    measured on real boards each do - so behind a chipset switch, beside a SATA
    card that really is Gen1 x1, it would complete the pattern and hide a real
    bottleneck behind a hint.

    Args:
        controller: The controller whose link is in question.
        inventory: The machine, whose port records place it on a switch.

    Returns:
        The port holding the second device at the floor, which is the evidence
        a reader can check, or ``None`` when any of the three readings is
        missing, when either device at the floor is not the switch maker's own
        function, or when the controller's port is not provably behind a
        bridge, including a scan that read no port records at all.
    """
    own_port = _port_holding(controller, inventory)
    if not controller.link.is_at_floor or own_port is None:
        return None
    if own_port.capability_denies_a_function:
        return None
    # The controller's OWN vendor against its port's, never the port's occupant:
    # that occupant is whichever device behind the port is hardest to displace,
    # which need not be this controller.
    maker = shared_maker(own_port.vendor, controller.vendor)
    if maker is None:
        return None
    # Ports of one switch share a bus, so the bus of the port this controller
    # occupies names the group, but only once that bus is shown to hang behind a
    # bridge. Ports on a root bus share a number and nothing else: each is an
    # independent slot, so a reading beside one says nothing about another.
    bus = pci_bus_of(own_port.address)
    if bus is None or not inventory.bus_is_behind_a_bridge(bus):
        return None

    def beside(slot: PcieSlot) -> bool:
        return slot.address != own_port.address and slot.occupant_address != controller.address

    # The machine holds each switch's readings grouped, rather than every
    # controller at the floor walking the ports around it: a switch carrying
    # many such functions made that walk cost the square of them.
    twin = next((slot for slot in inventory.floor_functions_on(bus, maker) if beside(slot)), None)
    passes_real_links = any(beside(slot) for slot in inventory.ports_running_above_floor_on(bus))
    return twin if passes_real_links else None


def _demand_clause(count: int, demand: float) -> str:
    """Open a sentence with what the attached drives can pull, in the number they come in.

    Example:
        >>> _demand_clause(3, 1.8)
        '3 drives can pull about 1.80 GB/s together'
        >>> _demand_clause(1, 0.6)
        'The drive can pull about 0.60 GB/s'
    """
    if count > 1:
        return f"{count} drives can pull about {format_gbytes(demand)} together"
    return f"The drive can pull about {format_gbytes(demand)}"


def _register_default_finding(
    controller: Controller,
    inventory: Inventory,
    *,
    twin: PcieSlot,
    demand: float,
) -> Finding:
    """Build the hint that stands in for an oversubscription warning resting on the PCIe floor."""
    link = controller.link
    wanted = _demand_clause(len(inventory.drives_on(controller.address)), demand)
    return Finding(
        severity=Severity.HINT,
        subject=controller.address,
        title=f"{controller.name} publishes the PCIe floor as its link, which is not a ceiling",
        detail=(
            f"{wanted}, and the link reads {format_pcie_sentence(link.max_speed_gtps, link.max_width)} "
            f"({format_gbytes(link.max_bandwidth_gbps)}) as both running and capable, the lowest the PCIe "
            f"specification allows. The {pci_class_name(twin.occupant_class)} at "
            f"{twin.occupant_address or twin.address} on the same switch publishes the identical floor, both "
            "carry the vendor of the switch ports in front of them, and another device there reports a real "
            "link, which is how functions integrated into the switch silicon read: the register holds the floor "
            "rather than describing a link, so it says nothing about what the drives can get through."
        ),
        action=(
            "Check the specification of the card or board this controller belongs to for its real uplink, "
            "rather than replacing hardware on this figure."
        ),
    )


def diagnose_controller_oversubscription(controller: Controller, inventory: Inventory) -> list[Finding]:
    """Report a controller whose drives can outrun its uplink.

    The ceiling is what the link CAN carry, never what it happens to be
    carrying. A PCIe link drops to 2.5 GT/s while the device behind it is idle
    and retrains when work arrives, so reading the resting figure invents a
    bottleneck that disappears the moment anything uses it. A link that is
    genuinely stuck below what both ends support is a different fault, and
    ``diagnose_controller_link`` already names it with the remedy that fits.

    An uplink figure that is the PCIe floor, published by a function integrated
    into switch silicon, is not a ceiling either. Where the ports around the
    controller show that, the warning gives way to a hint that sends the reader
    to the specification rather than to a replacement.

    Args:
        controller: The controller to examine.
        inventory: The machine it belongs to.

    Returns:
        A finding when the attached drives exceed the uplink figure, otherwise
        empty: the warning, or the hint where that figure is a register default.
    """
    uplink = controller.achievable_bandwidth_gbps
    demand = attached_demand_gbytes(controller, inventory)
    if uplink is None or demand is None or demand <= uplink:
        return []
    twin = _floor_twin(controller, inventory)
    if twin is not None:
        return [_register_default_finding(controller, inventory, twin=twin, demand=demand)]
    count = len(inventory.drives_on(controller.address))
    # A wider slot only helps a card that is running below its own maximum. A
    # part that is natively x1 gains nothing from a x16 connector, and sending
    # somebody to open the machine for it wastes the trip.
    if controller.link.is_downgraded:
        action = "Spread the drives across controllers, or move this card to a wider slot."
    else:
        action = (
            "Spread the drives across controllers, or replace this card: it is already at its own maximum, "
            "so a wider slot would not help."
        )
    return [
        Finding(
            severity=Severity.WARNING,
            subject=controller.address,
            title=f"{controller.name} is oversubscribed by the drives on it",
            detail=f"{_demand_clause(count, demand)}, but the uplink can carry {format_gbytes(uplink)}.",
            action=action,
        )
    ]


def diagnose(
    inventory: Inventory,
    *,
    history: History | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> tuple[Finding, ...]:
    """Run every applicable rule over an inventory.

    The physical-link rules are skipped where the readings are not physical. In
    a virtual machine the controller, its link speed and the disk behind it are
    all the hypervisor's invention, so "this drive negotiated 1.5 Gb/s, check the
    cable" is noise about a cable that does not exist. Health data is still real
    when the hypervisor passes a device through, so those rules keep running.

    Args:
        inventory: The machine to analyse.
        history: Counter samples recorded on earlier runs. With none, every rule
            behaves exactly as it did before history existed.
        thresholds: The judgement values to weigh against.

    Returns:
        Findings sorted most urgent first, then by subject for stable output.

    Example:
        >>> from lsdsk.domain.models import Inventory
        >>> diagnose(Inventory(hostname="empty"))
        ()
    """
    findings: list[Finding] = []
    physical = inventory.readings_are_physical
    for controller in inventory.controllers:
        if physical:
            findings.extend(diagnose_controller_link(controller, inventory))
            findings.extend(diagnose_controller_oversubscription(controller, inventory))
    for disk in inventory.disks:
        if physical:
            findings.extend(diagnose_disk_link(disk, inventory))
            findings.extend(diagnose_usb_link(disk))
        series = None if history is None else history.for_identity(identity_of(disk) or "")
        findings.extend(diagnose_health(disk, series, thresholds))
    if physical:
        findings.extend(diagnose_port_allocation(inventory))
        findings.extend(diagnose_fabric_links(inventory))
    findings.extend(diagnose_firmware_consistency(inventory, thresholds))
    return tuple(sorted(findings, key=lambda f: (SEVERITY_RANKING.index(f.severity), f.subject)))


def count_by_severity(findings: tuple[Finding, ...]) -> dict[Severity, int]:
    """Count findings per severity.

    Args:
        findings: The findings to tally.

    Returns:
        A count for every severity, including zeros.

    Example:
        >>> counts = count_by_severity(())
        >>> counts[Severity.CRITICAL]
        0
    """
    counts = dict.fromkeys(Severity, 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts


__all__ = [
    "SEVERITY_RANKING",
    "attached_demand_gbytes",
    "count_by_severity",
    "diagnose",
    "diagnose_controller_link",
    "diagnose_controller_oversubscription",
    "diagnose_disk_link",
    "diagnose_firmware_consistency",
    "diagnose_health",
    "diagnose_port_allocation",
    "diagnose_usb_link",
    "format_pcie_sentence",
    "interface_demand_gbytes",
    "one_step_in_severity",
    "refine",
]
