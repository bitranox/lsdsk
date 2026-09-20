"""Turn a captured Linux sysfs reading into the domain inventory.

Deliberately pure: it takes the typed reading :mod:`.capture` parses from the
mapping :mod:`.reader` produces, and returns domain objects, touching no files.
That is what lets the whole Linux mapping path be tested on any operating system
against captures taken from real machines, and it is what makes ``--replay``
render exactly what a live run would.

System Role:
    Adapter layer, translation half.  The impure half lives in :mod:`.reader`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from ....domain.enums import BusType, ControllerKind, DiskKind
from ....domain.models import (
    Controller,
    Disk,
    Health,
    InterfaceLink,
    Inventory,
    PcieLink,
    PcieSlot,
    PciNode,
    PortChild,
    representative_occupant,
)
from ....domain.text import device_text, first_reported
from ..decode import pciids
from ..decode.ahci import decode_capabilities
from ..decode.ata_identify import AtaIdentity, decode_identify, decode_vpd_ata_information
from ..decode.ata_smart import decode_health
from ..decode.captured import decode_base64, parse_int
from ..decode.nvme import decode_identify_controller, decode_smart_log
from ..decode.pciids import Database
from ..decode.virtualization import board_name, classify
from ..fabric import NodeSource, assemble, port_kind_of
from ..refusals import refusals_of
from .capture import AtaBlobs, NvmeBlobs, NvmeClassEntry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from ..decode.ahci import AhciCapabilities
    from .capture import AtaLinkEntry, BlockEntry, LinuxCapture, PciEntry, SasPhyEntry, ScsiHostEntry

# A PCI address as it appears inside a sysfs device path. The domain is FOUR OR
# MORE digits and cannot start mid-number: an Intel VMD re-enumerates its drives
# into domain 0x10000, and a fixed four digits with no left boundary matched the
# last four of it, so `10000:e1:00.0` reported a parent of `0000:e0:06.0` - an
# address in no capture, which detached every drive behind the VMD into a
# phantom root complex of its own.
_PCI_ADDRESS = re.compile(r"(?<![0-9a-f])[0-9a-f]{4,}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]")
_SAS_PORT = re.compile(r"/port-(\d+:\d+)/")
_ATA_PORT = re.compile(r"/ata(\d+)/")
# The same token as a zero-width lookahead, so EVERY occurrence in one path
# is found. A consuming pattern eats the separator its neighbour needs, so
# `/ata1/ata2/` would index only the first and a disk asking for the second
# would get no link where the substring test this replaces found one.
_ATA_PORT_ANYWHERE = re.compile(r"(?=/ata(\d+)/)")

# PCI base and sub class codes for storage, from the class triple 0xBBSSPP.
_CONTROLLER_KINDS: dict[int, ControllerKind] = {
    0x01: ControllerKind.IDE,
    0x04: ControllerKind.RAID,
    0x06: ControllerKind.AHCI,
    0x07: ControllerKind.SAS,
    0x08: ControllerKind.NVME,
}
_STORAGE_CLASS = 0x01
_BRIDGE_CLASS = 0x0604

# Sysfs reports hwmon temperatures in thousandths of a degree.
_MILLIDEGREE = 1000

# Sysfs block sizes are always counted in 512-byte units regardless of the
# drive's real sector size.
_SYSFS_SECTOR_BYTES = 512
# The kernel's own sector_t is a 64-bit unsigned, so a capture naming more
# sectors than this did not come from a block device. Bounded here rather
# than on the model because a capture leaves values as the text the platform
# published and decoding that text is this layer's tolerant job.
_MAX_SYSFS_SECTORS = 2**64 - 1


def parse_pcie_speed(text: str | None) -> float | None:
    """Parse a sysfs PCIe link speed such as ``8.0 GT/s PCIe``.

    Args:
        text: The sysfs value, or ``None``.

    Returns:
        The rate in GT/s, or ``None`` when absent or unparsable.

    Example:
        >>> parse_pcie_speed("8.0 GT/s PCIe")
        8.0
        >>> parse_pcie_speed("Unknown") is None
        True
    """
    if not text:
        return None
    match = re.match(r"([0-9.]+)\s*GT/s", text)
    return float(match.group(1)) if match else None


def parse_link_rate(text: str | None) -> float | None:
    """Parse a SAS phy rate such as ``6.0 Gbit`` or a SATA ``6.0 Gbps``.

    Both classes report ``Unknown`` or ``<unknown>`` when the link is idle or
    the driver does not track it, which must read as absent rather than as zero.

    Args:
        text: The sysfs value, or ``None``.

    Returns:
        The rate in Gb/s, or ``None``.

    Example:
        >>> parse_link_rate("6.0 Gbit")
        6.0
        >>> parse_link_rate("<unknown>") is None
        True
        >>> parse_link_rate("Unknown") is None
        True
    """
    if not text:
        return None
    match = re.match(r"([0-9.]+)\s*Gb", text)
    return float(match.group(1)) if match else None


def controller_address_of(device_path: str) -> str | None:
    """Return the PCI address of the controller a device hangs off.

    The last PCI address in a sysfs device path is the endpoint the device is
    attached to; everything after it is bus-specific topology.

    Args:
        device_path: An absolute sysfs device path.

    Returns:
        The controller's PCI address, or ``None`` when the path has none.

    Example:
        >>> controller_address_of("/sys/devices/pci0000:00/0000:00:03.0/0000:03:00.0/host6/target6:0:0")
        '0000:03:00.0'
        >>> controller_address_of("/sys/devices/virtual/block/loop0") is None
        True
    """
    matches = _PCI_ADDRESS.findall(device_path)
    return matches[-1] if matches else None


def _parent_address(pci_path: str) -> str | None:
    """Return the PCI address of the bridge immediately above a device."""
    matches = _PCI_ADDRESS.findall(pci_path)
    return matches[-2] if len(matches) >= 2 else None  # noqa: PLR2004 - device plus its parent


def _pcie_link(entry: PciEntry) -> PcieLink:
    """Build a PCIe link from one sysfs PCI device's attributes."""
    return PcieLink(
        current_speed_gtps=parse_pcie_speed(entry.current_link_speed),
        current_width=parse_int(entry.current_link_width),
        max_speed_gtps=parse_pcie_speed(entry.max_link_speed),
        max_width=parse_int(entry.max_link_width),
    )


def _pcie_capability_present(entry: PciEntry) -> bool:
    """Whether this device has a PCIe capability, which sysfs answers either way.

    Linux publishes ``max_link_speed`` and the rest for every device carrying a
    PCIe capability and nothing for one that does not, so absence here is a
    reading rather than a gap: this is a legacy PCI device. That is why the
    answer is a plain bool on this platform and a tri-state on the node.
    """
    return any(
        value is not None
        for value in (
            entry.max_link_speed,
            entry.max_link_width,
            entry.current_link_speed,
            entry.current_link_width,
            entry.pcie_port_type,
        )
    )


def _class_code(entry: PciEntry) -> int | None:
    """Return the PCI class triple as an integer."""
    return parse_int(entry.class_code, 16)


def controller_kind_of(class_code: int | None) -> ControllerKind:
    """Map a PCI class triple onto a controller kind.

    Args:
        class_code: The class triple, for example ``0x010700``.

    Returns:
        The controller kind, ``UNKNOWN`` when the device is not storage.

    Example:
        >>> controller_kind_of(0x010700)
        <ControllerKind.SAS: 'sas'>
        >>> controller_kind_of(0x030000)
        <ControllerKind.UNKNOWN: 'unknown'>
    """
    if class_code is None or (class_code >> 16) != _STORAGE_CLASS:
        return ControllerKind.UNKNOWN
    return _CONTROLLER_KINDS.get((class_code >> 8) & 0xFF, ControllerKind.OTHER)


def _host_details(capture: LinuxCapture) -> dict[str, ScsiHostEntry]:
    """Index SCSI hosts by the PCI address of their controller."""
    details: dict[str, ScsiHostEntry] = {}
    for entry in capture.classes.scsi_host.values():
        address = controller_address_of(entry.path)
        if address is None:
            continue
        # A card with several hosts reports the same board on each, so the first
        # host that names a board wins and the rest add nothing.
        if entry.board_name or address not in details:
            details[address] = entry
    return details


def _ahci_capabilities(entry: PciEntry) -> AhciCapabilities | None:
    """Decode the AHCI registers a privileged run recorded for one controller."""
    registers = entry.ahci
    return None if registers is None else decode_capabilities(registers.capability, registers.ports_implemented)


def _port_counts(capture: LinuxCapture) -> dict[str, int]:
    """Count physical ports or phys per controller.

    An AHCI controller is counted from its own ports-implemented bitmap and from
    nothing else. ``libata`` creates one ``ata_port`` per *declared* port, so
    counting those reports the capability register's port-count field rather
    than the sockets the board wired: a chipset commonly declares six and
    implements two. Where the bitmap cannot be read, or firmware leaves it at
    zero, the count is dropped rather than guessed, because advertising free
    ports that are not physically there sends somebody looking for connectors
    that do not exist.

    SAS phys are counted directly, as each ``sas_phy`` entry is a real phy.
    """
    counts: dict[str, int] = {}
    for entry in (*capture.classes.sas_phy.values(), *capture.classes.ata_port.values()):
        address = controller_address_of(entry.path)
        if address is not None:
            counts[address] = counts.get(address, 0) + 1

    for address, entry in capture.pci.items():
        if controller_kind_of(_class_code(entry)) is not ControllerKind.AHCI:
            continue
        capabilities = _ahci_capabilities(entry)
        if capabilities is not None and capabilities.ports_implemented:
            counts[address] = capabilities.ports_implemented
        else:
            counts.pop(address, None)
    return counts


def _ahci_port_speed(capture: LinuxCapture, address: str | None) -> float | None:
    """Return what one AHCI controller's ports can carry, in Gb/s."""
    entry = None if address is None else capture.pci.get(address)
    capabilities = None if entry is None else _ahci_capabilities(entry)
    return None if capabilities is None else capabilities.interface_speed_gbps


def build_controllers(capture: LinuxCapture) -> tuple[Controller, ...]:
    """Build every storage controller found in a capture.

    Args:
        capture: A Linux reading.

    Returns:
        Controllers in PCI address order.
    """
    devices = capture.pci
    hosts = _host_details(capture)
    ports = _port_counts(capture)
    database = _pci_database(capture)

    controllers: list[Controller] = []
    for address, entry in sorted(devices.items()):
        kind = controller_kind_of(_class_code(entry))
        if kind is ControllerKind.UNKNOWN:
            continue
        host = hosts.get(address)
        parent = _parent_address(entry.path)
        upstream = devices.get(parent) if parent else None
        controllers.append(
            Controller(
                address=address,
                name=(None if host is None else host.board_name) or _pci_name(entry, database),
                kind=kind,
                driver=entry.driver,
                firmware=None if host is None else host.version_fw,
                link=_pcie_link(entry),
                upstream=None if upstream is None else _pcie_link(upstream),
                upstream_name=None if upstream is None else _pci_name(upstream, database),
                upstream_address=parent,
                vendor=parse_int(entry.vendor, 16),
                port_count=ports.get(address),
                ports_used=None,
                readings_refused=refusals_of({"ahci-port-count": entry.ahci_error}),
            )
        )
    return tuple(controllers)


def _pci_database(capture: LinuxCapture) -> pciids.Database | None:
    """Return the identifier database carried in a capture, when it has one.

    A capture taken on one machine may be replayed on another that has a
    different ``pci.ids``, or none, so the reader records the names it resolved.
    """
    if not capture.pci_names:
        return None
    devices: dict[tuple[int, int], str] = {}
    for key, value in capture.pci_names.items():
        vendor_text, _, device_text = key.partition(":")
        vendor, device = parse_int(vendor_text, 16), parse_int(device_text, 16)
        if vendor is not None and device is not None:
            devices[(vendor, device)] = value
    return Database({}, devices)


def _pci_name(entry: PciEntry, database: pciids.Database | None) -> str:
    """Return a readable name for one PCI device."""
    vendor = parse_int(entry.vendor, 16)
    device = parse_int(entry.device, 16)
    if vendor is None or device is None:
        return "Unknown controller"
    return pciids.describe(vendor, device, database)


def build_slots(capture: LinuxCapture) -> tuple[PcieSlot, ...]:
    """Build the list of PCIe ports, what occupies them and whether they are slots.

    Args:
        capture: A Linux reading.

    Returns:
        Ports in PCI address order.
    """
    devices = capture.pci
    database = _pci_database(capture)
    slots: list[PcieSlot] = []
    for address, entry in sorted(devices.items()):
        class_code = _class_code(entry)
        if class_code is None or (class_code >> 8) != _BRIDGE_CLASS:
            continue
        present = [child for child in entry.children if child in devices]
        chosen = representative_occupant(
            [
                PortChild(child, _class_code(devices[child]), _pcie_link(devices[child]).max_bandwidth_gbps)
                for child in present
            ]
        )
        occupant = devices.get(chosen.address) if chosen is not None else None
        slots.append(
            PcieSlot(
                address=address,
                link=_pcie_link(entry),
                occupied=bool(present),
                connector_present=entry.slot_implemented,
                occupant_address=None if chosen is None else chosen.address,
                occupant_class=None if occupant is None else _class_code(occupant),
                occupant_name=None if occupant is None else _pci_name(occupant, database),
                occupant_link=None if occupant is None else _pcie_link(occupant),
                physical_slot_number=entry.slot_number,
                vendor=parse_int(entry.vendor, 16),
                occupant_vendor=None if occupant is None else parse_int(occupant.vendor, 16),
                occupant_count=len(present),
            )
        )
    return tuple(slots)


class _HwmonReading(NamedTuple):
    """One hardware monitor's temperature, and where it sat in the capture.

    The position is kept because a disk can own several monitors and the
    answer is the first of them IN CAPTURE ORDER, which a lookup keyed by
    path cannot otherwise recover.

    Attributes:
        position: The monitor's index among the capture's hwmon entries.
        temperature_c: Its temperature in whole degrees Celsius.
    """

    position: int
    temperature_c: int


class _IndexedCapture(NamedTuple):
    """A capture and the three lookups its disks are resolved through.

    Each is built ONCE for the whole capture rather than walked per disk. The
    scans they replace were O(disks x entries), so doubling a capture cost
    3.8 times as much rather than twice: measured over 2,000 against 4,000
    crafted disks, 93 percent of an ATA build sat in the libata scan and 85
    percent of an NVMe one in the class-entry scan. A capture is untrusted
    input admitted up to 64 MB, which is hundreds of thousands of entries.

    Attributes:
        capture: The reading every other value is read from.
        ata_links: libata links, keyed by the ``ataN`` tokens in their paths.
        nvme_classes: NVMe controllers, keyed by their own sysfs paths.
        hwmon_readings: Temperatures, keyed by the monitor's sysfs path.
    """

    capture: LinuxCapture
    ata_links: Mapping[str, AtaLinkEntry]
    nvme_classes: Mapping[str, NvmeClassEntry]
    hwmon_readings: Mapping[str, _HwmonReading]


def _index_capture(capture: LinuxCapture) -> _IndexedCapture:
    """Build every per-disk lookup a capture needs, once for all of them."""
    return _IndexedCapture(
        capture=capture,
        ata_links=_ata_links_by_port(capture),
        nvme_classes=_nvme_classes_by_path(capture),
        hwmon_readings=_hwmon_readings_by_path(capture),
    )


def _ata_links_by_port(capture: LinuxCapture) -> dict[str, AtaLinkEntry]:
    """Key each libata link by every ``ataN`` port token its path carries.

    The first entry wins, which is the one the per-disk scan returned: that
    took the first match in capture order too.
    """
    links: dict[str, AtaLinkEntry] = {}
    for entry in capture.classes.ata_link.values():
        for match in _ATA_PORT_ANYWHERE.finditer(entry.path):
            links.setdefault(match.group(1), entry)
    return links


def _nvme_classes_by_path(capture: LinuxCapture) -> dict[str, NvmeClassEntry]:
    """Key each NVMe controller by its own sysfs path, the first entry winning."""
    classes: dict[str, NvmeClassEntry] = {}
    for entry in capture.classes.nvme.values():
        if entry.path:
            classes.setdefault(entry.path, entry)
    return classes


def _hwmon_readings_by_path(capture: LinuxCapture) -> dict[str, _HwmonReading]:
    """Key every monitor that published a readable temperature by its path.

    A monitor whose reading does not parse is left out rather than stored as
    absent, because the scan this replaces walked past such an entry and kept
    looking rather than giving up on the disk.
    """
    readings: dict[str, _HwmonReading] = {}
    for position, entry in enumerate(capture.classes.hwmon.values()):
        raw = parse_int(entry.temp1_input)
        if raw is not None:
            readings.setdefault(entry.path, _HwmonReading(position, round(raw / _MILLIDEGREE)))
    return readings


def _self_and_ancestors(path: str) -> Iterator[str]:
    """`path`, then each directory above it, nearest first."""
    while path:
        yield path
        path = path.rpartition("/")[0]


def _phy_for(device_path: str, capture: LinuxCapture) -> SasPhyEntry | None:
    """Find the SAS phy a disk is attached through."""
    match = _SAS_PORT.search(device_path + "/")
    if match is None:
        return None
    return capture.classes.sas_phy.get(f"phy-{match.group(1)}")


def _ata_link_for(device_path: str, indexed: _IndexedCapture) -> AtaLinkEntry | None:
    """Find the ATA link a disk is attached through."""
    match = _ATA_PORT.search(device_path + "/")
    if match is None:
        return None
    return indexed.ata_links.get(match.group(1))


def _ata_identity(block: BlockEntry, ata: AtaBlobs) -> AtaIdentity | None:
    """Decode a drive's identity, preferring the privileged reading.

    Both sources carry the same structure. The passthrough reading is preferred
    only because it is present whenever it succeeded; the sysfs VPD page is the
    unprivileged fallback and yields the same answer.
    """
    sources: tuple[tuple[str | None, Callable[[bytes], AtaIdentity]], ...] = (
        (ata.identify, decode_identify),
        (block.vpd.vpd_pg89, decode_vpd_ata_information),
    )
    for raw, decode in sources:
        payload = decode_base64(raw)
        if not payload:
            continue
        try:
            return decode(payload)
        except ValueError:
            continue
    return None


def _sata_link(
    identity: AtaIdentity | None,
    phy: SasPhyEntry | None,
    ata_link: AtaLinkEntry | None,
    controller_speed: float | None = None,
) -> InterfaceLink:
    """Assemble a SATA or SAS interface link from every source that has one.

    The negotiated rate has three possible sources because no single one covers
    every topology: IDENTIFY word 77 is silent on older drives, ``ata_link``
    reports nothing for a drive behind a SAS HBA, and not every HBA populates
    its phy rates.
    """
    negotiated = identity.negotiated_gbps if identity else None
    if negotiated is None and phy is not None:
        negotiated = parse_link_rate(phy.negotiated_linkrate)
    if negotiated is None and ata_link is not None:
        negotiated = parse_link_rate(ata_link.sata_spd)

    port_max = parse_link_rate(phy.maximum_linkrate_hw) if phy else None
    if port_max is None and ata_link is not None:
        port_max = parse_link_rate(ata_link.sata_spd_max)
    # The AHCI controller's own capability register, which is the only place a
    # SATA port's speed is published once libata has no limit to report.
    if port_max is None:
        port_max = controller_speed

    return InterfaceLink(
        negotiated_gbps=negotiated,
        drive_max_gbps=identity.max_gbps if identity else None,
        port_max_gbps=port_max,
    )


def _hwmon_temperature(paths: Sequence[str], indexed: _IndexedCapture) -> int | None:
    """Read a device's temperature from the hwmon nodes it owns.

    A device can own several, so the answer is the first of them in CAPTURE
    order rather than in the order the device lists them, which is what the
    scan this replaces returned.
    """
    readings = [reading for path in paths if (reading := indexed.hwmon_readings.get(path)) is not None]
    if not readings:
        return None
    return min(readings, key=lambda reading: reading.position).temperature_c


def _bus_of(identity: AtaIdentity | None, phy: SasPhyEntry | None) -> BusType:
    """Tell SATA from SAS for a disk that is already known not to be NVMe.

    `build_disks` routes a node by its name, so an nvme one goes to
    `_build_nvme_disk` and never arrives here. What is left is decided by
    whether the drive answered ATA IDENTIFY, which the node name cannot say.
    """
    if identity is not None or phy is not None:
        # A drive that answers ATA IDENTIFY is SATA even when it is tunnelled
        # through a SAS expander; a drive that does not is native SAS.
        return BusType.SATA if identity is not None else BusType.SAS
    return BusType.UNKNOWN


def _size_bytes(block: BlockEntry, identity: AtaIdentity | None) -> int | None:
    """Return a disk's capacity, preferring the kernel's own figure.

    A sector count outside what the kernel could have published is read as
    no reading at all, the same answer a size of ``Unknown`` already gets,
    rather than multiplied into a capacity. Measured unbounded: a 40-digit
    count printed ``4547473508864641327721086976PiB`` as a measurement, and a
    320-digit one ended ``lsdsk disks`` in a bare OverflowError from the
    float conversion inside the formatter while every other view exited 0.
    """
    sectors = parse_int(block.size)
    if sectors is not None and 0 <= sectors <= _MAX_SYSFS_SECTORS:
        return sectors * _SYSFS_SECTOR_BYTES
    return identity.size_bytes if identity else None


def _build_nvme_disk(node: str, block: BlockEntry, indexed: _IndexedCapture) -> Disk:
    """Build one NVMe disk from a capture."""
    capture = indexed.capture
    record = capture.nvme.get(node, NvmeBlobs())
    identity = None
    controller_blob = decode_base64(record.identify_controller)
    if controller_blob is not None:
        try:
            identity = decode_identify_controller(controller_blob)
        except ValueError:
            identity = None

    health: Health | None = None
    log_blob = decode_base64(record.smart_log)
    if log_blob is not None:
        try:
            health = decode_smart_log(log_blob, identity)
        except ValueError:
            health = None

    temperature = _hwmon_temperature(block.hwmon, indexed)
    if health is None and temperature is not None:
        health = Health(temperature_c=temperature)

    address = controller_address_of(block.device_path)
    endpoint = capture.pci.get(address) if address else None
    # The controller publishes its model, serial and firmware in sysfs, readable
    # without any privilege. Falling back to it means an unprivileged run still
    # names the drive instead of showing three dashes.
    published = _nvme_class_entry(indexed, block.device_path)
    return Disk(
        node=node,
        path=f"/dev/{node}",
        model=first_reported(identity.model if identity else None, published.model) or node,
        serial=first_reported(identity.serial if identity else None, published.serial),
        firmware=first_reported(identity.firmware if identity else None, published.firmware_rev),
        wwn=_stable_identifier(block),
        size_bytes=_size_bytes(block, None),
        kind=DiskKind.SSD,
        bus=BusType.NVME,
        controller_address=address,
        pcie=None if endpoint is None else _pcie_link(endpoint),
        health=health,
        readings_refused=refusals_of(
            {
                "device": record.error,
                "identify-controller": record.identify_controller_error,
                "smart-log": record.smart_log_error,
            }
        ),
    )


def _nvme_class_entry(indexed: _IndexedCapture, device_path: str) -> NvmeClassEntry:
    """Find the nvme class entry for one namespace's device path.

    The entry sits AT that path or above it: sysfs publishes a controller's
    class entry at the controller, and a namespace's device path is either
    the controller itself or a directory under it. So the answer is the
    NEAREST ancestor of the device path that published one.

    An entry BELOW the device path is not one, though the scan this replaces
    accepted that direction too. It names something the namespace contains
    rather than the controller the namespace hangs off, and the raw string
    prefix it was tested with matched `/nvme/nvme01` against a device path of
    `/nvme/nvme0` - two different controllers.
    """
    for candidate in _self_and_ancestors(device_path):
        entry = indexed.nvme_classes.get(candidate)
        if entry is not None:
            return entry
    return NvmeClassEntry()


def _build_ata_disk(node: str, block: BlockEntry, indexed: _IndexedCapture) -> Disk:
    """Build one SATA or SAS disk from a capture."""
    capture = indexed.capture
    ata = capture.ata.get(node, AtaBlobs())
    device_path = block.device_path
    address = controller_address_of(device_path)
    identity = _ata_identity(block, ata)
    phy = _phy_for(device_path, capture)
    ata_link = _ata_link_for(device_path, indexed)

    health: Health | None = None
    data = decode_base64(ata.smart_data)
    if data is not None:
        try:
            health = decode_health(data, decode_base64(ata.smart_thresholds))
        except ValueError:
            health = None

    device = block.device
    rotating = block.queue.rotational
    kind = DiskKind.UNKNOWN
    if identity is not None:
        kind = identity.kind
    elif rotating is not None:
        kind = DiskKind.HDD if rotating else DiskKind.SSD

    return Disk(
        node=node,
        path=f"/dev/{node}",
        model=first_reported(identity.model if identity else None, device.model) or node,
        serial=first_reported(identity.serial if identity else None),
        firmware=first_reported(identity.firmware if identity else None, device.rev),
        wwn=_stable_identifier(block),
        size_bytes=_size_bytes(block, identity),
        kind=kind,
        bus=_bus_of(identity, phy),
        controller_address=address,
        link=_sata_link(identity, phy, ata_link, _ahci_port_speed(capture, address)),
        health=health,
        readings_refused=refusals_of(
            {
                "device": ata.error,
                "identify": ata.identify_error,
                "smart-data": ata.smart_data_error,
                "smart-thresholds": ata.smart_thresholds_error,
            }
        ),
    )


def _stable_identifier(block: BlockEntry) -> str | None:
    """Return the identifier that follows a drive between bays.

    A namespace UUID is preferred when the drive publishes one, otherwise the
    world-wide name. Both carry their own prefix, so the value says what it is.
    """
    if block.uuid:
        return f"uuid.{block.uuid}"
    for wwid in (block.wwid, block.device.wwid):
        if wwid:
            return wwid.strip()
    return None


def _is_kernel_virtual(block: BlockEntry) -> bool:
    """Whether the reader found this device under the kernel's virtual tree.

    The reader answers this from where sysfs puts the device, so the builder
    only has to carry the answer. Reading it from the node name here would
    reintroduce the guess the reader exists to avoid.
    """
    return block.virtual


def _build_one(node: str, block: BlockEntry, indexed: _IndexedCapture) -> Disk:
    """Build one disk, routed by whether its node is an NVMe namespace."""
    builder = _build_nvme_disk if node.startswith("nvme") else _build_ata_disk
    return builder(node, block, indexed)


def build_disks(capture: LinuxCapture) -> tuple[Disk, ...]:
    """Build every physical disk found in a capture.

    Args:
        capture: A Linux reading.

    Returns:
        Disks in node order, kernel-virtual devices excluded.
    """
    indexed = _index_capture(capture)
    return tuple(
        _build_one(node, block, indexed)
        for node, block in sorted(capture.block.items())
        if not _is_kernel_virtual(block)
    )


def build_virtual_disks(capture: LinuxCapture) -> tuple[Disk, ...]:
    """Build every kernel-virtual block device found in a capture.

    Two values are set here rather than inferred, because the general mapping
    reads them as measurements and for these devices they are not.

    The transport: left to `_bus_of`, a device with no ATA identity and no phy
    comes out ``unknown``, which claims it could not be read. Nothing failed to
    be read; the kernel already said there is no transport at all.

    The media: ``queue/rotational`` is 0 for loop, zd and zram alike, which the
    ordinary mapping turns into SSD. It is a default the kernel fills in for a
    device with no media, so believing it made a zvol on a pool of spinning
    disks report solid state.

    Args:
        capture: A Linux reading.

    Returns:
        Kernel-virtual devices in node order, each on ``BusType.VIRTUAL`` and
        with no claim about its media.
    """
    indexed = _index_capture(capture)
    return tuple(
        _build_one(node, block, indexed).with_changes(bus=BusType.VIRTUAL, kind=DiskKind.UNKNOWN)
        for node, block in sorted(capture.block.items())
        if _is_kernel_virtual(block)
    )


def build_tree(capture: LinuxCapture) -> tuple[PciNode, ...]:
    """Build the whole PCI fabric as a root-down tree.

    Parentage comes from the sysfs ``path``, which carries a device's entire
    ancestry, rather than from the ``children`` lists: measured on every
    committed Linux fixture the two agree, and the path automatically excludes
    the non-PCI children the reader's address filter admits.

    Args:
        capture: A Linux reading.

    Returns:
        Every PCI device as :class:`~lsdsk.domain.models.PciNode` roots and
        children, in address order.
    """
    database = _pci_database(capture)
    return assemble(
        tuple(
            NodeSource(
                address=address,
                name=_pci_name(entry, database),
                class_code=_class_code(entry),
                vendor=parse_int(entry.vendor, 16),
                driver=entry.driver,
                link=_pcie_link(entry),
                pcie_capability_present=_pcie_capability_present(entry),
                port_kind=port_kind_of(entry.pcie_port_type),
                connector_present=entry.slot_implemented,
                physical_slot_number=entry.slot_number,
                parent=_parent_address(entry.path),
            )
            for address, entry in sorted(capture.pci.items())
        )
    )


def build_inventory(capture: LinuxCapture) -> Inventory:
    """Turn a whole Linux reading into an inventory.

    Args:
        capture: A Linux reading, live or replayed, already parsed.

    Returns:
        The machine as the domain sees it.

    Example:
        >>> from lsdsk.adapters.hw.linux.capture import LinuxCapture
        >>> reading = {"schema": 2, "platform": "linux", "hostname": "example", "kernel": "6.1.0", "pci": {}}
        >>> build_inventory(LinuxCapture.model_validate(reading)).hostname
        'example'
    """
    disks = build_disks(capture)
    controllers = build_controllers(capture)

    used: dict[str, int] = {}
    for disk in disks:
        if disk.controller_address is not None:
            used[disk.controller_address] = used.get(disk.controller_address, 0) + 1

    environment, detail = classify(capture.environment)

    return Inventory(
        hostname=device_text(capture.hostname) or "unknown",
        # A copy, not a rebuild: only ports_used is known this late, and
        # restating every other field here would silently drop any field
        # Controller gains later.
        controllers=tuple(
            controller.with_changes(ports_used=used.get(controller.address, 0)) for controller in controllers
        ),
        disks=disks,
        virtual_disks=build_virtual_disks(capture),
        slots=build_slots(capture),
        pci_tree=build_tree(capture),
        privileged=capture.euid == 0,
        environment=environment,
        environment_detail=detail,
        board=board_name(capture.environment),
        devices_accessible=capture.devices_accessible,
    )


__all__ = [
    "build_controllers",
    "build_disks",
    "build_inventory",
    "build_slots",
    "build_tree",
    "build_virtual_disks",
    "controller_address_of",
    "controller_kind_of",
    "parse_link_rate",
    "parse_pcie_speed",
]
