"""Turn a captured Windows reading into the domain inventory.

Pure, like its Linux counterpart, so the whole Windows mapping path is testable
on any operating system against captures taken from real machines.

Windows names devices by instance identifier rather than by PCI address, so the
tree is walked by parentage instead of by path.  The disks a controller carries
are found by walking up from each disk until a PCI device is reached.

System Role:
    Adapter layer, translation half.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

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
from ..decode.ata_identify import decode_identify
from ..decode.ata_smart import decode_health
from ..decode.nvme import decode_identify_controller, decode_smart_log
from ..decode.text import device_text
from ..decode.virtualization import board_name_from_capture, classify, evidence_from_capture

if TYPE_CHECKING:
    from collections.abc import Mapping

from ..linux.builder import controller_kind_of, parse_link_rate, parse_pcie_speed

# A Windows disk interface path ends with the device instance, from which the
# familiar PhysicalDrive-style name cannot be recovered, so the path is shown.
_DISK_INDEX = re.compile(r"PhysicalDrive(\d+)", re.IGNORECASE)

# Bus types that mean the disk speaks ATA and therefore has an IDENTIFY page.
_ATA_BUSES = {"ata", "sata", "atapi"}

_BUS_TYPES: dict[str, BusType] = {
    "sata": BusType.SATA,
    "ata": BusType.SATA,
    "atapi": BusType.SATA,
    "sas": BusType.SAS,
    "scsi": BusType.SAS,
    "nvme": BusType.NVME,
    "usb": BusType.USB,
    "virtual": BusType.VIRTUAL,
    "file-backed virtual": BusType.VIRTUAL,
    "spaces": BusType.VIRTUAL,
}


def _to_int(text: str | None, base: int = 10) -> int | None:
    """Parse an integer, returning ``None`` for anything unparsable."""
    if text is None:
        return None
    try:
        return int(text, base)
    except ValueError:
        return None


def _decode_base64(value: object) -> bytes | None:
    """Decode a base64 blob from a capture, tolerating a malformed entry."""
    import binascii  # noqa: PLC0415 - only needed on this error path
    from base64 import b64decode  # noqa: PLC0415 - only needed on this error path

    if not isinstance(value, str):
        return None
    try:
        return b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _pcie_link(entry: Mapping[str, Any]) -> PcieLink:
    """Build a PCIe link from one captured device's properties."""
    return PcieLink(
        current_speed_gtps=parse_pcie_speed(entry.get("current_link_speed")),
        current_width=_to_int(entry.get("current_link_width")),
        max_speed_gtps=parse_pcie_speed(entry.get("max_link_speed")),
        max_width=_to_int(entry.get("max_link_width")),
    )


def _class_code(entry: Mapping[str, Any]) -> int | None:
    """Return the PCI class triple as an integer."""
    return _to_int(entry.get("class"), 16)


def controller_of(instance: str | None, pci: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Walk up the device tree until a PCI device is found.

    Args:
        instance: The instance identifier to start from.
        pci: Every captured PCI device, keyed by instance identifier.

    Returns:
        The controller's instance identifier, or ``None``.

    Example:
        >>> controller_of("PCI\\\\VEN_8086&DEV_A182\\\\3", {"PCI\\\\VEN_8086&DEV_A182\\\\3": {}})
        'PCI\\\\VEN_8086&DEV_A182\\\\3'
    """
    seen: set[str] = set()
    current = instance
    while current and current not in seen:
        seen.add(current)
        if current in pci:
            return current
        entry = pci.get(current)
        current = None if entry is None else entry.get("parent")
    return None


def build_controllers(capture: Mapping[str, Any]) -> tuple[Controller, ...]:
    """Build every storage controller found in a Windows capture."""
    devices: Mapping[str, Mapping[str, Any]] = capture.get("pci", {})
    controllers: list[Controller] = []
    for instance, entry in sorted(devices.items()):
        kind = controller_kind_of(_class_code(entry))
        if kind is ControllerKind.UNKNOWN:
            continue
        parent = devices.get(str(entry.get("parent", "")))
        controllers.append(
            Controller(
                address=str(entry.get("address") or instance),
                name=str(entry.get("name") or instance),
                kind=kind,
                driver=entry.get("driver"),
                link=_pcie_link(entry),
                upstream=_pcie_link(parent) if parent else None,
            )
        )
    return tuple(controllers)


def build_slots(capture: Mapping[str, Any]) -> tuple[PcieSlot, ...]:
    """Build the PCIe ports a card could move between.

    Windows exposes no equivalent of the Slot Implemented bit through the device
    properties, so ``connector_present`` stays unknown and the placement rules
    never propose moving a card.  That is the honest outcome: a recommendation
    that might send someone hunting for a slot that does not exist is worse than
    the platform-ceiling hint, which is still produced.
    """
    devices: Mapping[str, Mapping[str, Any]] = capture.get("pci", {})
    slots: list[PcieSlot] = []
    for instance, entry in sorted(devices.items()):
        class_code = _class_code(entry)
        if class_code is None or (class_code >> 8) != 0x0604:  # noqa: PLR2004 - the PCI-to-PCI bridge class
            continue
        children: list[str] = list(entry.get("children") or [])
        occupant = devices.get(children[0]) if children else None
        slots.append(
            PcieSlot(
                address=str(entry.get("address") or instance),
                link=_pcie_link(entry),
                occupied=bool(children),
                connector_present=None,
                occupant_address=None if occupant is None else str(occupant.get("address") or children[0]),
                occupant_class=_class_code(occupant) if occupant else None,
                occupant_name=None if occupant is None else occupant.get("name"),
                occupant_link=_pcie_link(occupant) if occupant else None,
                physical_slot_number=entry.get("slot_number"),
            )
        )
    return tuple(slots)


def _health_from(record: Mapping[str, Any], entry: Mapping[str, Any], *, nvme: bool) -> Health | None:
    """Decode whatever health data the capture holds for one disk."""
    if nvme:
        identity = None
        identify_blob = _decode_base64(record.get("identify_controller"))
        if identify_blob is not None:
            try:
                identity = decode_identify_controller(identify_blob)
            except ValueError:
                identity = None
        log_blob = _decode_base64(record.get("smart_log"))
        if log_blob is not None:
            try:
                return decode_smart_log(log_blob, identity)
            except ValueError:
                return None
    else:
        data = _decode_base64(record.get("smart_data"))
        if data is not None:
            try:
                return decode_health(data, _decode_base64(record.get("smart_thresholds")))
            except ValueError:
                return None

    # No passthrough, but the storage stack may still have offered a temperature.
    temperature: Mapping[str, Any] = entry.get("temperature", {})
    if temperature:
        return Health(
            temperature_c=_as_int(temperature.get("temperature_c")),
            temperature_warning_c=_as_int(temperature.get("warning_c")),
            temperature_critical_c=_as_int(temperature.get("critical_c")),
        )
    return None


def _as_int(value: object) -> int | None:
    """Coerce a captured number to an int, or ``None``."""
    return value if isinstance(value, int) else None


def build_disks(capture: Mapping[str, Any]) -> tuple[Disk, ...]:
    """Build every disk found in a Windows capture."""
    pci: Mapping[str, Mapping[str, Any]] = capture.get("pci", {})
    entries: Mapping[str, Mapping[str, Any]] = capture.get("disks", {})
    disks: list[Disk] = []

    for path, entry in sorted(entries.items()):
        device: Mapping[str, Any] = entry.get("device", {})
        bus_name = str(device.get("bus_type", "unknown"))
        bus = _BUS_TYPES.get(bus_name, BusType.UNKNOWN)
        is_nvme = bus is BusType.NVME
        record: Mapping[str, Any] = entry.get("nvme" if is_nvme else "ata", {})

        identity = None
        if not is_nvme and bus_name in _ATA_BUSES:
            blob = _decode_base64(record.get("identify"))
            if blob is not None:
                try:
                    identity = decode_identify(blob)
                except ValueError:
                    identity = None

        nvme_identity = None
        if is_nvme:
            blob = _decode_base64(record.get("identify_controller"))
            if blob is not None:
                try:
                    nvme_identity = decode_identify_controller(blob)
                except ValueError:
                    nvme_identity = None

        controller_instance = controller_of(entry.get("parent"), pci)
        endpoint = pci.get(controller_instance) if controller_instance else None
        controller = str(endpoint.get("address") or controller_instance) if endpoint else controller_instance
        rotating = entry.get("rotating")
        kind = DiskKind.UNKNOWN
        if identity is not None:
            kind = identity.kind
        elif is_nvme:
            kind = DiskKind.SSD
        elif rotating is not None:
            kind = DiskKind.HDD if rotating else DiskKind.SSD

        model = (nvme_identity.model if nvme_identity else None) or (identity.model if identity else None)
        disks.append(
            Disk(
                node=_node_name(entry, path),
                path=_node_name(entry, path),
                model=model or str(device.get("model") or "unknown"),
                serial=(nvme_identity.serial if nvme_identity else None)
                or (identity.serial if identity else None)
                or device.get("serial"),
                firmware=(nvme_identity.firmware if nvme_identity else None)
                or (identity.firmware if identity else None)
                or device.get("rev"),
                size_bytes=_as_int(entry.get("size_bytes")),
                kind=kind,
                bus=_BUS_TYPES.get(bus_name, BusType.UNKNOWN),
                controller_address=controller,
                link=InterfaceLink(
                    negotiated_gbps=identity.negotiated_gbps if identity else None,
                    drive_max_gbps=identity.max_gbps if identity else None,
                    port_max_gbps=parse_link_rate(entry.get("port_max")),
                ),
                pcie=_pcie_link(endpoint) if is_nvme and endpoint else None,
                health=_health_from(record, entry, nvme=is_nvme),
            )
        )
    return tuple(disks)


def _node_name(entry: Mapping[str, Any], path: str) -> str:
    """Render a disk as something a person can read.

    The interface path is a GUID-laden string no one wants in a listing, so the
    PhysicalDrive number the reader asked the operating system for is preferred.
    """
    node = entry.get("node")
    if isinstance(node, str) and node:
        return node
    match = _DISK_INDEX.search(path)
    return f"PhysicalDrive{match.group(1)}" if match else path


def build_inventory(capture: Mapping[str, Any]) -> Inventory:
    """Turn a whole Windows capture into an inventory.

    Args:
        capture: A Windows reading, live or replayed.

    Returns:
        The machine as the domain sees it.

    Example:
        >>> build_inventory({"platform": "win32", "hostname": "vm"}).hostname
        'vm'
    """
    disks = build_disks(capture)
    controllers = build_controllers(capture)

    used: dict[str, int] = {}
    for disk in disks:
        if disk.controller_address is not None:
            used[disk.controller_address] = used.get(disk.controller_address, 0) + 1

    raw_environment: Mapping[str, object] = capture.get("environment") or {}
    environment, detail = classify(evidence_from_capture(raw_environment))

    return Inventory(
        hostname=device_text(str(capture.get("hostname", "unknown"))) or "unknown",
        # replace, not a rebuild: only ports_used is known this late, and
        # restating every other field here would silently drop any field
        # Controller gains later.
        controllers=tuple(
            replace(controller, ports_used=used.get(controller.address, 0)) for controller in controllers
        ),
        disks=disks,
        slots=build_slots(capture),
        privileged=bool(capture.get("elevated", False)),
        environment=environment,
        environment_detail=detail,
        board=board_name_from_capture(raw_environment),
        devices_accessible=bool(capture.get("devices_accessible", True)),
    )


__all__ = ["build_controllers", "build_disks", "build_inventory", "build_slots", "controller_of"]
