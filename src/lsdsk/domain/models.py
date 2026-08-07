"""Typed value objects describing storage topology, health and diagnostics.

Every model here is frozen and free of I/O.  Collectors on each platform build
these from whatever the operating system offers; renderers and the diagnostics
rules consume nothing else.  That is what lets the same rules and the same
output run unchanged on Linux and Windows.

System Role:
    The vocabulary of the domain layer.  Adapters translate into it, never out
    of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import BusType, ControllerKind, DiskKind, Environment, Severity

# Usable bandwidth of one PCIe lane in one direction, in GB/s, per link speed in
# GT/s.  These are not raw signalling rates: they already account for the line
# code, 8b/10b below Gen3 and 128b/130b from Gen3 onward, because the number a
# reader cares about is throughput, not baud.
_PCIE_LANE_GBPS: dict[float, float] = {
    2.5: 0.250,
    5.0: 0.500,
    8.0: 0.985,
    16.0: 1.969,
    32.0: 3.938,
    64.0: 7.563,
}

# Marketing generation number for a PCIe signalling rate.
_PCIE_GENERATION: dict[float, int] = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}

# PCI base class codes, enough to say what is sitting in a slot.
_PCI_CLASS_STORAGE = 0x01
_PCI_CLASS_DISPLAY = 0x03
_PCI_CLASS_NAMES: dict[int, str] = {
    0x00: "legacy device",
    0x01: "storage controller",
    0x02: "network controller",
    0x03: "display controller",
    0x04: "multimedia device",
    0x05: "memory controller",
    0x06: "bridge",
    0x07: "communication controller",
    0x08: "system peripheral",
    0x09: "input device",
    0x0B: "processor",
    0x0C: "serial bus controller",
    0x0D: "wireless controller",
    0x10: "encryption controller",
    0x11: "signal processing controller",
    0x12: "processing accelerator",
}


def pci_class_name(class_code: int | None) -> str:
    """Return a readable name for a PCI class triple.

    Args:
        class_code: The class triple, for example ``0x030000``.

    Returns:
        A readable description, or a generic phrase when unrecognised.

    Example:
        >>> pci_class_name(0x030000)
        'display controller'
        >>> pci_class_name(0x010700)
        'storage controller'
        >>> pci_class_name(None)
        'unknown device'
    """
    if class_code is None:
        return "unknown device"
    return _PCI_CLASS_NAMES.get(class_code >> 16, "unknown device")


def pcie_generation(speed_gtps: float | None) -> int | None:
    """Return the PCIe generation number for a signalling rate.

    Args:
        speed_gtps: Link speed in GT/s, or ``None`` when unknown.

    Returns:
        The generation number, or ``None`` if the rate is unknown or unmapped.

    Example:
        >>> pcie_generation(8.0)
        3
        >>> pcie_generation(None) is None
        True
    """
    if speed_gtps is None:
        return None
    return _PCIE_GENERATION.get(speed_gtps)


def pcie_bandwidth_gbps(speed_gtps: float | None, width: int | None) -> float | None:
    """Return usable one-direction bandwidth for a PCIe link.

    Args:
        speed_gtps: Link speed in GT/s.
        width: Link width in lanes.

    Returns:
        Bandwidth in GB/s, or ``None`` when either input is unknown.

    Example:
        >>> pcie_bandwidth_gbps(8.0, 8)
        7.88
        >>> pcie_bandwidth_gbps(16.0, 4)
        7.876
        >>> pcie_bandwidth_gbps(None, 4) is None
        True
    """
    if speed_gtps is None or width is None or width <= 0:
        return None
    per_lane = _PCIE_LANE_GBPS.get(speed_gtps)
    if per_lane is None:
        return None
    return round(per_lane * width, 3)


@dataclass(frozen=True, slots=True)
class PcieLink:
    """Negotiated and maximum state of one PCIe link.

    Attributes:
        current_speed_gtps: Negotiated signalling rate in GT/s.
        current_width: Negotiated width in lanes.
        max_speed_gtps: Highest rate this end of the link supports.
        max_width: Widest width this end of the link supports.

    Example:
        >>> link = PcieLink(8.0, 8, 16.0, 8)
        >>> link.current_bandwidth_gbps
        7.88
        >>> link.is_downgraded
        True
    """

    current_speed_gtps: float | None = None
    current_width: int | None = None
    max_speed_gtps: float | None = None
    max_width: int | None = None

    @property
    def capability_is_known(self) -> bool:
        """Whether both halves of this end's capability were actually read.

        Asked of the values rather than of their rendering. A renderer that
        tests its own output for the unknown placeholder is reading a decision
        back out of a string it just wrote, and it silently changes meaning the
        day that placeholder does.

        Example:
            >>> PcieLink(max_speed_gtps=8.0, max_width=4).capability_is_known
            True
            >>> PcieLink().capability_is_known
            False
        """
        return self.max_speed_gtps is not None and self.max_width is not None

    @property
    def current_bandwidth_gbps(self) -> float | None:
        """Usable bandwidth of the negotiated link, in GB/s."""
        return pcie_bandwidth_gbps(self.current_speed_gtps, self.current_width)

    @property
    def max_bandwidth_gbps(self) -> float | None:
        """Usable bandwidth this end of the link could reach, in GB/s."""
        return pcie_bandwidth_gbps(self.max_speed_gtps, self.max_width)

    @property
    def is_downgraded(self) -> bool:
        """Whether the link negotiated below what this end supports.

        Example:
            >>> PcieLink(8.0, 8, 8.0, 8).is_downgraded
            False
            >>> PcieLink(8.0, 4, 8.0, 8).is_downgraded
            True
        """
        speed_low = (
            self.current_speed_gtps is not None
            and self.max_speed_gtps is not None
            and self.current_speed_gtps < self.max_speed_gtps
        )
        width_low = (
            self.current_width is not None and self.max_width is not None and self.current_width < self.max_width
        )
        return speed_low or width_low

    @property
    def is_dead(self) -> bool:
        """Whether the link never trained, reported as a width of zero.

        Example:
            >>> PcieLink(2.5, 0, 8.0, 4).is_dead
            True
        """
        return self.current_width == 0

    def shortfall_against(self, ceiling: PcieLink) -> str | None:
        """Name which dimension falls short of another link's capability.

        Speed and width fail for different reasons and are fixed differently: a
        narrow link usually means slot wiring or a bifurcation setting, a slow
        one usually means a generation limit or a BIOS setting.  Saying only
        "less bandwidth" would leave the reader to guess which.

        Args:
            ceiling: The capability to measure against.

        Returns:
            A phrase naming the shortfall, or ``None`` when there is none.

        Example:
            >>> card = PcieLink(max_speed_gtps=16.0, max_width=8)
            >>> PcieLink(8.0, 8, 8.0, 8).shortfall_against(card)
            'speed'
            >>> PcieLink(16.0, 4, 16.0, 4).shortfall_against(card)
            'width'
            >>> PcieLink(8.0, 4, 8.0, 4).shortfall_against(card)
            'speed and width'
            >>> PcieLink(16.0, 8, 16.0, 8).shortfall_against(card) is None
            True
        """
        slow = (
            self.max_speed_gtps is not None
            and ceiling.max_speed_gtps is not None
            and self.max_speed_gtps < ceiling.max_speed_gtps
        )
        narrow = self.max_width is not None and ceiling.max_width is not None and self.max_width < ceiling.max_width
        if slow and narrow:
            return "speed and width"
        if slow:
            return "speed"
        if narrow:
            return "width"
        return None


@dataclass(frozen=True, slots=True)
class PcieSlot:
    """A PCIe port, and whether a card could actually be moved into it.

    Used to answer "is there a better place for this card in this machine".
    Most PCIe bridges are not slots: they are internal ports to soldered-down
    devices, with no connector a human could plug anything into.  Advising
    someone to move a card into one of those would be worse than saying nothing,
    so a port only counts as a candidate when the hardware says it terminates in
    a real connector.

    An occupied slot is not automatically unavailable either.  What sits in it
    decides: a slot holding the graphics card cannot be taken, but a slot
    holding another storage controller that needs less bandwidth is a candidate
    for swapping the two cards round.

    Attributes:
        address: PCI address of the port, for example ``0000:00:03.0``.
        link: Link state and capability of the port.
        occupied: Whether a device sits behind it.
        connector_present: Whether this port ends in a physical slot, from the
            Slot Implemented bit in its PCIe capability.  ``None`` means it could
            not be determined, which is what an unprivileged run sees, because
            the capability lives beyond the world-readable part of PCI config
            space.
        occupant_address: PCI address of whatever sits in it.
        occupant_class: PCI class triple of the occupant.
        occupant_name: Readable name of the occupant.
        occupant_link: The occupant's own link capability, which is what decides
            whether it needs the slot it is in.
        physical_slot_number: The board's own number for this connector, as a
            mainboard manual labels its slots. ``None`` when it could not be
            read, which is any unprivileged run. It is the only readable datum
            tying a port to something a person can point at: no source gives the
            form factor, so an M.2 socket cannot be told from a card slot.

    Example:
        >>> port = PcieSlot("0000:00:03.0", PcieLink(8.0, 8, 8.0, 8), connector_present=True)
        >>> port.is_move_target
        True
        >>> PcieSlot("0000:00:11.0", PcieLink(), connector_present=False).is_move_target
        False
        >>> PcieSlot("0000:00:03.0", PcieLink()).is_move_target
        False
    """

    address: str
    link: PcieLink
    occupied: bool = False
    connector_present: bool | None = None
    occupant_address: str | None = None
    occupant_class: int | None = None
    occupant_name: str | None = None
    occupant_link: PcieLink | None = None
    physical_slot_number: int | None = None

    @property
    def is_move_target(self) -> bool:
        """Whether a card could be moved into this port with nothing displaced.

        Requires a free port with a confirmed physical connector.  Unknown is
        treated as unusable on purpose: a recommendation that cannot be carried
        out is worse than no recommendation.
        """
        return self.connector_present is True and not self.occupied

    @property
    def capability_gbps(self) -> float | None:
        """What this port itself can carry, in GB/s."""
        return self.link.max_bandwidth_gbps

    @property
    def occupant_need_gbps(self) -> float | None:
        """What the card in this port can actually use, in GB/s.

        A card's own maximum link speed and width is what it can use, however
        generous the slot is.  A one-gigabit network card that maxes out at a
        single Gen1 lane cannot use a wide slot no matter where it is put.
        """
        return None if self.occupant_link is None else self.occupant_link.max_bandwidth_gbps

    @property
    def occupant_is_display(self) -> bool:
        """Whether a graphics card holds this port.

        A graphics card stays where the monitor output and the mechanical
        clearance are, so it is never offered for displacement. Kept as one
        property because both the swap rule and the slot view need the same
        answer, and two copies of it would drift.

        Example:
            >>> PcieSlot("a", PcieLink(), occupant_class=0x030000).occupant_is_display
            True
            >>> PcieSlot("a", PcieLink(), occupant_class=0x020000).occupant_is_display
            False
            >>> PcieSlot("a", PcieLink()).occupant_is_display
            False
        """
        return self.occupant_class is not None and (self.occupant_class >> 16) == _PCI_CLASS_DISPLAY

    @property
    def is_swap_candidate(self) -> bool:
        """Whether the card in this port is demonstrably wasting it.

        True when the occupant cannot use the bandwidth the port offers, so it
        would lose nothing by moving to a narrower or slower slot.  Display
        controllers are excluded regardless: a graphics card has to stay where
        the monitor output and the mechanical clearance are, and that is not a
        trade this tool should propose.

        Example:
            >>> narrow = PcieLink(max_speed_gtps=2.5, max_width=1)
            >>> wide = PcieLink(max_speed_gtps=8.0, max_width=8)
            >>> nic = PcieSlot("0000:00:02.0", wide, occupied=True, connector_present=True,
            ...                occupant_class=0x020000, occupant_link=narrow)
            >>> nic.is_swap_candidate
            True
            >>> gpu = PcieSlot("0000:00:02.0", wide, occupied=True, connector_present=True,
            ...                occupant_class=0x030000, occupant_link=wide)
            >>> gpu.is_swap_candidate
            False
        """
        if self.connector_present is not True or not self.occupied:
            return False
        if self.occupant_is_display:
            return False
        need, capability = self.occupant_need_gbps, self.capability_gbps
        return need is not None and capability is not None and need < capability

    @property
    def occupant_description(self) -> str:
        """A readable description of what is in this slot.

        Example:
            >>> PcieSlot("a", PcieLink(), occupied=True, occupant_class=0x030000).occupant_description
            'display controller'
            >>> PcieSlot("a", PcieLink()).occupant_description
            'empty'
        """
        if not self.occupied:
            return "empty"
        if self.occupant_name:
            return self.occupant_name
        return pci_class_name(self.occupant_class)


@dataclass(frozen=True, slots=True)
class InterfaceLink:
    """Negotiated versus capable speed of one disk's own interface.

    Holds three numbers, because whether a slow link is a fault depends on both
    ends: a 3 Gb/s link is fine for a 3 Gb/s drive and a defect for a 6 Gb/s one.

    Attributes:
        negotiated_gbps: What the link actually runs at.
        drive_max_gbps: The fastest the drive itself can go.
        port_max_gbps: The fastest the port or phy it is attached to can go.

    Example:
        >>> link = InterfaceLink(3.0, 6.0, 12.0)
        >>> link.achievable_gbps
        6.0
        >>> link.is_underperforming
        True
    """

    negotiated_gbps: float | None = None
    drive_max_gbps: float | None = None
    port_max_gbps: float | None = None

    @property
    def achievable_gbps(self) -> float | None:
        """The best this pairing could manage, being the slower of the two ends.

        Both ends are required. An end that was never read is not evidence of a
        capable one: filling it in from the end that *was* read would make an
        unknown port look at least as fast as the drive, which turns "we could
        not measure this" into "the port is fine", and that is the difference
        between a cable fault and a drive sitting in a slower port.

        Example:
            >>> InterfaceLink(6.0, 6.0, 12.0).achievable_gbps
            6.0
            >>> InterfaceLink(3.0, 6.0, 3.0).achievable_gbps
            3.0
            >>> InterfaceLink(3.0, 6.0, None).achievable_gbps is None
            True
            >>> InterfaceLink(6.0, None, None).achievable_gbps is None
            True
        """
        if self.drive_max_gbps is None or self.port_max_gbps is None:
            return None
        return min(self.drive_max_gbps, self.port_max_gbps)

    @property
    def is_underperforming(self) -> bool:
        """Whether the link provably runs below what both ends could manage.

        False whenever either end is unknown, because the claim it supports is
        that *both* ends agreed they could go faster and then did not.

        Example:
            >>> InterfaceLink(3.0, 6.0, 6.0).is_underperforming
            True
            >>> InterfaceLink(3.0, 6.0, None).is_underperforming
            False
        """
        achievable = self.achievable_gbps
        return self.negotiated_gbps is not None and achievable is not None and self.negotiated_gbps < achievable

    @property
    def is_below_drive_capability(self) -> bool:
        """Whether the link runs below what the drive alone can do.

        Weaker than :attr:`is_underperforming` and says nothing about blame: a
        slower port explains it just as well as a bad cable. It is what remains
        knowable when the port capability could not be read.

        Example:
            >>> InterfaceLink(3.0, 6.0, None).is_below_drive_capability
            True
            >>> InterfaceLink(6.0, 6.0, None).is_below_drive_capability
            False
        """
        return (
            self.negotiated_gbps is not None
            and self.drive_max_gbps is not None
            and self.negotiated_gbps < self.drive_max_gbps
        )

    @property
    def is_port_limited(self) -> bool:
        """Whether the port, not the drive, is what caps this link.

        Example:
            >>> InterfaceLink(3.0, 6.0, 3.0).is_port_limited
            True
            >>> InterfaceLink(6.0, 6.0, 12.0).is_port_limited
            False
        """
        return (
            self.drive_max_gbps is not None
            and self.port_max_gbps is not None
            and self.port_max_gbps < self.drive_max_gbps
        )


@dataclass(frozen=True, slots=True)
class SmartAttribute:
    """One row of the ATA SMART attribute table.

    Attributes:
        id: Attribute identifier, 1 to 254.
        name: Conventional name, or an empty string when the id is unknown.
        value: Normalised current value, typically 0 to 253.
        worst: Worst normalised value ever recorded.
        threshold: Failure threshold, or ``None`` when not read.
        raw: Vendor-specific raw value.

    Example:
        >>> SmartAttribute(5, "Reallocated_Sector_Ct", 100, 100, 10, 0).is_failing
        False
        >>> SmartAttribute(5, "Reallocated_Sector_Ct", 8, 8, 10, 42).is_failing
        True
    """

    id: int
    name: str
    value: int
    worst: int
    threshold: int | None
    raw: int

    @property
    def is_failing(self) -> bool:
        """Whether the normalised value has fallen to or below its threshold."""
        return self.threshold is not None and self.threshold > 0 and self.value <= self.threshold


# Critical warning bit meanings from the NVMe SMART/Health log page. Defined by
# the specification rather than by any operating system, and kept here because
# the rules need the meaning, not the byte.
CRITICAL_WARNING_REASONS: tuple[str, ...] = (
    "spare capacity below threshold",
    "temperature outside the safe range",
    "internal reliability degraded",
    "media placed in read-only mode",
    "volatile memory backup failed",
    "persistent memory region unreliable",
)


@dataclass(frozen=True, slots=True)
class Health:
    """Condition and wear of one disk, normalised across ATA and NVMe.

    Every field is optional because an unprivileged run, or a controller that
    refuses passthrough, leaves most of them unreadable.  ``None`` means "not
    read", never "zero".

    Attributes:
        ok: Overall self-assessment, ``None`` when it could not be read.
        temperature_c: Current composite temperature in degrees Celsius.
        temperature_warning_c: Vendor's own warning threshold.
        temperature_critical_c: Vendor's own critical threshold.
        power_on_hours: Cumulative powered-on time.
        percent_used: Wear indicator, 0 is new and 100 is at rated endurance.
        reallocated_sectors: Sectors retired to the spare pool.
        pending_sectors: Sectors awaiting reallocation.
        uncorrectable_sectors: Sectors that could not be recovered.
        media_errors: Unrecovered data integrity errors (NVMe).
        crc_errors: Frames corrupted in transit on the interface and resent.
            A property of the cable and connector, not of the media.
        bytes_read: Lifetime host reads.
        bytes_written: Lifetime host writes.
        unsafe_shutdowns: Power lost without a clean shutdown notification
            (NVMe). Climbs with every hard power cut, so it is read as a rate.
        error_log_entries: Entries added to the NVMe error information log.
            The nearest NVMe equivalent of an interface error count.
        power_cycles: Times the drive has been powered up.
        available_spare: Remaining spare capacity as a percentage (NVMe).
        available_spare_threshold: The percentage below which the drive itself
            raises a critical warning.
        critical_warning: The NVMe critical warning byte. Kept as the raw bits
            so the reasons can be named rather than collapsed to a boolean.
        attributes: The full ATA SMART table, empty for NVMe.

    Example:
        >>> Health(percent_used=59).percent_used
        59
        >>> Health(critical_warning=0b101).critical_warning_reasons
        ('spare capacity below threshold', 'internal reliability degraded')
    """

    ok: bool | None = None
    temperature_c: int | None = None
    temperature_warning_c: int | None = None
    temperature_critical_c: int | None = None
    power_on_hours: int | None = None
    percent_used: int | None = None
    reallocated_sectors: int | None = None
    pending_sectors: int | None = None
    uncorrectable_sectors: int | None = None
    media_errors: int | None = None
    crc_errors: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None
    unsafe_shutdowns: int | None = None
    error_log_entries: int | None = None
    power_cycles: int | None = None
    available_spare: int | None = None
    available_spare_threshold: int | None = None
    critical_warning: int | None = None
    attributes: tuple[SmartAttribute, ...] = ()

    @property
    def critical_warning_reasons(self) -> tuple[str, ...]:
        """Why the drive is raising a critical warning, one phrase per set bit.

        Empty when nothing is wrong and when the byte was never read, which are
        different things: ``ok`` distinguishes them.
        """
        if not self.critical_warning:
            return ()
        return tuple(
            reason for bit, reason in enumerate(CRITICAL_WARNING_REASONS) if self.critical_warning & (1 << bit)
        )

    @property
    def spare_below_threshold(self) -> bool:
        """Whether spare capacity has fallen to the drive's own critical level."""
        if self.available_spare is None or self.available_spare_threshold is None:
            return False
        return self.available_spare <= self.available_spare_threshold


@dataclass(frozen=True, slots=True)
class Controller:
    """A storage controller and its position on the PCIe fabric.

    Attributes:
        address: PCI address, for example ``0000:03:00.0``.
        name: Resolved human-readable name.
        kind: What sort of controller this is.
        driver: Bound driver name.
        firmware: Controller firmware revision, where the driver reports one.
        link: The controller's own PCIe link state and capability.
        upstream: Capability of the bridge it plugs into, which is the ceiling
            the machine can offer it.
        upstream_name: What the port above it is CALLED, which is not the same
            thing as what it was measured to do. Carried because a platform can
            withhold the port's capability registers and still name the port,
            and a name like "PCIe RC 060 (x4) G4" answers the question the
            missing registers left open. It is reported, never parsed into a
            capability: it comes from a driver package rather than the hardware,
            and only some vendors put the width and generation in it.
        port_count: Total ports or phys, where known.
        ports_used: Ports or phys with something attached.

    Example:
        >>> Controller("0000:03:00.0", "HBA 9500-16i", ControllerKind.SAS).address
        '0000:03:00.0'
    """

    address: str
    name: str
    kind: ControllerKind = ControllerKind.UNKNOWN
    driver: str | None = None
    firmware: str | None = None
    link: PcieLink = field(default_factory=PcieLink)
    upstream: PcieLink | None = None
    upstream_name: str | None = None
    port_count: int | None = None
    ports_used: int | None = None

    @property
    def ports_free(self) -> int | None:
        """How many ports are still available for another drive.

        Example:
            >>> Controller("a", "n", port_count=16, ports_used=11).ports_free
            5
            >>> Controller("a", "n").ports_free is None
            True
        """
        if self.port_count is None or self.ports_used is None:
            return None
        return max(self.port_count - self.ports_used, 0)

    @property
    def achievable_bandwidth_gbps(self) -> float | None:
        """Best PCIe bandwidth this machine can give this controller.

        The lower of what the card supports and what its bridge supports.

        Example:
            >>> card = PcieLink(8.0, 8, 16.0, 8)
            >>> bridge = PcieLink(8.0, 8, 8.0, 8)
            >>> Controller("a", "n", link=card, upstream=bridge).achievable_bandwidth_gbps
            7.88
        """
        own = self.link.max_bandwidth_gbps
        if self.upstream is None:
            return own
        upstream = self.upstream.max_bandwidth_gbps
        candidates = [value for value in (own, upstream) if value is not None]
        return min(candidates) if candidates else None


@dataclass(frozen=True, slots=True)
class Disk:
    """One physical disk, wherever it hangs.

    Attributes:
        node: Kernel or OS name, for example ``sda`` or ``PhysicalDrive0``.
        path: Path used to open the device.
        model: Product name as the drive reports it.
        serial: Serial number.
        firmware: Firmware revision.
        wwn: The identifier that stays with the drive wherever it is plugged in,
            as the drive publishes it: ``naa.`` for SATA and SAS, ``eui.`` or a
            namespace ``uuid.`` for NVMe. Device names are not stable across
            reboots, so this is what a work order should quote.
        size_bytes: Capacity.
        kind: Solid state or rotating.
        bus: Transport it speaks.
        controller_address: PCI address of the controller it hangs off.
        link: Its interface speed, negotiated against both ends' capability.
        pcie: For NVMe, the drive's own PCIe link.
        health: Condition and wear, when it could be read.

    Example:
        >>> Disk("sda", "/dev/sda", "Samsung SSD 870 EVO 4TB").node
        'sda'
    """

    node: str
    path: str
    model: str
    serial: str | None = None
    firmware: str | None = None
    wwn: str | None = None
    size_bytes: int | None = None
    kind: DiskKind = DiskKind.UNKNOWN
    bus: BusType = BusType.UNKNOWN
    controller_address: str | None = None
    link: InterfaceLink = field(default_factory=InterfaceLink)
    pcie: PcieLink | None = None
    health: Health | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """One diagnosed problem or improvement opportunity.

    Attributes:
        severity: How much it should worry the reader.
        subject: The object it concerns, as shown in the tree.
        title: One-line statement of the problem.
        detail: The measurement that triggered it.
        action: What to do about it, or ``None`` when nothing can be done.

    Example:
        >>> Finding(Severity.WARNING, "/dev/sdb", "SATA link below drive capability").severity
        <Severity.WARNING: 'warning'>
    """

    severity: Severity
    subject: str
    title: str
    detail: str = ""
    action: str | None = None


@dataclass(frozen=True, slots=True)
class Inventory:
    """Everything one scan found on one machine.

    Attributes:
        hostname: Machine the scan came from.
        controllers: Storage controllers, in PCI address order.
        disks: Physical disks, in node order.
        slots: Every PCIe bridge and root port, for placement advice.
        privileged: Whether the scan had the rights to read SMART data.
        environment: Whether this is bare metal, a guest, or a container.
        environment_detail: The runtime or hypervisor, when it could be named.
        board: The mainboard, when DMI named it. Empty when it did not.
        devices_accessible: Whether the device nodes needed to interrogate a
            disk exist at all. False in most containers, where elevating
            changes nothing because there is nothing to open.

    Example:
        >>> Inventory("linux-sas-hba").hostname
        'linux-sas-hba'
    """

    hostname: str
    controllers: tuple[Controller, ...] = ()
    disks: tuple[Disk, ...] = ()
    slots: tuple[PcieSlot, ...] = ()
    privileged: bool = False
    environment: Environment = Environment.UNKNOWN
    environment_detail: str = ""
    board: str = ""
    devices_accessible: bool = True

    @property
    def hardware_is_local(self) -> bool:
        """Whether the hardware described here is this machine's to act on.

        Example:
            >>> Inventory("h", environment=Environment.BARE_METAL).hardware_is_local
            True
            >>> Inventory("h", environment=Environment.CONTAINER).hardware_is_local
            False
        """
        return self.environment is not Environment.CONTAINER

    @property
    def readings_are_physical(self) -> bool:
        """Whether link speeds and temperatures describe real hardware.

        A container is included: it sees the host's real disks through a shared
        kernel, so a drive negotiating below its capability there is a genuine
        fault and worth reporting, even though it has to be fixed from the host.
        Only a hypervisor invents the numbers.

        Example:
            >>> Inventory("h", environment=Environment.VIRTUAL_MACHINE).readings_are_physical
            False
            >>> Inventory("h", environment=Environment.CONTAINER).readings_are_physical
            True
        """
        return self.environment is not Environment.VIRTUAL_MACHINE

    def port_link_for(self, disk: Disk) -> PcieLink | None:
        """Return the PCIe port a directly-attached disk sits in.

        An NVMe drive is its own controller, so the seat it occupies is that
        controller's upstream link. Without this the port column can only repeat
        the drive's own capability, which reads as a port that is never the
        constraint even when it always is.

        Args:
            disk: The disk to place.

        Returns:
            The upstream link, or ``None`` for a disk that is not PCIe-attached
            or whose controller could not be identified.
        """
        if disk.pcie is None or disk.controller_address is None:
            return None
        for controller in self.controllers:
            if controller.address == disk.controller_address:
                return controller.upstream
        return None

    def disks_on(self, controller_address: str) -> tuple[Disk, ...]:
        """Return the disks attached to one controller.

        Args:
            controller_address: PCI address to match.

        Returns:
            Matching disks, in inventory order.

        Example:
            >>> disk = Disk("sda", "/dev/sda", "m", controller_address="0000:03:00.0")
            >>> inv = Inventory("h", disks=(disk,))
            >>> [d.node for d in inv.disks_on("0000:03:00.0")]
            ['sda']
        """
        return tuple(disk for disk in self.disks if disk.controller_address == controller_address)


__all__ = [
    "Controller",
    "Disk",
    "Finding",
    "Health",
    "InterfaceLink",
    "Inventory",
    "PcieLink",
    "PcieSlot",
    "SmartAttribute",
    "pcie_bandwidth_gbps",
    "pcie_generation",
]
