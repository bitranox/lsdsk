"""Pure rules that turn an inventory into findings.

No I/O, no formatting, no platform knowledge.  Given the same inventory these
functions always produce the same findings, which is what makes them testable
against hand-built cases and against snapshots captured from real machines.

The grading principle: a link running below a device's own maximum is only a
fault when the machine could actually do better.  Every speed rule therefore
weighs three numbers, what the device can do, what the other end can do, and
what was negotiated, and only calls it a warning when something on this machine
can be changed.  Where the platform is the ceiling, the finding says which
upgrade would lift it and whether the attached load would even notice.

System Role:
    The analytical core.  Renderers and the CLI consume its output; nothing
    here consumes theirs.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .enums import ControllerKind, Severity
from .history import CounterKind, History, identity_of, trend_for
from .models import (
    Controller,
    Disk,
    Finding,
    Inventory,
    PcieSlot,
    pcie_bandwidth_gbps,
    pcie_generation,
)
from .thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from .history import DiskSeries, Trend
    from .thresholds import Thresholds

# The shipped judgement values now live on Thresholds, which the adapter layer
# builds from configuration. These names are kept as the documented defaults.
WEAR_WARNING_PERCENT = DEFAULT_THRESHOLDS.wear_warning_percent
WEAR_CRITICAL_PERCENT = DEFAULT_THRESHOLDS.wear_critical_percent

# Serial links carry 8 bits of payload in every 10 transmitted below 12 Gb/s and
# use the same ratio at 12 Gb/s for SAS-3, so usable bytes per second is the
# signalling rate over ten.
_SERIAL_ENCODING_DIVISOR = 10.0

CRC_ERRORS_SIGNIFICANT = DEFAULT_THRESHOLDS.crc_errors_significant

_MIXED_FIRMWARE_THRESHOLD = DEFAULT_THRESHOLDS.mixed_firmware_threshold


def interface_demand_gbytes(disk: Disk) -> float | None:
    """Return the bandwidth one disk can actually pull, in GB/s.

    Args:
        disk: The disk to measure.

    Returns:
        Usable bandwidth in GB/s, or ``None`` when the link is unknown.

    Example:
        >>> from lsdsk.domain.models import Disk, InterfaceLink
        >>> interface_demand_gbytes(Disk("sda", "/dev/sda", "m", link=InterfaceLink(6.0, 6.0, 6.0)))
        0.6
    """
    if disk.pcie is not None:
        return disk.pcie.current_bandwidth_gbps
    if disk.link.negotiated_gbps is None:
        return None
    return round(disk.link.negotiated_gbps / _SERIAL_ENCODING_DIVISOR, 3)


def _format_gbytes(value: float | None) -> str:
    """Render a GB/s figure for a message, or a placeholder when unknown."""
    return "unknown" if value is None else f"{value:.2f} GB/s"


def _format_pcie(speed_gtps: float | None, width: int | None) -> str:
    """Render a PCIe link as a generation and width.

    Example:
        >>> _format_pcie(8.0, 8)
        'PCIe 3.0 x8'
        >>> _format_pcie(None, None)
        'PCIe unknown'
    """
    generation = pcie_generation(speed_gtps)
    if generation is None or width is None:
        return "PCIe unknown"
    return f"PCIe {generation}.0 x{width}"


def _board_best_pcie(controller: Controller, inventory: Inventory) -> tuple[float | None, int | None]:
    """Return the fastest port capability anywhere on this board.

    Capability only. Whether a port is a usable connector, and whether anything
    already occupies it, are separate questions answered elsewhere: a port that
    cannot be proposed as a move target is still evidence of what the board can
    do. Conflating the two once read an occupied Gen4 port as an absent one and
    advised buying a PCIe 4.0 board for a machine that was already PCIe 5.0.
    """
    speeds = [slot.link.max_speed_gtps for slot in inventory.slots if slot.link.max_speed_gtps is not None]
    widths = [slot.link.max_width for slot in inventory.slots if slot.link.max_width is not None]
    if controller.upstream is not None:
        if controller.upstream.max_speed_gtps is not None:
            speeds.append(controller.upstream.max_speed_gtps)
        if controller.upstream.max_width is not None:
            widths.append(controller.upstream.max_width)
    return (max(speeds) if speeds else None), (max(widths) if widths else None)


def _achievable_pcie(controller: Controller) -> tuple[float | None, int | None]:
    """Return what the port this controller currently sits in can give it.

    This is the *current* seat, not the board's ceiling. See
    :func:`_board_best_pcie` for the latter; the two differ whenever a faster
    port exists but is occupied.
    """
    own_speed = controller.link.max_speed_gtps
    own_width = controller.link.max_width
    if controller.upstream is None:
        return own_speed, own_width
    speeds = [v for v in (own_speed, controller.upstream.max_speed_gtps) if v is not None]
    widths = [v for v in (own_width, controller.upstream.max_width) if v is not None]
    return (min(speeds) if speeds else None), (min(widths) if widths else None)


def _gain_in(slot: PcieSlot, controller: Controller) -> float | None:
    """What a controller would get in one slot, in GB/s.

    A slot that does not report its own speed and width is never a candidate.
    Legacy PCI bridges report neither, and treating an unknown capability as an
    unlimited one made every such bridge look like the fastest slot in the
    machine, which produced a confident recommendation to move a card into a
    slot slower than the one it already occupied.
    """
    if slot.link.max_speed_gtps is None or slot.link.max_width is None:
        return None
    return pcie_bandwidth_gbps(
        _lower(slot.link.max_speed_gtps, controller.link.max_speed_gtps),
        _lower_int(slot.link.max_width, controller.link.max_width),
    )


def _best_slot(
    controller: Controller,
    inventory: Inventory,
    candidates: Callable[[PcieSlot], bool],
) -> PcieSlot | None:
    """Find the slot passing a test that would serve a controller best."""
    current = pcie_bandwidth_gbps(*_achievable_pcie(controller))
    if current is None:
        return None
    best: PcieSlot | None = None
    best_bandwidth = current
    for slot in inventory.slots:
        # Skip the port this controller already sits behind, matched by its
        # occupant rather than by address: a bridge and the card behind it never
        # share an address, so comparing addresses alone offers a card its own
        # slot as somewhere better to be.
        if controller.address in (slot.address, slot.occupant_address):
            continue
        if not candidates(slot):
            continue
        gain = _gain_in(slot, controller)
        if gain is not None and gain > best_bandwidth:
            best, best_bandwidth = slot, gain
    return best


def _free_slot_for(controller: Controller, inventory: Inventory) -> PcieSlot | None:
    """Find an empty slot with a real connector that would serve better."""
    return _best_slot(controller, inventory, lambda slot: slot.is_move_target)


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
        current = pcie_bandwidth_gbps(*_achievable_pcie(controller))
        # The displaced card must fit in the slot this controller vacates.
        if current is not None and occupant_need > current:
            return False
        return needed is None or occupant_need < needed

    return _best_slot(controller, inventory, is_worthwhile)


def _lower(left: float | None, right: float | None) -> float | None:
    """Return the smaller of two optional numbers, ignoring unknowns."""
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _lower_int(left: int | None, right: int | None) -> int | None:
    """Return the smaller of two optional integers, ignoring unknowns."""
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def attached_demand_gbytes(controller: Controller, inventory: Inventory) -> float | None:
    """Sum what the disks on one controller can pull, in GB/s.

    Args:
        controller: The controller to total up.
        inventory: The machine the controller belongs to.

    Returns:
        Aggregate demand in GB/s, or ``None`` when nothing is attached.
    """
    demands = [demand for disk in inventory.disks_on(controller.address) if (demand := interface_demand_gbytes(disk))]
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

    achievable_speed, achievable_width = _achievable_pcie(controller)
    negotiated = link.current_bandwidth_gbps
    achievable = pcie_bandwidth_gbps(achievable_speed, achievable_width)

    if negotiated is not None and achievable is not None and negotiated < achievable:
        return [
            Finding(
                severity=Severity.WARNING,
                subject=controller.address,
                title=f"{controller.name} negotiated below what this machine offers",
                detail=(
                    f"Running {_format_pcie(link.current_speed_gtps, link.current_width)} "
                    f"({_format_gbytes(negotiated)}) where both ends support "
                    f"{_format_pcie(achievable_speed, achievable_width)} ({_format_gbytes(achievable)})."
                ),
                action="Reseat the card, check the riser and cabling, and look for a slot speed override in the BIOS.",
            )
        ]

    own_max = link.max_bandwidth_gbps
    if achievable is None or own_max is None or achievable >= own_max:
        return []

    return [_platform_limited_finding(controller, inventory, achievable, own_max)]


def _platform_limited_finding(
    controller: Controller,
    inventory: Inventory,
    achievable: float,
    own_max: float,
) -> Finding:
    """Build the finding for a controller capped by the machine, not by itself."""
    achievable_speed, achievable_width = _achievable_pcie(controller)
    shortfall = _slot_shortfall(controller)

    move = _free_slot_for(controller, inventory)
    if move is not None:
        return Finding(
            severity=Severity.WARNING,
            subject=controller.address,
            title=f"{controller.name} is in a slot narrower or slower than it needs",
            detail=(
                f"This slot gives it {_format_gbytes(achievable)} and falls short on {shortfall}; "
                f"the card itself can do {_format_gbytes(own_max)}."
            ),
            action=(
                f"Move it to the free slot at {move.address} "
                f"({_format_pcie(move.link.max_speed_gtps, move.link.max_width)}). "
                "Check the slot is mechanically long enough or open-ended first."
            ),
        )

    swap = _swap_slot_for(controller, inventory)
    if swap is not None:
        return Finding(
            severity=Severity.WARNING,
            subject=controller.address,
            title=f"{controller.name} is in a slot narrower or slower than it needs",
            detail=(
                f"This slot gives it {_format_gbytes(achievable)} and falls short on {shortfall}. "
                f"The slot at {swap.address} is faster and holds a {swap.occupant_description}, which can only "
                f"use {_format_gbytes(swap.occupant_need_gbps)} of the {_format_gbytes(swap.capability_gbps)} "
                "it offers, so that card loses nothing in a narrower slot."
            ),
            action=(
                f"Swap the two cards over: this controller into {swap.address}, "
                f"the {swap.occupant_description} into {controller.address}."
            ),
        )

    board_speed, board_width = _board_best_pcie(controller, inventory)
    board_best = pcie_bandwidth_gbps(board_speed, board_width)
    board_has_faster = board_best is not None and board_best > achievable

    if board_has_faster:
        detail = (
            f"The card is {_format_pcie(controller.link.max_speed_gtps, controller.link.max_width)} capable; "
            f"the port it sits in gives it {_format_pcie(achievable_speed, achievable_width)}, short on "
            f"{shortfall}. This board has faster ports "
            f"({_format_pcie(board_speed, board_width)}) but none of them is free or swappable. "
            f"{_headroom_sentence(controller, inventory, achievable)}"
        )
        action = (
            f"Freeing a {_format_pcie(board_speed, board_width)} port would take this link to "
            f"{_format_gbytes(own_max)}; the board itself does not need replacing."
        )
    else:
        detail = (
            f"The card is {_format_pcie(controller.link.max_speed_gtps, controller.link.max_width)} capable; "
            f"the fastest port on this board is {_format_pcie(achievable_speed, achievable_width)}, short on "
            f"{shortfall}, and no free or swappable slot does better. "
            f"{_headroom_sentence(controller, inventory, achievable)}"
        )
        action = _upgrade_sentence(controller, achievable, own_max)

    return Finding(
        severity=Severity.HINT,
        subject=controller.address,
        title=f"{controller.name} is capped by the mainboard, not by itself",
        detail=detail,
        action=action,
    )


def _slot_shortfall(controller: Controller) -> str:
    """Name the dimension in which a controller's slot falls short."""
    if controller.upstream is None:
        return "bandwidth"
    return controller.upstream.shortfall_against(controller.link) or "bandwidth"


def _headroom_sentence(controller: Controller, inventory: Inventory, achievable: float) -> str:
    """Say whether the attached drives can actually feel the cap."""
    demand = attached_demand_gbytes(controller, inventory)
    if demand is None:
        return "Nothing is attached to it yet."
    if demand < achievable:
        return (
            f"The {len(inventory.disks_on(controller.address))} attached drives need about "
            f"{_format_gbytes(demand)}, so this link is not the bottleneck today; revisit if more "
            "drives are added."
        )
    return f"The attached drives already want about {_format_gbytes(demand)}, at or beyond this link."


def _upgrade_sentence(controller: Controller, achievable: float, own_max: float) -> str:
    """Say which platform upgrade would lift the cap, and by how much."""
    own_generation = pcie_generation(controller.link.max_speed_gtps)
    board_generation = pcie_generation(_achievable_pcie(controller)[0])
    if board_generation is not None and own_generation is not None and own_generation > board_generation:
        return (
            f"A PCIe {board_generation + 1}.0 board would take this link from {_format_gbytes(achievable)} "
            f"to {_format_gbytes(own_max)}."
        )
    return f"A wider slot would take this link to {_format_gbytes(own_max)}."


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
    for controller in inventory.controllers:
        if controller.address == disk.controller_address:
            continue
        free = controller.ports_free
        if not free:
            continue
        attached = inventory.disks_on(controller.address)
        rates = [d.link.port_max_gbps for d in attached if d.link.port_max_gbps is not None]
        if rates and max(rates) > port_max:
            return f"{controller.name} at {controller.address}"
    return None


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
    taken: set[str] = set()

    for disk in starved:
        port_max, drive_max = disk.link.port_max_gbps, disk.link.drive_max_gbps
        if port_max is None or drive_max is None:
            continue
        partner = _wasteful_holder(inventory, needs=drive_max, offers=port_max, taken=taken)
        if partner is None:
            continue
        taken.add(partner.node)
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


def _wasteful_holder(inventory: Inventory, *, needs: float, offers: float, taken: set[str]) -> Disk | None:
    """Find a drive holding a port faster than it can use.

    Args:
        inventory: The machine to search.
        needs: The speed the starved drive wants from the port it would take.
        offers: The speed of the port the starved drive would hand over.
        taken: Drives already promised to another swap.

    Returns:
        A drive that would lose nothing by trading places, or ``None``.
    """
    for candidate in inventory.disks:
        link = candidate.link
        if candidate.node in taken or link.drive_max_gbps is None or link.port_max_gbps is None:
            continue
        # It must gain the starved drive what it wanted, and lose nothing itself.
        if link.port_max_gbps >= needs and link.drive_max_gbps <= offers and link.drive_max_gbps < link.port_max_gbps:
            return candidate
    return None


# A measured rate moves a finding by exactly one step, never more. History
# refines a judgement that the counters already justified; it never manufactures
# one, and it never jumps a hint straight to critical.
_ESCALATION: dict[Severity, Severity] = {
    Severity.HINT: Severity.WARNING,
    Severity.WARNING: Severity.CRITICAL,
    Severity.CRITICAL: Severity.CRITICAL,
}
_DE_ESCALATION: dict[Severity, Severity] = {
    Severity.CRITICAL: Severity.WARNING,
    Severity.WARNING: Severity.HINT,
    Severity.HINT: Severity.HINT,
}

WEAR_PROJECTION_MIN_POINTS = DEFAULT_THRESHOLDS.wear_projection_min_points


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
        >>> base = Finding(Severity.WARNING, "/dev/sdd", "has errors")
        >>> rising = Trend(CounterKind.CRC_ERRORS, TrendVerdict.RISING, 900, 200, 10, 20.0, None)
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
        return replace(
            finding,
            severity=_ESCALATION[finding.severity],
            detail=f"{finding.detail}{measured}",
        )
    if trend.is_quiet and trend.span_hours and trend.expected_from_lifetime is not None:
        measured = (
            f" None of them are recent: the count has not moved in {trend.span_hours} power-on hours, "
            f"and this drive's own lifetime rate predicted about {trend.expected_from_lifetime:.0f} "
            "in that time. Whatever caused them is not doing so now."
        )
        return replace(
            finding,
            severity=_DE_ESCALATION[finding.severity],
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


def diagnose_controller_oversubscription(controller: Controller, inventory: Inventory) -> list[Finding]:
    """Report a controller whose drives can outrun its uplink.

    Args:
        controller: The controller to examine.
        inventory: The machine it belongs to.

    Returns:
        A finding when the attached drives exceed the uplink, otherwise empty.
    """
    uplink = controller.link.current_bandwidth_gbps
    demand = attached_demand_gbytes(controller, inventory)
    if uplink is None or demand is None or demand <= uplink:
        return []
    count = len(inventory.disks_on(controller.address))
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
            detail=(
                f"{count} drives can pull about {_format_gbytes(demand)} together, but the uplink carries "
                f"{_format_gbytes(uplink)}."
            ),
            action=action,
        )
    ]


_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.HINT: 2}


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
        >>> diagnose(Inventory("empty"))
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
        series = None if history is None else history.for_identity(identity_of(disk) or "")
        findings.extend(diagnose_health(disk, series, thresholds))
    if physical:
        findings.extend(diagnose_port_allocation(inventory))
    findings.extend(diagnose_firmware_consistency(inventory, thresholds))
    return tuple(sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.subject)))


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


def is_storage_controller(kind: ControllerKind) -> bool:
    """Whether a controller kind is one that can carry disks.

    Example:
        >>> is_storage_controller(ControllerKind.SAS)
        True
        >>> is_storage_controller(ControllerKind.UNKNOWN)
        False
    """
    return kind in {
        ControllerKind.AHCI,
        ControllerKind.SAS,
        ControllerKind.NVME,
        ControllerKind.RAID,
        ControllerKind.IDE,
    }


__all__ = [
    "CRC_ERRORS_SIGNIFICANT",
    "WEAR_CRITICAL_PERCENT",
    "WEAR_WARNING_PERCENT",
    "attached_demand_gbytes",
    "count_by_severity",
    "diagnose",
    "diagnose_controller_link",
    "diagnose_controller_oversubscription",
    "diagnose_disk_link",
    "diagnose_firmware_consistency",
    "diagnose_health",
    "diagnose_port_allocation",
    "interface_demand_gbytes",
    "is_storage_controller",
]
