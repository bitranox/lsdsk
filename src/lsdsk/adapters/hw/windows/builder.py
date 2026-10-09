"""Turn a captured Windows reading into the domain inventory.

Pure, like its Linux counterpart, so the whole Windows mapping path is testable
on any operating system against captures taken from real machines. It takes the
typed reading :mod:`.capture` parses from the mapping :mod:`.reader` produces.

It shares its counterpart's one exception, and for the same reason: a capture
recording no ``pci_names`` leaves nothing here to name a device from, so
:func:`~lsdsk.adapters.hw.decode.pciids.describe` falls back on the replaying
machine's ``pci.ids`` - which on a Windows capture rendered on Linux means the
device is named by a file the captured machine never had.

Windows names devices by instance identifier rather than by PCI address, so the
tree is walked by parentage instead of by path.  The disks a controller carries
are found by walking up from each disk until a PCI device is reached.

System Role:
    Adapter layer, translation half.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypeVar

from ....domain.disk_name import disk_name_order
from ....domain.enums import BusType, ControllerKind, DiskKind, PciPortKind, UsbLaneRate, UsbTransport
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
    RefusedReading,
    UsbLink,
    UsbSpeed,
    representative_occupant,
)
from ....domain.pci_address import pci_address_order
from ....domain.text import device_text, first_reported
from ..decode import pciids
from ..decode.ata_identify import decode_identify
from ..decode.ata_smart import decode_health
from ..decode.captured import decode_base64, parse_int, parse_pci_id
from ..decode.nvme import decode_identify_controller, decode_smart_log
from ..decode.usb import (
    decode_bos,
    decode_connection,
    decode_connection_v2,
    decode_connector,
    decode_hub_type,
    decode_superspeedplus,
    fastest,
)
from ..decode.virtualization import board_name, classify
from ..fabric import NodeSource, assemble
from ..linux.builder import controller_kind_of, parse_pcie_running_width, parse_pcie_speed, parse_pcie_width
from ..refusals import refusals_of
from .capture import HealthBlobs
from .usage import resolve_usage

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..decode.ata_identify import AtaIdentity
    from ..decode.nvme import NvmeIdentity
    from .capture import DiskEntry, PciEntry, UsbPortEntry, WindowsCapture

# A Windows disk interface path ends with the device instance, from which the
# familiar PhysicalDrive-style name cannot be recovered, so the path is shown.
_DISK_INDEX = re.compile(r"PhysicalDrive(\d+)", re.IGNORECASE)

_Decoded = TypeVar("_Decoded")


def _pcie_link(entry: PciEntry) -> PcieLink:
    """Build a PCIe link from one captured device's properties."""
    return PcieLink(
        current_speed_gtps=parse_pcie_speed(entry.current_link_speed),
        current_width=parse_pcie_running_width(entry.current_link_width),
        max_speed_gtps=parse_pcie_speed(entry.max_link_speed),
        max_width=parse_pcie_width(entry.max_link_width),
    )


def _pcie_capability_present(entry: PciEntry) -> bool | None:
    """Whether this device has a PCIe capability, as far as Windows says.

    ``True`` where a link property came back, and ``None`` otherwise, never
    ``False``: Windows publishes the link registers of an endpoint and of a
    bridge nothing at all, so a device with no values did not answer the
    question rather than answering no. Calling that a legacy PCI device would
    state a measurement nobody took, on every bridge of every Windows machine.
    """
    published = any(
        value is not None
        for value in (
            entry.max_link_speed,
            entry.max_link_width,
            entry.current_link_speed,
            entry.current_link_width,
        )
    )
    return True if published else None


def _class_code(entry: PciEntry) -> int | None:
    """Return the PCI class triple as an integer."""
    return parse_int(entry.class_code, 16)


def controller_of(ancestry: Sequence[str], pci: Mapping[str, PciEntry]) -> str | None:
    r"""Return the nearest PCI device above a disk, which is the controller it hangs off.

    A disk's parent is a PCI device only when the controller's own driver
    presents the disk. Behind a USB bridge the parent is the mass-storage device,
    above that sits a hub, and only above those the host controller, so the whole
    ancestry is searched rather than the parent alone. That is the answer Linux
    gives by walking a disk's sysfs path.

    Args:
        ancestry: The instance identifiers above the disk, nearest first.
        pci: Every captured PCI device, keyed by instance identifier.

    Returns:
        The controller's instance identifier, or ``None`` when no PCI device is
        above the disk, as for a disk a software bus driver presents.

    Example:
        >>> from lsdsk.adapters.hw.windows.capture import PciEntry
        >>> pci = {"PCI\\VEN_8086&DEV_7AE0\\3": PciEntry()}
        >>> controller_of(["USBSTOR\\DISK\\1", "USB\\ROOT_HUB30\\4", "PCI\\VEN_8086&DEV_7AE0\\3"], pci)
        'PCI\\VEN_8086&DEV_7AE0\\3'
        >>> controller_of(["ROOT\\VHDMP\\0000"], pci) is None
        True
    """
    return next((instance for instance in ancestry if instance in pci), None)


def _ancestry_of(entry: DiskEntry) -> tuple[str, ...]:
    """Return the instance identifiers above a disk, nearest first.

    A capture taken before the reader recorded the whole ancestry holds only the
    parent, which is then all there is to search.
    """
    if entry.ancestors:
        return entry.ancestors
    return (entry.parent,) if entry.parent else ()


def _controller_name(entry: PciEntry, instance: str, database: pciids.Database | None) -> str:
    """Name one controller, preferring the language-neutral PCI database.

    Windows' own device description is localised, so on a German install an
    NVMe controller reads "Standardmaessiger NVM Express-Controller" and the
    output is half English. The numeric vendor and device identifiers say the
    same thing in one language, and resolving them is what makes a controller
    read identically here and on Linux.

    The operating system's description is kept only where the identifiers are
    missing, which is the case where there is nothing else to say.

    Args:
        entry: One PCI device from the capture.
        instance: The device instance path, used when nothing else names it.
        database: The names the capturing machine resolved, or ``None`` where
            it recorded none. Passed in rather than looked up here, because a
            lookup would read the REPLAYING machine's ``pci.ids`` and name a
            Windows capture after whatever box is rendering it.

    Returns:
        A name for the controller.
    """
    vendor = parse_pci_id(entry.vendor)
    device = parse_pci_id(entry.device)
    if vendor is not None and device is not None:
        return pciids.describe(vendor, device, database)
    return entry.name or instance


def build_controllers(capture: WindowsCapture) -> tuple[Controller, ...]:
    """Build every storage controller found in a Windows capture.

    Args:
        capture: The typed reading, live or replayed.

    Returns:
        One controller per storage device in the capture.
    """
    devices = capture.pci
    database = pciids.database_from_names(capture.pci_names)
    controllers: list[Controller] = []
    for instance, entry in sorted(devices.items()):
        kind = controller_kind_of(_class_code(entry))
        if kind is ControllerKind.UNKNOWN:
            continue
        parent = devices.get(entry.parent or "")
        controllers.append(
            Controller(
                address=entry.address or instance,
                name=_controller_name(entry, instance, database),
                kind=kind,
                driver=entry.driver,
                link=_pcie_link(entry),
                upstream=None if parent is None else _pcie_link(parent),
                # Windows publishes no link registers for a PCIe bridge, so this
                # string is the only thing said about the port at all.
                upstream_name=None if parent is None else parent.name or None,
                upstream_address=None if parent is None else parent.address or entry.parent,
                vendor=parse_pci_id(entry.vendor),
            )
        )
    return tuple(sorted(controllers, key=lambda controller: pci_address_order(controller.address)))


def build_slots(capture: WindowsCapture) -> tuple[PcieSlot, ...]:
    """Build the PCIe ports a card could move between.

    Windows exposes no equivalent of the Slot Implemented bit through the device
    properties, so ``connector_present`` stays unknown and the placement rules
    never propose moving a card.  That is the honest outcome: a recommendation
    that might send someone hunting for a slot that does not exist is worse than
    the platform-ceiling hint, which is still produced.

    Args:
        capture: The typed reading, live or replayed.

    Returns:
        One port per PCIe device that could carry a card.
    """
    devices = capture.pci
    slots: list[PcieSlot] = []
    for instance, entry in sorted(devices.items()):
        class_code = _class_code(entry)
        if class_code is None or (class_code >> 8) != 0x0604:  # noqa: PLR2004 - the PCI-to-PCI bridge class
            continue
        # Keyed by the address the port would report, so the rule's tie-break
        # falls to the lowest ADDRESS rather than to an instance-id ordering.
        behind = {(devices[child].address or child): devices[child] for child in entry.children if child in devices}
        chosen = representative_occupant(
            [
                PortChild(address, _class_code(child), _pcie_link(child).max_bandwidth_gbps)
                for address, child in behind.items()
            ]
        )
        occupant = None if chosen is None else behind[chosen.address]
        slots.append(
            PcieSlot(
                address=entry.address or instance,
                link=_pcie_link(entry),
                occupied=bool(behind),
                connector_present=None,
                occupant_address=None if chosen is None else chosen.address,
                occupant_class=None if occupant is None else _class_code(occupant),
                occupant_name=None if occupant is None else occupant.name,
                occupant_link=None if occupant is None else _pcie_link(occupant),
                physical_slot_number=entry.slot_number,
                vendor=parse_pci_id(entry.vendor),
                occupant_vendor=None if occupant is None else parse_pci_id(occupant.vendor),
                occupant_count=len(behind),
            )
        )
    return tuple(sorted(slots, key=lambda slot: pci_address_order(slot.address)))


def _health_from(
    record: HealthBlobs, entry: DiskEntry, *, bus: BusType, nvme_identity: NvmeIdentity | None
) -> Health | None:
    """Decode whatever health data the capture holds for one disk.

    Takes the transport rather than a flag: the caller already knows it, and a
    bool can only ever say "NVMe or not", so a third transport needing its own
    decode would fall silently into the ATA branch. Takes the NVMe identity the
    caller decoded too, so one blob is decoded once.
    """
    if bus is BusType.NVME:
        log_blob = decode_base64(record.smart_log)
        if log_blob is not None:
            try:
                return decode_smart_log(log_blob, nvme_identity)
            except ValueError:
                return None
    else:
        data = decode_base64(record.smart_data)
        if data is not None:
            try:
                return decode_health(data, decode_base64(record.smart_thresholds))
            except ValueError:
                return None

    # No passthrough, but the storage stack may still have offered a temperature.
    temperature = entry.temperature
    if temperature is not None:
        return Health(
            temperature_c=temperature.temperature_c,
            temperature_warning_c=temperature.warning_c,
            temperature_critical_c=temperature.critical_c,
        )
    return None


# Transports Windows names for a disk it reaches through an adapter without
# telling SATA from SAS: the drive behind decides, as on Linux.
_ADAPTER_BUSES = frozenset({BusType.SAS, BusType.UNKNOWN})


def _bus_of(bus: BusType, *, identity: AtaIdentity | None, usb: UsbLink | None) -> BusType:
    """Say which bus a disk is on, as the Linux builder does for the same drive.

    A drive that answers ATA IDENTIFY is SATA even when the adapter in front of
    it (an HBA, a RAID volume) is reported as SAS, SCSI or RAID. A USB disk and
    a hypervisor's disk keep what they are.

    Args:
        bus: The transport Windows reported.
        identity: The decoded IDENTIFY answer, when the drive gave one.
        usb: The disk's USB link, when it hangs off one.

    Returns:
        The bus to show.
    """
    if usb is not None:
        return BusType.USB
    if identity is not None and bus in _ADAPTER_BUSES:
        return BusType.SATA
    return bus


def build_disks(capture: WindowsCapture) -> tuple[Disk, ...]:
    """Build every disk found in a Windows capture.

    Args:
        capture: The typed reading, live or replayed.

    Returns:
        One disk per drive in the capture, virtual devices included.
    """
    pci = capture.pci
    disks: list[Disk] = []

    for path, entry in sorted(capture.disks.items()):
        device = entry.device
        bus = device.bus_type
        is_nvme = bus is BusType.NVME
        record = (entry.nvme if is_nvme else entry.ata) or HealthBlobs()
        usb_chain = _usb_chain(entry, capture)
        usb = _usb_link(usb_chain, capture)

        # Decoded whenever the reader got an IDENTIFY answer, whatever transport
        # Windows names: a SATA drive behind an LSI HBA or a RAID volume is
        # reported as sas/scsi/raid, and an answer to ATA IDENTIFY is what makes
        # a drive SATA. A USB disk's came through the bridge by SAT passthrough.
        identity = None
        blob = None if is_nvme else decode_base64(record.identify)
        if blob is not None:
            try:
                identity = decode_identify(blob)
            except ValueError:
                identity = None

        nvme_identity = None
        if is_nvme:
            blob = decode_base64(record.identify_controller)
            if blob is not None:
                try:
                    nvme_identity = decode_identify_controller(blob)
                except ValueError:
                    nvme_identity = None

        controller_instance = controller_of(_ancestry_of(entry), pci)
        endpoint = pci.get(controller_instance) if controller_instance else None
        controller = controller_instance if endpoint is None else endpoint.address or controller_instance
        rotating = entry.rotating
        kind = DiskKind.UNKNOWN
        if identity is not None:
            kind = identity.kind
        elif is_nvme:
            kind = DiskKind.SSD
        elif rotating is not None:
            kind = DiskKind.HDD if rotating else DiskKind.SSD

        model = first_reported(
            nvme_identity.model if nvme_identity else None,
            identity.model if identity else None,
            device.model,
        )
        node = _node_name(entry, path)
        disks.append(
            Disk(
                node=node,
                path=node,
                model=model or "unknown",
                serial=first_reported(
                    nvme_identity.serial if nvme_identity else None,
                    identity.serial if identity else None,
                    device.serial,
                ),
                firmware=first_reported(
                    nvme_identity.firmware if nvme_identity else None,
                    identity.firmware if identity else None,
                    device.rev,
                ),
                size_bytes=entry.size_bytes,
                kind=kind,
                bus=_bus_of(bus, identity=identity, usb=usb),
                controller_address=controller,
                # The reader records no port capability for a disk, so the port
                # end of the link stays unmeasured.
                link=InterfaceLink(
                    negotiated_gbps=identity.negotiated_gbps if identity else None,
                    drive_max_gbps=identity.max_gbps if identity else None,
                ),
                pcie=_pcie_link(endpoint) if is_nvme and endpoint is not None else None,
                usb=usb,
                health=_health_from(record, entry, bus=bus, nvme_identity=nvme_identity),
                readings_refused=_refusals_for(entry, record),
            )
        )
    return tuple(sorted(disks, key=lambda disk: disk_name_order(disk.node)))


_USB2 = UsbSpeed(lane_rate=UsbLaneRate.HIGH)
_GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
_GEN1X2 = UsbSpeed(lane_rate=UsbLaneRate.GEN1, lanes=2)
_ROOT_HUB_TYPE = 1
_TRANSPORTS = {"uaspstor": UsbTransport.UAS, "usbstor": UsbTransport.BOT}


def _decoded(payload: str | None, decoder: Callable[[bytes], _Decoded]) -> _Decoded | None:
    """Decode one recorded hub answer, or ``None`` when it was not recorded or does not decode."""
    blob = decode_base64(payload)
    if blob is None:
        return None
    try:
        return decoder(blob)
    except ValueError:
        return None


def _usb_chain(entry: DiskEntry, capture: WindowsCapture) -> list[UsbPortEntry]:
    """The port records above a disk, nearest first: its own USB device, then each hub with a record."""
    if not capture.usb_ports:
        return []
    return [capture.usb_ports[instance] for instance in _ancestry_of(entry) if instance in capture.usb_ports]


def _port_running(port: UsbPortEntry) -> UsbSpeed | None:
    """What the device in a port negotiated.

    ``..._EX`` reports high speed for a SuperSpeed device, so it is believed
    only once V2 has said the device is not running SuperSpeed - or, without
    V2, only below high speed, where the two cannot disagree. A SuperSpeedPlus
    link with no rate recorded stays unread: guessing one lane at 10 Gb/s would
    report a healthy 20 Gb/s link as slow.
    """
    protocols = _decoded(port.connection_v2, decode_connection_v2)
    if protocols is not None and protocols.operating_superspeedplus:
        return _decoded(port.superspeedplus, decode_superspeedplus)
    if protocols is not None and protocols.operating_superspeed:
        return _GEN1
    connection = _decoded(port.connection, decode_connection)
    speed = connection.speed if connection is not None else None
    if protocols is None and speed is not None and speed.signalling_mbps >= _USB2.signalling_mbps:
        return None
    return speed


def _device_capability(port: UsbPortEntry, running: UsbSpeed | None) -> UsbSpeed | None:
    """The fastest the device in a port can run: its BOS, the V2 capability flags, and what it runs.

    A BOS lists lane speeds and never a lane count, so the running link is a
    lower bound on it, as on Linux.

    A read BOS refines the answer ABOVE the V2 flags' floor and never below it.
    A flag claims no more than it proves: SuperSpeedPlus is one 10 Gb/s lane
    OR two 5 Gb/s lanes, so its floor is two Gen 1 lanes, the slower of the two
    and true of both. A BOS naming 5 Gb/s sublinks cannot say which, so taken
    alone it reads one Gen 1 lane - less than the flag proves - and the device
    would get a lower answer for having given more evidence. The floor can
    never over-claim, so the fastest of the three is always safe.
    """
    declared = _decoded(port.bos, decode_bos)
    return fastest(declared.fastest if declared is not None else None, _flag_floor(port), running)


def _flag_floor(port: UsbPortEntry) -> UsbSpeed | None:
    """The least a device can do by V2's capable-of flags alone, or ``None`` when they were not read."""
    protocols = _decoded(port.connection_v2, decode_connection_v2)
    if protocols is None:
        return None
    if protocols.superspeedplus_capable:
        return _GEN1X2
    return _GEN1 if protocols.superspeed_capable else None


def _on_usb2_twin(port: UsbPortEntry, running: UsbSpeed | None) -> bool | None:
    """Whether a device runs on the USB 2 half of a socket whose USB 3 half is another port."""
    protocols = _decoded(port.connection_v2, decode_connection_v2)
    if protocols is None:
        return None
    if protocols.port_usb3:
        return False
    connector = _decoded(port.connector, decode_connector)
    if connector is None:
        return None
    if connector.companion_port == 0:
        return False
    return None if running is None else running.signalling_mbps <= _USB2.signalling_mbps


def _socket_capability(port: UsbPortEntry, hub_port: UsbPortEntry | None, *, twin: bool | None) -> UsbSpeed | None:
    """The fastest the socket a device is plugged into can run.

    A port that speaks no USB 3 can do 480 Mb/s, which is a reading. A USB 3
    port is as fast as the external hub it belongs to says it is through that
    hub's own BOS; a root hub says only "USB 3", so its ports stay unread. The
    USB 2 half of a USB 3 socket is not the socket's capability either, and a
    twin nobody read (``None``: the connector answer is missing) is not an
    absent one - only ``False`` rules out a USB 3 half behind a USB 2 port.
    """
    protocols = _decoded(port.connection_v2, decode_connection_v2)
    if protocols is None or twin is not False:
        return None
    if not protocols.port_usb3:
        return _USB2
    if hub_port is None or _decoded(hub_port.bos, decode_bos) is None:
        return None
    return _device_capability(hub_port, _port_running(hub_port))


def _behind_hub(chain: Sequence[UsbPortEntry], capture: WindowsCapture) -> bool | None:
    """Whether a hub sits between the disk's socket and the root hub."""
    if len(chain) > 1:
        return True
    hub = capture.usb_hubs.get(chain[0].hub)
    hub_type = _decoded(hub.information, decode_hub_type) if hub is not None else None
    return None if hub_type is None else hub_type != _ROOT_HUB_TYPE


def _usb_link(chain: Sequence[UsbPortEntry], capture: WindowsCapture) -> UsbLink | None:
    """Assemble a USB disk's link from the answers of the hubs on its chain."""
    if not chain:
        return None
    port, hubs = chain[0], chain[1:]
    running = _port_running(port)
    twin = _on_usb2_twin(port, running)
    upstream = [speed for hub in hubs if (speed := _port_running(hub)) is not None]
    return UsbLink(
        running=running,
        device_max=_device_capability(port, running),
        port_max=_socket_capability(port, hubs[0] if hubs else None, twin=twin),
        behind_hub=_behind_hub(chain, capture),
        upstream=min(upstream, key=lambda speed: speed.bandwidth_gbps) if upstream else None,
        on_usb2_twin=twin,
        transport=_TRANSPORTS.get((port.service or "").lower(), UsbTransport.UNKNOWN),
    )


def _refusals_for(entry: DiskEntry, record: HealthBlobs) -> tuple[RefusedReading, ...]:
    """Name the readings this disk refused, with the reason Windows gave.

    The whole-device refusal is kept beside the per-command ones because it is
    the one an unprivileged run meets first: a device that cannot be opened
    reports nothing else at all, and ``ata``/``nvme`` are then absent rather than
    carrying a reason of their own.

    Args:
        entry: The disk as the capture holds it.
        record: Its health blobs, ATA or NVMe.

    Returns:
        One value per refused reading, empty when the disk answered everything.
    """
    return refusals_of(
        {
            "device": entry.error,
            "usb-link": entry.usb_link_error,
            "identify": record.identify_error,
            "identify-controller": record.identify_controller_error,
            "smart-data": record.smart_data_error,
            "smart-thresholds": record.smart_thresholds_error,
            "smart-log": record.smart_log_error,
        }
    )


def _node_name(entry: DiskEntry, path: str) -> str:
    """Render a disk as something a person can read.

    The interface path is a GUID-laden string no one wants in a listing, so the
    PhysicalDrive number the reader asked the operating system for is preferred.
    """
    if entry.node:
        return entry.node
    match = _DISK_INDEX.search(path)
    return f"PhysicalDrive{match.group(1)}" if match else path


def build_tree(capture: WindowsCapture) -> tuple[PciNode, ...]:
    """Build the whole PCI fabric as a root-down tree.

    Parentage comes from the recorded ``parent`` chain, keyed by instance
    identifier. A parent that is not itself a captured PCI device, such as
    the ACPI root the whole chain leaves PCI into, hands the device to the
    synthetic root of its own bus.

    Args:
        capture: A Windows reading.

    Returns:
        Every PCI device as :class:`~lsdsk.domain.models.PciNode` roots and
        children, in address order.
    """
    devices = capture.pci
    database = pciids.database_from_names(capture.pci_names)
    return assemble(
        tuple(
            NodeSource(
                address=entry.address or instance,
                name=_controller_name(entry, instance, database),
                class_code=_class_code(entry),
                vendor=parse_pci_id(entry.vendor),
                driver=entry.driver,
                link=_pcie_link(entry),
                pcie_capability_present=_pcie_capability_present(entry),
                # Windows publishes no PCIe capability port type without a
                # kernel driver, so every port reads as unknown here.
                port_kind=PciPortKind.UNKNOWN,
                # The Slot Implemented bit is likewise unreadable, so a
                # connector is never claimed.
                connector_present=None,
                physical_slot_number=entry.slot_number,
                parent=None
                if entry.parent is None
                else (devices[entry.parent].address or entry.parent)
                if entry.parent in devices
                else None,
            )
            for instance, entry in sorted(devices.items())
        )
    )


def build_inventory(capture: WindowsCapture) -> Inventory:
    """Turn a whole Windows reading into an inventory.

    Args:
        capture: A Windows reading, live or replayed, already parsed.

    Returns:
        The machine as the domain sees it.

    Example:
        >>> from lsdsk.adapters.hw.windows.capture import WindowsCapture
        >>> reading = {"schema": 2, "platform": "win32", "hostname": "vm", "kernel": "10.0", "pci": {}}
        >>> build_inventory(WindowsCapture.model_validate(reading)).hostname
        'vm'
    """
    controllers = build_controllers(capture)
    environment, detail = classify(capture.environment)

    usage = resolve_usage(capture, environment)
    disks = tuple(disk.with_changes(usage=usage.get(disk.node)) for disk in build_disks(capture))

    used: dict[str, int] = {}
    for disk in disks:
        if disk.controller_address is not None:
            used[disk.controller_address] = used.get(disk.controller_address, 0) + 1

    return Inventory(
        hostname=device_text(capture.hostname) or "unknown",
        # A copy, not a rebuild: only ports_used is known this late, and
        # restating every other field here would silently drop any field
        # Controller gains later.
        controllers=tuple(
            controller.with_changes(ports_used=used.get(controller.address, 0)) for controller in controllers
        ),
        disks=disks,
        slots=build_slots(capture),
        pci_tree=build_tree(capture),
        privileged=capture.elevated,
        environment=environment,
        environment_detail=detail,
        board=board_name(capture.environment),
        devices_accessible=capture.devices_accessible,
    )


__all__ = ["build_controllers", "build_disks", "build_inventory", "build_slots", "controller_of"]
