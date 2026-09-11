"""The typed shape of a Windows reading.

:mod:`.reader` writes a plain JSON-serialisable mapping, because that is what a
snapshot stores. This is where the mapping becomes typed, once, for a live run
and a replay alike, so :mod:`.builder` reads attributes rather than keys and a
replay whose sections have the wrong shape is refused as a bad file.

Windows names devices by instance identifier, so the PCI devices and the disks
stay maps keyed by that identifier and by the disk's interface path; what they
hold has a fixed set of keys, and those entries are models. Only the keys the
builder reads are modelled, and the rest of a capture is ignored rather than
refused.

System Role:
    Adapter layer, the parse step between :mod:`.reader` and :mod:`.builder`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from ....domain.enums import BusType, Platform
from ..capture import CaptureHeader, CaptureModel
from ..decode.virtualization import VirtualizationEvidence

# The transport names the reader takes from STORAGE_DEVICE_DESCRIPTOR.BusType,
# mapped onto the buses lsdsk grades. Windows has more transports than lsdsk
# has rules for; the rest read as unknown rather than being refused, because
# they are real readings of a machine this tool simply has nothing to say about.
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


def _bus_type_of(value: object) -> object:
    """Map a transport name onto a bus, leaving anything else to be refused.

    Example:
        >>> _bus_type_of("atapi")
        <BusType.SATA: 'sata'>
        >>> _bus_type_of("fibre")
        <BusType.UNKNOWN: 'unknown'>
    """
    return _BUS_TYPES.get(value, BusType.UNKNOWN) if isinstance(value, str) else value


class PciEntry(CaptureModel):
    """One PCI device, as SetupAPI describes it.

    Attributes:
        class_code: The class triple, such as ``0x010802``. Aliased because
            ``class`` is a keyword.
        vendor: The vendor identifier.
        device: The device identifier.
        name: Windows' own description, which is localised.
        driver: The service bound to the device.
        current_link_speed: The negotiated PCIe speed, written as sysfs writes it.
        current_link_width: The negotiated width.
        max_link_speed: The best speed the device supports.
        max_link_width: The widest link the device supports.
        slot_number: The UI number the firmware assigned the slot.
        address: The bus, device and function address.
        parent: The instance identifier of the device above it.
        children: The instance identifiers of the devices below it.
    """

    class_code: str | None = Field(default=None, alias="class")
    vendor: str | None = None
    device: str | None = None
    name: str | None = None
    driver: str | None = None
    current_link_speed: str | None = None
    current_link_width: str | None = None
    max_link_speed: str | None = None
    max_link_width: str | None = None
    slot_number: int | None = None
    address: str | None = None
    parent: str | None = None
    children: tuple[str, ...] = ()


class StorageDescriptor(CaptureModel):
    """The identity strings a disk's storage descriptor carries.

    Attributes:
        bus_type: The transport, parsed here from the name the reader recorded.
        model: The model string.
        serial: The serial number.
        rev: The firmware revision.
    """

    bus_type: Annotated[BusType, BeforeValidator(_bus_type_of)] = BusType.UNKNOWN
    model: str | None = None
    serial: str | None = None
    rev: str | None = None


class DiskTemperature(CaptureModel):
    """The temperature the storage stack offered, with the drive's own thresholds.

    Attributes:
        temperature_c: The current temperature.
        warning_c: The drive's warning threshold.
        critical_c: The drive's critical threshold.
    """

    temperature_c: int | None = None
    warning_c: int | None = None
    critical_c: int | None = None


class HealthBlobs(CaptureModel):
    """What passthrough returned for one disk, base64 encoded.

    Attributes:
        identify: The ATA IDENTIFY DEVICE data.
        identify_controller: The NVMe Identify Controller structure.
        smart_log: The NVMe SMART / Health Information log page.
        smart_data: The ATA SMART READ DATA structure.
        smart_thresholds: The ATA SMART READ THRESHOLDS structure.
    """

    identify: str | None = None
    identify_controller: str | None = None
    smart_log: str | None = None
    smart_data: str | None = None
    smart_thresholds: str | None = None


class DiskEntry(CaptureModel):
    """One disk.

    Attributes:
        parent: The instance identifier of the device the disk hangs off.
        node: The PhysicalDrive name the reader asked Windows for.
        device: The storage descriptor's identity strings.
        size_bytes: The disk's length.
        rotating: Whether the drive has a seek penalty, meaning rotating media.
        temperature: The temperature, when the storage stack offered one.
        nvme: NVMe passthrough results.
        ata: ATA passthrough results.
    """

    parent: str | None = None
    node: str | None = None
    device: StorageDescriptor = StorageDescriptor()
    size_bytes: int | None = None
    rotating: bool | None = None
    temperature: DiskTemperature | None = None
    nvme: HealthBlobs | None = None
    ata: HealthBlobs | None = None


class WindowsCapture(CaptureHeader):
    """A whole Windows reading.

    Attributes:
        platform: Always Windows; it is what selects this model.
        elevated: Whether the reader ran as Administrator.
        devices_accessible: Whether any disk could be opened at all.
        environment: The evidence for bare metal, a guest or a container.
        pci: Every PCI device, keyed by instance identifier.
        disks: Every disk, keyed by interface path.
    """

    platform: Literal[Platform.WINDOWS]
    elevated: bool = False
    devices_accessible: bool = True
    environment: VirtualizationEvidence = VirtualizationEvidence()
    pci: dict[str, PciEntry]
    disks: dict[str, DiskEntry] = Field(default_factory=dict[str, DiskEntry])


__all__ = [
    "DiskEntry",
    "DiskTemperature",
    "HealthBlobs",
    "PciEntry",
    "StorageDescriptor",
    "WindowsCapture",
]
