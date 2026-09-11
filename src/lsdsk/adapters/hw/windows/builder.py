"""Turn a captured Windows reading into the domain inventory.

Pure, like its Linux counterpart, so the whole Windows mapping path is testable
on any operating system against captures taken from real machines. It takes the
typed reading :mod:`.capture` parses from the mapping :mod:`.reader` produces.

Windows names devices by instance identifier rather than by PCI address, so the
tree is walked by parentage instead of by path.  The disks a controller carries
are found by walking up from each disk until a PCI device is reached.

System Role:
    Adapter layer, translation half.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from ....domain.enums import BusType, ControllerKind, DiskKind
from ....domain.models import (
    Controller,
    Disk,
    Health,
    InterfaceLink,
    Inventory,
    PcieLink,
    PcieSlot,
)
from ..decode import pciids
from ..decode.ata_identify import decode_identify
from ..decode.ata_smart import decode_health
from ..decode.captured import decode_base64, parse_int
from ..decode.nvme import decode_identify_controller, decode_smart_log
from ..decode.text import device_text
from ..decode.virtualization import board_name, classify
from ..linux.builder import controller_kind_of, parse_pcie_speed
from .capture import HealthBlobs

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..decode.nvme import NvmeIdentity
    from .capture import DiskEntry, PciEntry, WindowsCapture

# A Windows disk interface path ends with the device instance, from which the
# familiar PhysicalDrive-style name cannot be recovered, so the path is shown.
_DISK_INDEX = re.compile(r"PhysicalDrive(\d+)", re.IGNORECASE)


def _pcie_link(entry: PciEntry) -> PcieLink:
    """Build a PCIe link from one captured device's properties."""
    return PcieLink(
        current_speed_gtps=parse_pcie_speed(entry.current_link_speed),
        current_width=parse_int(entry.current_link_width),
        max_speed_gtps=parse_pcie_speed(entry.max_link_speed),
        max_width=parse_int(entry.max_link_width),
    )


def _class_code(entry: PciEntry) -> int | None:
    """Return the PCI class triple as an integer."""
    return parse_int(entry.class_code, 16)


def controller_of(instance: str | None, pci: Mapping[str, PciEntry]) -> str | None:
    """Walk up the device tree until a PCI device is found.

    Args:
        instance: The instance identifier to start from.
        pci: Every captured PCI device, keyed by instance identifier.

    Returns:
        The controller's instance identifier, or ``None``.

    Example:
        >>> from lsdsk.adapters.hw.windows.capture import PciEntry
        >>> controller_of("PCI\\\\VEN_8086&DEV_A182\\\\3", {"PCI\\\\VEN_8086&DEV_A182\\\\3": PciEntry()})
        'PCI\\\\VEN_8086&DEV_A182\\\\3'
    """
    seen: set[str] = set()
    current = instance
    while current and current not in seen:
        seen.add(current)
        if current in pci:
            return current
        entry = pci.get(current)
        current = None if entry is None else entry.parent
    return None


def _controller_name(entry: PciEntry, instance: str) -> str:
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

    Returns:
        A name for the controller.
    """
    vendor = parse_int(entry.vendor, 16)
    device = parse_int(entry.device, 16)
    if vendor is not None and device is not None:
        return pciids.describe(vendor, device)
    return entry.name or instance


def build_controllers(capture: WindowsCapture) -> tuple[Controller, ...]:
    """Build every storage controller found in a Windows capture."""
    devices = capture.pci
    controllers: list[Controller] = []
    for instance, entry in sorted(devices.items()):
        kind = controller_kind_of(_class_code(entry))
        if kind is ControllerKind.UNKNOWN:
            continue
        parent = devices.get(entry.parent or "")
        controllers.append(
            Controller(
                address=entry.address or instance,
                name=_controller_name(entry, instance),
                kind=kind,
                driver=entry.driver,
                link=_pcie_link(entry),
                upstream=None if parent is None else _pcie_link(parent),
                # Windows publishes no link registers for a PCIe bridge, so this
                # string is the only thing said about the port at all.
                upstream_name=None if parent is None else parent.name or None,
            )
        )
    return tuple(controllers)


def build_slots(capture: WindowsCapture) -> tuple[PcieSlot, ...]:
    """Build the PCIe ports a card could move between.

    Windows exposes no equivalent of the Slot Implemented bit through the device
    properties, so ``connector_present`` stays unknown and the placement rules
    never propose moving a card.  That is the honest outcome: a recommendation
    that might send someone hunting for a slot that does not exist is worse than
    the platform-ceiling hint, which is still produced.
    """
    devices = capture.pci
    slots: list[PcieSlot] = []
    for instance, entry in sorted(devices.items()):
        class_code = _class_code(entry)
        if class_code is None or (class_code >> 8) != 0x0604:  # noqa: PLR2004 - the PCI-to-PCI bridge class
            continue
        children = entry.children
        occupant = devices.get(children[0]) if children else None
        slots.append(
            PcieSlot(
                address=entry.address or instance,
                link=_pcie_link(entry),
                occupied=bool(children),
                connector_present=None,
                occupant_address=None if occupant is None else occupant.address or children[0],
                occupant_class=None if occupant is None else _class_code(occupant),
                occupant_name=None if occupant is None else occupant.name,
                occupant_link=None if occupant is None else _pcie_link(occupant),
                physical_slot_number=entry.slot_number,
            )
        )
    return tuple(slots)


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


def build_disks(capture: WindowsCapture) -> tuple[Disk, ...]:
    """Build every disk found in a Windows capture."""
    pci = capture.pci
    disks: list[Disk] = []

    for path, entry in sorted(capture.disks.items()):
        device = entry.device
        bus = device.bus_type
        is_nvme = bus is BusType.NVME
        record = (entry.nvme if is_nvme else entry.ata) or HealthBlobs()

        identity = None
        if bus is BusType.SATA:
            blob = decode_base64(record.identify)
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

        controller_instance = controller_of(entry.parent, pci)
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

        model = (nvme_identity.model if nvme_identity else None) or (identity.model if identity else None)
        node = _node_name(entry, path)
        disks.append(
            Disk(
                node=node,
                path=node,
                model=model or device.model or "unknown",
                serial=(nvme_identity.serial if nvme_identity else None)
                or (identity.serial if identity else None)
                or device.serial,
                firmware=(nvme_identity.firmware if nvme_identity else None)
                or (identity.firmware if identity else None)
                or device.rev,
                size_bytes=entry.size_bytes,
                kind=kind,
                bus=bus,
                controller_address=controller,
                # The reader records no port capability for a disk, so the port
                # end of the link stays unmeasured.
                link=InterfaceLink(
                    negotiated_gbps=identity.negotiated_gbps if identity else None,
                    drive_max_gbps=identity.max_gbps if identity else None,
                ),
                pcie=_pcie_link(endpoint) if is_nvme and endpoint is not None else None,
                health=_health_from(record, entry, bus=bus, nvme_identity=nvme_identity),
            )
        )
    return tuple(disks)


def _node_name(entry: DiskEntry, path: str) -> str:
    """Render a disk as something a person can read.

    The interface path is a GUID-laden string no one wants in a listing, so the
    PhysicalDrive number the reader asked the operating system for is preferred.
    """
    if entry.node:
        return entry.node
    match = _DISK_INDEX.search(path)
    return f"PhysicalDrive{match.group(1)}" if match else path


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
    disks = build_disks(capture)
    controllers = build_controllers(capture)

    used: dict[str, int] = {}
    for disk in disks:
        if disk.controller_address is not None:
            used[disk.controller_address] = used.get(disk.controller_address, 0) + 1

    environment, detail = classify(capture.environment)

    return Inventory(
        hostname=device_text(capture.hostname) or "unknown",
        # replace, not a rebuild: only ports_used is known this late, and
        # restating every other field here would silently drop any field
        # Controller gains later.
        controllers=tuple(
            replace(controller, ports_used=used.get(controller.address, 0)) for controller in controllers
        ),
        disks=disks,
        slots=build_slots(capture),
        privileged=capture.elevated,
        environment=environment,
        environment_detail=detail,
        board=board_name(capture.environment),
        devices_accessible=capture.devices_accessible,
    )


__all__ = ["build_controllers", "build_disks", "build_inventory", "build_slots", "controller_of"]
