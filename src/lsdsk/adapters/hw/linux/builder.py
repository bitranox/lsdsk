"""Turn a captured Linux sysfs reading into the domain inventory.

Deliberately pure: it takes the plain mapping that :mod:`.reader` produces and
returns domain objects, touching no files.  That is what lets the whole Linux
mapping path be tested on any operating system against captures taken from real
machines, and it is what makes ``--replay`` render exactly what a live run would.

System Role:
    Adapter layer, translation half.  The impure half lives in :mod:`.reader`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..decode.text import device_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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
from ..decode.ahci import capabilities_from_capture
from ..decode.ata_identify import AtaIdentity, decode_vpd_ata_information
from ..decode.ata_smart import decode_health
from ..decode.nvme import decode_identify_controller, decode_smart_log
from ..decode.pciids import Database
from ..decode.virtualization import board_name_from_capture, classify, evidence_from_capture

# A PCI address as it appears inside a sysfs device path.
_PCI_ADDRESS = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]")
_SAS_PORT = re.compile(r"/port-(\d+:\d+)/")
_ATA_PORT = re.compile(r"/ata(\d+)/")

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


def _to_int(text: str | None, base: int = 10) -> int | None:
    """Parse an integer, returning ``None`` for anything unparsable."""
    if text is None:
        return None
    try:
        return int(text, base)
    except ValueError:
        return None


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


def _pcie_link(entry: Mapping[str, str]) -> PcieLink:
    """Build a PCIe link from one sysfs PCI device's attributes."""
    return PcieLink(
        current_speed_gtps=parse_pcie_speed(entry.get("current_link_speed")),
        current_width=_to_int(entry.get("current_link_width")),
        max_speed_gtps=parse_pcie_speed(entry.get("max_link_speed")),
        max_width=_to_int(entry.get("max_link_width")),
    )


def _class_code(entry: Mapping[str, str]) -> int | None:
    """Return the PCI class triple as an integer."""
    return _to_int(entry.get("class"), 16)


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


def _host_details(capture: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Index SCSI host attributes by the PCI address of their controller."""
    details: dict[str, dict[str, str]] = {}
    hosts: Mapping[str, Mapping[str, str]] = capture.get("classes", {}).get("scsi_host", {})
    for entry in hosts.values():
        address = controller_address_of(entry.get("path", ""))
        if address is None:
            continue
        # A card with several hosts reports the same board on each, so the first
        # host that names a board wins and the rest add nothing.
        if entry.get("board_name") or address not in details:
            details[address] = dict(entry)
    return details


def _port_counts(capture: Mapping[str, Any]) -> dict[str, int]:
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
    classes: Mapping[str, Mapping[str, Mapping[str, str]]] = capture.get("classes", {})
    for class_name in ("sas_phy", "ata_port"):
        for entry in classes.get(class_name, {}).values():
            address = controller_address_of(entry.get("path", ""))
            if address is not None:
                counts[address] = counts.get(address, 0) + 1

    for address, entry in capture.get("pci", {}).items():
        if controller_kind_of(_class_code(entry)) is not ControllerKind.AHCI:
            continue
        capabilities = capabilities_from_capture(entry.get("ahci"))
        if capabilities is not None and capabilities.ports_implemented:
            counts[address] = capabilities.ports_implemented
        else:
            counts.pop(address, None)
    return counts


def _ahci_port_speed(capture: Mapping[str, Any], address: str | None) -> float | None:
    """Return what one AHCI controller's ports can carry, in Gb/s."""
    if address is None:
        return None
    capabilities = capabilities_from_capture(capture.get("pci", {}).get(address, {}).get("ahci"))
    return None if capabilities is None else capabilities.interface_speed_gbps


def build_controllers(capture: Mapping[str, Any]) -> tuple[Controller, ...]:
    """Build every storage controller found in a capture.

    Args:
        capture: A Linux sysfs capture.

    Returns:
        Controllers in PCI address order.
    """
    devices: Mapping[str, Mapping[str, str]] = capture.get("pci", {})
    hosts = _host_details(capture)
    ports = _port_counts(capture)
    database = _pci_database(capture)

    controllers: list[Controller] = []
    for address, entry in sorted(devices.items()):
        kind = controller_kind_of(_class_code(entry))
        if kind is ControllerKind.UNKNOWN:
            continue
        host = hosts.get(address, {})
        parent = _parent_address(entry.get("path", ""))
        upstream = devices.get(parent) if parent else None
        controllers.append(
            Controller(
                address=address,
                name=host.get("board_name") or _pci_name(entry, database),
                kind=kind,
                driver=entry.get("driver"),
                firmware=host.get("version_fw"),
                link=_pcie_link(entry),
                upstream=_pcie_link(upstream) if upstream else None,
                upstream_name=_pci_name(upstream, database) if upstream else None,
                port_count=ports.get(address),
                ports_used=None,
            )
        )
    return tuple(controllers)


def _pci_database(capture: Mapping[str, Any]) -> pciids.Database | None:
    """Return the identifier database carried in a capture, when it has one.

    A capture taken on one machine may be replayed on another that has a
    different ``pci.ids``, or none, so the reader records the names it resolved.
    """
    names: Mapping[str, str] | None = capture.get("pci_names")
    if not names:
        return None
    devices: dict[tuple[int, int], str] = {}
    for key, value in names.items():
        vendor_text, _, device_text = key.partition(":")
        vendor, device = _to_int(vendor_text, 16), _to_int(device_text, 16)
        if vendor is not None and device is not None:
            devices[(vendor, device)] = value
    return Database({}, devices)


def _pci_name(entry: Mapping[str, str], database: pciids.Database | None) -> str:
    """Return a readable name for one PCI device."""
    vendor = _to_int(entry.get("vendor"), 16)
    device = _to_int(entry.get("device"), 16)
    if vendor is None or device is None:
        return "Unknown controller"
    return pciids.describe(vendor, device, database)


def build_slots(capture: Mapping[str, Any]) -> tuple[PcieSlot, ...]:
    """Build the list of PCIe ports, what occupies them and whether they are slots.

    Args:
        capture: A Linux sysfs capture.

    Returns:
        Ports in PCI address order.
    """
    devices: Mapping[str, Mapping[str, Any]] = capture.get("pci", {})
    database = _pci_database(capture)
    slots: list[PcieSlot] = []
    for address, entry in sorted(devices.items()):
        class_code = _class_code(entry)
        if class_code is None or (class_code >> 8) != _BRIDGE_CLASS:
            continue
        children: Sequence[str] = entry.get("children", ())
        occupant = devices.get(children[0]) if children else None
        slots.append(
            PcieSlot(
                address=address,
                link=_pcie_link(entry),
                occupied=bool(children),
                connector_present=entry.get("slot_implemented"),
                occupant_address=children[0] if children else None,
                occupant_class=_class_code(occupant) if occupant else None,
                occupant_name=_pci_name(occupant, database) if occupant else None,
                occupant_link=_pcie_link(occupant) if occupant else None,
                physical_slot_number=entry.get("slot_number"),
            )
        )
    return tuple(slots)


def _phy_for(device_path: str, capture: Mapping[str, Any]) -> Mapping[str, str] | None:
    """Find the SAS phy a disk is attached through."""
    match = _SAS_PORT.search(device_path + "/")
    if match is None:
        return None
    phys: Mapping[str, Mapping[str, str]] = capture.get("classes", {}).get("sas_phy", {})
    return phys.get(f"phy-{match.group(1)}")


def _ata_link_for(device_path: str, capture: Mapping[str, Any]) -> Mapping[str, str] | None:
    """Find the ATA link a disk is attached through."""
    match = _ATA_PORT.search(device_path + "/")
    if match is None:
        return None
    links: Mapping[str, Mapping[str, str]] = capture.get("classes", {}).get("ata_link", {})
    return next(
        (entry for entry in links.values() if f"/ata{match.group(1)}/" in entry.get("path", "")),
        None,
    )


def _ata_identity(block: Mapping[str, Any], ata: Mapping[str, Any]) -> AtaIdentity | None:
    """Decode a drive's identity, preferring the privileged reading.

    Both sources carry the same structure. The passthrough reading is preferred
    only because it is present whenever it succeeded; the sysfs VPD page is the
    unprivileged fallback and yields the same answer.
    """
    for blob_key, source in (("identify", ata), ("vpd_pg89", block.get("vpd", {}))):
        raw = source.get(blob_key)
        if not raw:
            continue
        payload = _decode_base64(raw)
        if payload is None:
            continue
        try:
            return decode_vpd_ata_information(payload) if blob_key == "vpd_pg89" else _decode_identify(payload)
        except ValueError:
            continue
    return None


def _decode_identify(payload: bytes) -> AtaIdentity:
    """Decode an IDENTIFY payload, kept separate so the import stays local."""
    from ..decode.ata_identify import decode_identify  # noqa: PLC0415 - avoids a name clash with the VPD variant

    return decode_identify(payload)


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


def _sata_link(
    identity: AtaIdentity | None,
    phy: Mapping[str, str] | None,
    ata_link: Mapping[str, str] | None,
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
        negotiated = parse_link_rate(phy.get("negotiated_linkrate"))
    if negotiated is None and ata_link is not None:
        negotiated = parse_link_rate(ata_link.get("sata_spd"))

    port_max = parse_link_rate(phy.get("maximum_linkrate_hw")) if phy else None
    if port_max is None and ata_link is not None:
        port_max = parse_link_rate(ata_link.get("sata_spd_max"))
    # The AHCI controller's own capability register, which is the only place a
    # SATA port's speed is published once libata has no limit to report.
    if port_max is None:
        port_max = controller_speed

    return InterfaceLink(
        negotiated_gbps=negotiated,
        drive_max_gbps=identity.max_gbps if identity else None,
        port_max_gbps=port_max,
    )


def _hwmon_temperature(paths: Sequence[str], capture: Mapping[str, Any]) -> int | None:
    """Read a device's temperature from the hwmon nodes it owns."""
    monitors: Mapping[str, Mapping[str, str]] = capture.get("classes", {}).get("hwmon", {})
    wanted = set(paths)
    for entry in monitors.values():
        if entry.get("path") not in wanted:
            continue
        raw = _to_int(entry.get("temp1_input"))
        if raw is not None:
            return round(raw / _MILLIDEGREE)
    return None


def _bus_of(node: str, identity: AtaIdentity | None, phy: Mapping[str, str] | None) -> BusType:
    """Decide which transport a disk speaks, among the ones that answer ATA.

    NVMe never reaches here: `build_disks` routes a node by its name, and an
    nvme one goes to `_build_nvme_disk`, which sets `BusType.NVME` itself.
    """
    if identity is not None or phy is not None:
        # A drive that answers ATA IDENTIFY is SATA even when it is tunnelled
        # through a SAS expander; a drive that does not is native SAS.
        return BusType.SATA if identity is not None else BusType.SAS
    return BusType.UNKNOWN


def _size_bytes(block: Mapping[str, Any], identity: AtaIdentity | None) -> int | None:
    """Return a disk's capacity, preferring the kernel's own figure."""
    sectors = _to_int(block.get("size"))
    if sectors is not None:
        return sectors * _SYSFS_SECTOR_BYTES
    return identity.size_bytes if identity else None


def _build_nvme_disk(node: str, block: Mapping[str, Any], capture: Mapping[str, Any]) -> Disk:
    """Build one NVMe disk from a capture."""
    record: Mapping[str, Any] = capture.get("nvme", {}).get(node, {})
    identity = None
    controller_blob = _decode_base64(record.get("identify_controller"))
    if controller_blob is not None:
        try:
            identity = decode_identify_controller(controller_blob)
        except ValueError:
            identity = None

    health: Health | None = None
    log_blob = _decode_base64(record.get("smart_log"))
    if log_blob is not None:
        try:
            health = decode_smart_log(log_blob, identity)
        except ValueError:
            health = None

    temperature = _hwmon_temperature(block.get("hwmon", ()), capture)
    if health is None and temperature is not None:
        health = Health(temperature_c=temperature)

    address = controller_address_of(block.get("device_path", ""))
    endpoint: Mapping[str, str] = capture.get("pci", {}).get(address, {}) if address else {}
    # The controller publishes its model, serial and firmware in sysfs, readable
    # without any privilege. Falling back to it means an unprivileged run still
    # names the drive instead of showing three dashes.
    published = _nvme_class_entry(capture, block.get("device_path", ""))
    return Disk(
        node=node,
        path=f"/dev/{node}",
        model=(identity.model if identity else None) or device_text(published.get("model") or "") or node,
        serial=(identity.serial if identity else None) or device_text(published.get("serial") or "") or None,
        firmware=(identity.firmware if identity else None) or device_text(published.get("firmware_rev") or "") or None,
        wwn=_stable_identifier(block, block.get("device", {})),
        size_bytes=_size_bytes(block, None),
        kind=DiskKind.SSD,
        bus=BusType.NVME,
        controller_address=address,
        pcie=_pcie_link(endpoint) if endpoint else None,
        health=health,
    )


def _nvme_class_entry(capture: Mapping[str, Any], device_path: str) -> Mapping[str, str]:
    """Find the nvme class entry matching one namespace's device path."""
    entries: Mapping[str, Mapping[str, str]] = capture.get("classes", {}).get("nvme", {})
    for entry in entries.values():
        path = entry.get("path", "")
        if path and device_path and (device_path.startswith(path) or path.startswith(device_path)):
            return entry
    return {}


def _build_ata_disk(node: str, block: Mapping[str, Any], capture: Mapping[str, Any]) -> Disk:
    """Build one SATA or SAS disk from a capture."""
    ata: Mapping[str, Any] = capture.get("ata", {}).get(node, {})
    device_path = block.get("device_path", "")
    identity = _ata_identity(block, ata)
    phy = _phy_for(device_path, capture)
    ata_link = _ata_link_for(device_path, capture)

    health: Health | None = None
    data = _decode_base64(ata.get("smart_data"))
    if data is not None:
        try:
            health = decode_health(data, _decode_base64(ata.get("smart_thresholds")))
        except ValueError:
            health = None

    device: Mapping[str, str] = block.get("device", {})
    rotational = block.get("queue", {}).get("rotational")
    kind = DiskKind.UNKNOWN
    if identity is not None:
        kind = identity.kind
    elif rotational is not None:
        kind = DiskKind.HDD if rotational == "1" else DiskKind.SSD

    return Disk(
        node=node,
        path=f"/dev/{node}",
        model=(identity.model if identity else None) or device_text(device.get("model") or "") or node,
        serial=identity.serial if identity else None,
        firmware=(identity.firmware if identity else None) or device_text(device.get("rev") or "") or None,
        wwn=_stable_identifier(block, device),
        size_bytes=_size_bytes(block, identity),
        kind=kind,
        bus=_bus_of(node, identity, phy),
        controller_address=controller_address_of(device_path),
        link=_sata_link(identity, phy, ata_link, _ahci_port_speed(capture, controller_address_of(device_path))),
        health=health,
    )


def _stable_identifier(block: Mapping[str, Any], device: Mapping[str, Any]) -> str | None:
    """Return the identifier that follows a drive between bays.

    A namespace UUID is preferred when the drive publishes one, otherwise the
    world-wide name. Both carry their own prefix, so the value says what it is.
    """
    uuid = block.get("uuid")
    if isinstance(uuid, str) and uuid:
        return f"uuid.{uuid}"
    for source in (block, device):
        wwid = source.get("wwid")
        if isinstance(wwid, str) and wwid:
            return wwid.strip()
    return None


def build_disks(capture: Mapping[str, Any]) -> tuple[Disk, ...]:
    """Build every disk found in a capture.

    Args:
        capture: A Linux sysfs capture.

    Returns:
        Disks in node order.
    """
    blocks: Mapping[str, Mapping[str, Any]] = capture.get("block", {})
    disks: list[Disk] = []
    for node, block in sorted(blocks.items()):
        builder = _build_nvme_disk if node.startswith("nvme") else _build_ata_disk
        disks.append(builder(node, block, capture))
    return tuple(disks)


def build_inventory(capture: Mapping[str, Any]) -> Inventory:
    """Turn a whole Linux capture into an inventory.

    Args:
        capture: A Linux sysfs capture, live or replayed.

    Returns:
        The machine as the domain sees it.

    Example:
        >>> build_inventory({"hostname": "example"}).hostname
        'example'
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
        privileged=capture.get("euid") == 0,
        environment=environment,
        environment_detail=detail,
        board=board_name_from_capture(raw_environment),
        devices_accessible=bool(capture.get("devices_accessible", True)),
    )


__all__ = [
    "build_controllers",
    "build_disks",
    "build_inventory",
    "build_slots",
    "controller_address_of",
    "controller_kind_of",
    "parse_link_rate",
    "parse_pcie_speed",
]
