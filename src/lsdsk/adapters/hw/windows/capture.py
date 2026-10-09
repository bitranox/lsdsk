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
from ..capture import CaptureHeader, CaptureModel, DeviceText, EncodedPayload, Entries, EntryMap
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


def bus_type_of(value: object) -> object:
    """Map a transport name onto a bus, leaving anything else to be refused.

    Public rather than a parse-step private: :mod:`.reader` needs the same
    mapping to decide whether to open NVMe or ATA passthrough for a disk, and
    reaching for this shared conversion instead of comparing the raw string
    against an enum member itself keeps that decision in the one place a
    future transport spelling would need to be taught.

    Args:
        value: The transport name Windows published, or anything else.

    Returns:
        The bus it names, or the value unchanged for the model to refuse.

    Example:
        >>> bus_type_of("atapi")
        <BusType.SATA: 'sata'>
        >>> bus_type_of("fibre")
        <BusType.UNKNOWN: 'unknown'>
    """
    return _BUS_TYPES.get(value, BusType.UNKNOWN) if isinstance(value, str) else value


# The widths of the Win32 structures and properties these fields are read
# from, for the reason the Linux ones carry theirs: a value wider than its
# own source was never read from a disk. A length past a signed 64-bit one
# did not print oddly, it pushed four columns off the disks table, so the
# drive's serial and firmware disappeared rather than the number looking wrong.
_UINT32_MAX = 0xFFFFFFFF  # DEVPKEY UINumber, read as a UINT32
_PCIE_SLOT_NUMBER_MAX = 0x1FFF  # the 13-bit Physical Slot Number the domain's slot number means
_LARGE_INTEGER_MAX = 2**63 - 1  # IOCTL_DISK_GET_LENGTH_INFO answers in one
_SHORT_MIN = -(2**15)  # the temperature fields are each a ctypes.c_short
_SHORT_MAX = 2**15 - 1
_MAX_HUB_PORT = 255  # USB_NODE_CONNECTION_INFORMATION_EX.ConnectionIndex addresses a hub's ports in one byte


def slot_number_of(value: object) -> object:
    """Keep a UI number only when it fits the PCIe Physical Slot Number field.

    The domain's slot number is the 13-bit field Linux reads from Slot
    Capabilities. Windows publishes a firmware UI number of up to 32 bits, and
    a wider one is not that number, so it reads as unknown rather than as a
    slot that does not exist.

    Args:
        value: The recorded UI number, or anything else the capture held.

    Returns:
        The value when it is an integer in range, else ``None`` for an
        out-of-range integer, and the value untouched for anything the model
        should reject by its own type.

    Example:
        >>> slot_number_of(3), slot_number_of(0x2000), slot_number_of(None)
        (3, None, None)
    """
    if isinstance(value, int) and not isinstance(value, bool) and not 0 <= value <= _PCIE_SLOT_NUMBER_MAX:
        return None
    return value


class PciEntry(CaptureModel, frozen=True):
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

    class_code: DeviceText | None = Field(default=None, alias="class")
    vendor: DeviceText | None = None
    device: DeviceText | None = None
    name: DeviceText | None = None
    driver: DeviceText | None = None
    current_link_speed: DeviceText | None = None
    current_link_width: DeviceText | None = None
    max_link_speed: DeviceText | None = None
    max_link_width: DeviceText | None = None
    slot_number: Annotated[int | None, BeforeValidator(slot_number_of)] = Field(default=None, ge=0, le=_UINT32_MAX)
    address: DeviceText | None = None
    parent: DeviceText | None = None
    children: Entries[DeviceText] = ()


class StorageDescriptor(CaptureModel, frozen=True):
    """The identity strings a disk's storage descriptor carries.

    Attributes:
        bus_type: The transport, parsed here from the name the reader recorded.
        model: The model string.
        serial: The serial number.
        rev: The firmware revision.
    """

    bus_type: Annotated[BusType, BeforeValidator(bus_type_of)] = BusType.UNKNOWN
    model: DeviceText | None = None
    serial: DeviceText | None = None
    rev: DeviceText | None = None


class DiskTemperature(CaptureModel, frozen=True):
    """The temperature the storage stack offered, with the drive's own thresholds.

    Attributes:
        temperature_c: The current temperature.
        warning_c: The drive's warning threshold.
        critical_c: The drive's critical threshold.
    """

    temperature_c: int | None = Field(default=None, ge=_SHORT_MIN, le=_SHORT_MAX)
    warning_c: int | None = Field(default=None, ge=_SHORT_MIN, le=_SHORT_MAX)
    critical_c: int | None = Field(default=None, ge=_SHORT_MIN, le=_SHORT_MAX)


class HealthBlobs(CaptureModel, frozen=True):
    """What passthrough returned for one disk, base64 encoded.

    The ``*_error`` fields are the other half of every payload field: the reader
    writes one or the other, never both. A device opened without Administrator
    cannot be asked for IDENTIFY at all, and that sentence is what tells an
    absent reading from a healthy zero.

    Attributes:
        identify: The ATA IDENTIFY DEVICE data.
        identify_controller: The NVMe Identify Controller structure.
        smart_log: The NVMe SMART / Health Information log page.
        smart_data: The ATA SMART READ DATA structure.
        smart_thresholds: The ATA SMART READ THRESHOLDS structure.
        identify_error: Why IDENTIFY was refused.
        identify_controller_error: Why Identify Controller was refused.
        smart_log_error: Why the health log was refused.
        smart_data_error: Why SMART READ DATA was refused.
        smart_thresholds_error: Why SMART READ THRESHOLDS was refused.
    """

    identify: EncodedPayload | None = None
    identify_controller: EncodedPayload | None = None
    smart_log: EncodedPayload | None = None
    smart_data: EncodedPayload | None = None
    smart_thresholds: EncodedPayload | None = None
    identify_error: DeviceText | None = None
    identify_controller_error: DeviceText | None = None
    smart_log_error: DeviceText | None = None
    smart_data_error: DeviceText | None = None
    smart_thresholds_error: DeviceText | None = None


class DiskEntry(CaptureModel, frozen=True):
    """One disk.

    Attributes:
        parent: The instance identifier of the device the disk hangs off.
        ancestors: The instance identifiers above the disk, nearest first, as far
            up the device tree as it goes. A capture taken before the reader
            recorded them holds only ``parent``.
        node: The PhysicalDrive name the reader asked Windows for.
        device: The storage descriptor's identity strings.
        size_bytes: The disk's length.
        rotating: Whether the drive has a seek penalty, meaning rotating media.
        temperature: The temperature, when the storage stack offered one.
        nvme: NVMe passthrough results.
        ata: ATA passthrough results.
        error: Why the device could not be opened at all, in which case nothing
            else here was read.
        usb_link_error: Why the hub port a USB disk is plugged into could not be
            asked about its link: the hub would not open, the port did not
            answer, or the disk's place on the hub is not known.
        interface_unreadable: True for the stand-in the reader writes when a disk
            interface was listed but its path could not be read. It is a
            skipped reading, not a drive, so the builder keeps it out of the
            disks and reports its ``error`` instead.
    """

    parent: DeviceText | None = None
    ancestors: Entries[DeviceText] = ()
    node: DeviceText | None = None
    device: StorageDescriptor = StorageDescriptor()
    size_bytes: int | None = Field(default=None, ge=0, le=_LARGE_INTEGER_MAX)
    rotating: bool | None = None
    temperature: DiskTemperature | None = None
    nvme: HealthBlobs | None = None
    ata: HealthBlobs | None = None
    error: DeviceText | None = None
    usb_link_error: DeviceText | None = None
    interface_unreadable: bool = False


class UsbPortEntry(CaptureModel, frozen=True):
    """What one hub said about one of its ports and the device plugged into it.

    Every payload is the hub's own answer, as many bytes as it returned, for
    :mod:`..decode.usb` to read. A payload left empty without an ``error`` is
    an answer too: a port not running SuperSpeedPlus refuses that request, and
    a device with no BOS STALLs the request for it.

    Attributes:
        hub: The instance identifier of the hub the port belongs to.
        port: The port's number on that hub.
        service: The driver bound to the device in the port, such as
            ``UASPStor``, ``USBSTOR`` or ``USBHUB3``.
        connection: ``USB_NODE_CONNECTION_INFORMATION_EX``.
        connection_v2: ``USB_NODE_CONNECTION_INFORMATION_EX_V2``.
        connector: ``USB_PORT_CONNECTOR_PROPERTIES``.
        superspeedplus: ``USB_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION``.
        bos: The device's BOS descriptor, without the hub's request header.
        error: Why the port could not be asked at all, in which case none of
            the payloads was read.
    """

    hub: DeviceText
    port: int = Field(ge=1, le=_MAX_HUB_PORT)
    service: DeviceText | None = None
    connection: EncodedPayload | None = None
    connection_v2: EncodedPayload | None = None
    connector: EncodedPayload | None = None
    superspeedplus: EncodedPayload | None = None
    bos: EncodedPayload | None = None
    error: DeviceText | None = None


class UsbHubEntry(CaptureModel, frozen=True):
    """What one hub said about itself.

    Attributes:
        information: ``USB_HUB_INFORMATION_EX``, whose type says root, USB 2 or USB 3.
        error: Why the hub could not be opened.
    """

    information: EncodedPayload | None = None
    error: DeviceText | None = None


class VolumeEntry(CaptureModel, frozen=True):
    """What one volume answered, keyed in :class:`WindowsCapture` by a per-capture ordinal.

    Attributes:
        paths: Every mount path the volume answers to: a drive letter, a bare
            folder mount, or both when a letter is also mounted as a folder.
        disks: The disk numbers the volume's extents span; more than one for a
            spanned or striped dynamic volume, none for a volume this machine
            could not place on a disk at all (a RAM disk).
        esp: Whether the volume's partition is the EFI System Partition, or
            ``None`` when the partition ioctl itself failed.
        error: Why the volume could not be opened (neither ioctl was then
            issued), or why its disk-extents ioctl failed.
        paths_error: Why the volume's mount paths could not be read, in which
            case `paths` is empty without meaning the volume has none.
        drive_type: What ``GetDriveTypeW`` answered for the volume's first
            path (``DRIVE_FIXED``, ``DRIVE_CDROM``, ...), so the resolver can
            tell a failure on an optical drive or a RAM disk, which can sit on
            no physical disk, from one on a fixed volume. ``None`` for a volume
            with no path.
    """

    paths: Entries[DeviceText] = ()
    disks: Entries[Annotated[int, Field(ge=0, le=_UINT32_MAX)]] = ()
    esp: bool | None = None
    error: DeviceText | None = None
    paths_error: DeviceText | None = None
    drive_type: int | None = Field(default=None, ge=0, le=_UINT32_MAX)


class WindowsCapture(CaptureHeader, frozen=True):
    r"""A whole Windows reading.

    Attributes:
        platform: Always Windows; it is what selects this model.
        elevated: Whether the reader ran as Administrator.
        devices_accessible: Whether any disk could be opened at all.
        environment: The evidence for bare metal, a guest or a container.
        pci: Every PCI device, keyed by instance identifier.
        pci_names: Device names the reader resolved, keyed ``vendor:device``, so
            a replay names devices the way the capturing machine did. Absent
            from a capture taken before this field existed, which is the one
            case a builder still has to fall back on the replaying machine for.
        disks: Every disk, keyed by interface path.
        usb_ports: The port every USB device above a disk is plugged into,
            keyed by the instance identifier of that device. Absent from a
            capture taken before USB links were read.
        usb_hubs: Every hub one of those ports belongs to, keyed by its
            instance identifier.
        volumes: Every volume Windows enumerated, keyed by an opaque ordinal
            string local to this capture (``"0"``, ``"1"``, ...) rather than
            its ``\\?\Volume{...}\`` GUID path, which never reaches a
            capture. ``None`` for a capture taken before volumes were read,
            which leaves every disk's usage undecidable rather than claiming
            none was found. A capture taken before this field held the
            ordinal keys the GUID path itself; either way the key is opaque
            and only ever looked up, never parsed.
        windows_volume: The ordinal key, within :attr:`volumes`, of the
            volume the Windows directory lives on, so the builder can mark
            its disk as boot. ``None`` when it could not be resolved.
    """

    platform: Literal[Platform.WINDOWS]
    elevated: bool = False
    devices_accessible: bool = True
    environment: VirtualizationEvidence = VirtualizationEvidence()
    pci: EntryMap[DeviceText, PciEntry]
    pci_names: EntryMap[DeviceText, DeviceText] = Field(default_factory=dict[DeviceText, DeviceText])
    disks: EntryMap[DeviceText, DiskEntry] = Field(default_factory=dict[DeviceText, DiskEntry])
    usb_ports: EntryMap[DeviceText, UsbPortEntry] = Field(default_factory=dict[DeviceText, UsbPortEntry])
    usb_hubs: EntryMap[DeviceText, UsbHubEntry] = Field(default_factory=dict[DeviceText, UsbHubEntry])
    volumes: EntryMap[DeviceText, VolumeEntry] | None = None
    windows_volume: DeviceText | None = None


__all__ = [
    "DiskEntry",
    "DiskTemperature",
    "HealthBlobs",
    "PciEntry",
    "StorageDescriptor",
    "UsbHubEntry",
    "UsbPortEntry",
    "VolumeEntry",
    "WindowsCapture",
    "bus_type_of",
]
