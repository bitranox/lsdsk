"""The typed shape of a Linux reading.

:mod:`.reader` writes a plain JSON-serialisable mapping, because that is what a
snapshot stores. This is where the mapping becomes typed, once, for a live run
and a replay alike, so :mod:`.builder` reads attributes rather than keys and a
replay whose sections have the wrong shape is refused as a bad file instead of
failing deep inside the mapping.

The maps keyed by data stay maps: PCI address to device, block node to device,
sysfs device name to class entry. What they hold has a fixed set of keys, and
those entries are models.

Only the keys the builder reads are modelled; the rest of a capture is ignored
rather than refused. Values stay the text sysfs published. Turning
``8.0 GT/s PCIe`` or a link width into a number is decoding, and it stays in the
builder's tolerant parsers, where an unparsable value becomes "not measured".
Doing it here would refuse a whole live run over one odd attribute.

System Role:
    Adapter layer, the parse step between :mod:`.reader` and :mod:`.builder`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from ....domain.enums import Platform
from ..capture import CaptureHeader, CaptureModel, DeviceText, EncodedPayload, Entries, EntryMap
from ..decode.virtualization import VirtualizationEvidence

# ``rotational`` is a kernel-published ``0``/``1`` text flag, unlike most of
# what a capture carries as text: sysfs never publishes anything else there,
# so an unrecognised value has no established meaning and reads as "not
# measured" rather than being guessed at. Windows already carries this as a
# real bool (``DiskEntry.rotating``); this is the Linux side of the same
# boundary conversion, keyed by the exact wire text rather than a dict lookup
# because there are only ever the two values worth naming.
_ROTATING_VALUES: dict[str, bool] = {"0": False, "1": True}

# The widths of the registers and the kernel types these fields are read
# from. They are facts from the specifications rather than policy, which is
# why they sit at the parse: a capture is untrusted input, and a value wider
# than its own source was never read from hardware. Unbounded, two of them
# reached figures the tool then stated - a ports bitmap of 14,000 bits was
# counted into "14000 ports, 13999 free", and a capacity past what a float
# holds ended `lsdsk disks` in a bare OverflowError.
_UINT32_MAX = 0xFFFFFFFF  # an AHCI register, and uid_t
_PCIE_PORT_TYPE_MAX = 0xF  # the PCIe capability's Device/Port Type, 4 bits
_PCIE_SLOT_NUMBER_MAX = 0x1FFF  # Slot Capabilities' Physical Slot Number, 13 bits


def _rotating_of(value: object) -> object:
    """Parse the kernel's rotational flag into a bool, at the boundary.

    Anything other than the two kernel-published digits - an empty read, a
    driver that publishes something else - becomes ``None`` rather than a
    guess, the same way :func:`~lsdsk.adapters.hw.windows.capture.bus_type_of`
    leaves an unrecognised transport unresolved rather than inventing one.

    Example:
        >>> _rotating_of("1")
        True
        >>> _rotating_of("0")
        False
        >>> _rotating_of("unexpected") is None
        True
    """
    return _ROTATING_VALUES.get(value) if isinstance(value, str) else value


class AhciRegisters(CaptureModel, frozen=True):
    """The two AHCI registers a privileged run read from the controller itself.

    Attributes:
        capability: The HBA capability register.
        ports_implemented: The ports-implemented bitmap.
    """

    capability: int = Field(ge=0, le=_UINT32_MAX)
    ports_implemented: int = Field(ge=0, le=_UINT32_MAX)


class PciEntry(CaptureModel, frozen=True):
    """One PCI device, as sysfs and its configuration space describe it.

    Attributes:
        class_code: The class triple as sysfs writes it, such as ``0x010601``.
            Aliased because ``class`` is a keyword.
        vendor: The vendor identifier.
        device: The device identifier.
        driver: The bound driver's name.
        current_link_speed: The negotiated PCIe speed, such as ``8.0 GT/s PCIe``.
        current_link_width: The negotiated width.
        max_link_speed: The best speed the device supports.
        max_link_width: The widest link the device supports.
        path: The resolved sysfs path, which carries the device's parentage.
        children: The PCI addresses directly below a bridge.
        ahci: The AHCI registers, recorded for an AHCI controller on a
            privileged run only.
        ahci_error: Why those registers could not be read, when the host refused
            the mapping rather than the controller having no such region. The two
            are not the same fact: a controller with no BAR5 has nothing to
            report, while a refused mapping means the port count is unknown and
            the run is incomplete.
        slot_implemented: Whether the port ends in a physical connector, read
            from configuration space and therefore only with privilege.
        slot_number: The physical slot number the board assigned the port.
        pcie_port_type: The PCIe capability's port type byte, which names a
            root port against a switch's upstream and downstream legs. The
            reader has always written it; declaring it here stops the parse
            from discarding it.
    """

    class_code: DeviceText | None = Field(default=None, alias="class")
    vendor: DeviceText | None = None
    device: DeviceText | None = None
    driver: DeviceText | None = None
    current_link_speed: DeviceText | None = None
    current_link_width: DeviceText | None = None
    max_link_speed: DeviceText | None = None
    max_link_width: DeviceText | None = None
    path: DeviceText = ""
    children: Entries[DeviceText] = ()
    ahci: AhciRegisters | None = None
    ahci_error: DeviceText | None = None
    slot_implemented: bool | None = None
    slot_number: int | None = Field(default=None, ge=0, le=_PCIE_SLOT_NUMBER_MAX)
    pcie_port_type: int | None = Field(default=None, ge=0, le=_PCIE_PORT_TYPE_MAX)


class ClassEntry(CaptureModel, frozen=True):
    """What every sysfs class entry carries.

    Attributes:
        path: The entry's resolved sysfs path, which names the controller it
            hangs off.
    """

    path: DeviceText = ""


class ScsiHostEntry(ClassEntry, frozen=True):
    """A SCSI host, which is where an HBA publishes its own identity.

    Attributes:
        board_name: The board the driver reports, when it reports one.
        version_fw: The HBA firmware version.
    """

    board_name: DeviceText | None = None
    version_fw: DeviceText | None = None


class SasPhyEntry(ClassEntry, frozen=True):
    """A SAS phy.

    Attributes:
        negotiated_linkrate: The rate the phy negotiated, such as ``6.0 Gbit``.
        maximum_linkrate_hw: The fastest rate the phy hardware supports.
    """

    negotiated_linkrate: DeviceText | None = None
    maximum_linkrate_hw: DeviceText | None = None


class AtaLinkEntry(ClassEntry, frozen=True):
    """A libata link.

    Attributes:
        sata_spd: The negotiated SATA speed, such as ``6.0 Gbps``.
        sata_spd_max: The fastest speed libata allows on the link.
    """

    sata_spd: DeviceText | None = None
    sata_spd_max: DeviceText | None = None


class NvmeClassEntry(ClassEntry, frozen=True):
    """An NVMe controller's sysfs identity, readable without privilege.

    Attributes:
        model: The model string.
        serial: The serial number.
        firmware_rev: The firmware revision.
    """

    model: DeviceText | None = None
    serial: DeviceText | None = None
    firmware_rev: DeviceText | None = None


class HwmonEntry(ClassEntry, frozen=True):
    """A hardware monitor.

    Attributes:
        temp1_input: The first temperature, in thousandths of a degree.
    """

    temp1_input: DeviceText | None = None


class SysfsClasses(CaptureModel, frozen=True):
    """The sysfs classes that describe storage topology, each keyed by device name.

    Attributes:
        scsi_host: SCSI hosts.
        sas_phy: SAS phys.
        ata_link: libata links.
        ata_port: libata ports, counted rather than read.
        nvme: NVMe controllers.
        hwmon: Hardware monitors.
    """

    scsi_host: EntryMap[DeviceText, ScsiHostEntry] = Field(default_factory=dict[DeviceText, ScsiHostEntry])
    sas_phy: EntryMap[DeviceText, SasPhyEntry] = Field(default_factory=dict[DeviceText, SasPhyEntry])
    ata_link: EntryMap[DeviceText, AtaLinkEntry] = Field(default_factory=dict[DeviceText, AtaLinkEntry])
    ata_port: EntryMap[DeviceText, ClassEntry] = Field(default_factory=dict[DeviceText, ClassEntry])
    nvme: EntryMap[DeviceText, NvmeClassEntry] = Field(default_factory=dict[DeviceText, NvmeClassEntry])
    hwmon: EntryMap[DeviceText, HwmonEntry] = Field(default_factory=dict[DeviceText, HwmonEntry])


class QueueAttributes(CaptureModel, frozen=True):
    """A block device's queue attributes.

    Attributes:
        rotational: Whether the kernel reports rotating media, parsed from the
            wire's ``1``/``0`` text at this boundary rather than left for the
            mapping layer to compare against a literal. ``None`` when sysfs
            could not be read or published something this format never uses.
    """

    rotational: Annotated[bool | None, BeforeValidator(_rotating_of)] = None


class DeviceAttributes(CaptureModel, frozen=True):
    """The attributes of the SCSI or SATA device behind a block node.

    Attributes:
        model: The model string.
        rev: The firmware revision.
        wwid: The world-wide identifier.
    """

    model: DeviceText | None = None
    rev: DeviceText | None = None
    wwid: DeviceText | None = None


class VpdPages(CaptureModel, frozen=True):
    """The vital product data pages read without privilege, base64 encoded.

    Attributes:
        vpd_pg89: The ATA Information page, which embeds the IDENTIFY data.
    """

    vpd_pg89: EncodedPayload | None = None


class MountEntry(CaptureModel, frozen=True):
    """One row of mountinfo.

    Attributes:
        dev: The mounted filesystem's own ``maj:min``, mountinfo's field 3.
        mountpoint: Where it is mounted, with the kernel's octal path escapes
            already undone.
        fstype: The filesystem type, such as ``zfs`` or ``vfat``.
        source: What was mounted, which is a dataset name for ``zfs``, a UUID
            or label for many others, or a ``/dev/`` path.
        source_dev: The ``maj:min`` of ``source``, when it names a resolvable
            device node. ``None`` for a source that is not a device path (a
            ZFS dataset, ``tmpfs``, an NFS export) and for one that could not
            be resolved.
    """

    dev: DeviceText
    mountpoint: DeviceText
    fstype: DeviceText = ""
    source: DeviceText = ""
    source_dev: DeviceText | None = None


class SwapEntry(CaptureModel, frozen=True):
    """One row of ``/proc/swaps``, with its header already skipped.

    Attributes:
        path: The swap file or partition's path.
        dev: Its ``maj:min``, when ``path`` resolves to a device node. ``None``
            for a swap file on a filesystem.
    """

    path: DeviceText
    dev: DeviceText | None = None


class PartitionEntry(CaptureModel, frozen=True):
    """One partition of a disk, from its sysfs child directory.

    Attributes:
        dev: The partition's own ``maj:min``, when read.
        holders: What sits directly on it (a device-mapper device's kernel
            name), keyed the same way :data:`LinuxCapture.stacked` is.
    """

    dev: DeviceText | None = None
    holders: Entries[DeviceText] = ()


class StackedEntry(CaptureModel, frozen=True):
    """One device-mapper device sitting on a disk or a partition.

    Attributes:
        dev: The device's own ``maj:min``, when read.
        dm_name: The mapping's name, such as ``cryptroot``, when the device
            publishes one.
        dm_uuid: The mapping's UUID, which names its TYPE (``CRYPT-LUKS2-...``,
            ``LVM-...``) as its first dash-separated field.
        holders: What sits directly on this device, so a crypt-under-LVM chain
            is readable one layer at a time.
    """

    dev: DeviceText | None = None
    dm_name: DeviceText | None = None
    dm_uuid: DeviceText | None = None
    holders: Entries[DeviceText] = ()


class FilesystemSignature(CaptureModel, frozen=True):
    """What the udev database says a block device's content is formatted as.

    Only the two properties this tool reads: the database also carries
    serials and by-id paths, and keeping those out of a capture's new fields
    is what keeps them free of identifiers.

    Attributes:
        fs_type: The filesystem or RAID member type udev detected, such as
            ``zfs_member``.
        fs_label: The filesystem's own label, when it has one.
    """

    fs_type: DeviceText | None = None
    fs_label: DeviceText | None = None


class BlockEntry(CaptureModel, frozen=True):
    """One block device.

    Attributes:
        size: The size in 512-byte units, as sysfs writes it.
        virtual: Whether the kernel put the device under its virtual tree.
        wwid: The block-level stable identifier, which NVMe publishes.
        uuid: The namespace UUID, when the drive publishes one.
        queue: The queue attributes.
        device: The attributes of the device behind the node.
        device_path: The resolved sysfs path of that device.
        vpd: The VPD pages the device answered.
        hwmon: The resolved paths of the hardware monitors the device owns.
        dev: The block device's own ``maj:min``, recorded for a non-virtual
            device only - a virtual one is already named by ``virtual: True``.
        holders: What sits directly on the whole device (never on one of its
            partitions), such as a device-mapper device using the raw disk.
        partitions: The device's partitions, keyed by kernel name, when it has
            any.
    """

    size: DeviceText | None = None
    virtual: bool = False
    wwid: DeviceText | None = None
    uuid: DeviceText | None = None
    queue: QueueAttributes = QueueAttributes()
    device: DeviceAttributes = DeviceAttributes()
    device_path: DeviceText = ""
    vpd: VpdPages = VpdPages()
    hwmon: Entries[DeviceText] = ()
    dev: DeviceText | None = None
    holders: Entries[DeviceText] = ()
    partitions: EntryMap[DeviceText, PartitionEntry] | None = None


class AtaBlobs(CaptureModel, frozen=True):
    """What ATA passthrough returned for one disk, base64 encoded.

    The ``*_error`` fields are the other half of every payload field: the reader
    writes one or the other, never both, and until they were named here the
    refusal was dropped at this boundary while the payload got through. A
    reading that is absent because the drive refused it is a different fact from
    one that is absent because nobody looked, and only this text tells them
    apart.

    Attributes:
        identify: The IDENTIFY DEVICE data.
        smart_data: The SMART READ DATA structure.
        smart_thresholds: The SMART READ THRESHOLDS structure.
        error: Why the device node could not be opened at all.
        identify_error: Why IDENTIFY was refused.
        smart_data_error: Why SMART READ DATA was refused.
        smart_thresholds_error: Why SMART READ THRESHOLDS was refused.
    """

    identify: EncodedPayload | None = None
    smart_data: EncodedPayload | None = None
    smart_thresholds: EncodedPayload | None = None
    error: DeviceText | None = None
    identify_error: DeviceText | None = None
    smart_data_error: DeviceText | None = None
    smart_thresholds_error: DeviceText | None = None


class NvmeBlobs(CaptureModel, frozen=True):
    """What NVMe admin passthrough returned for one disk, base64 encoded.

    Attributes:
        identify_controller: The Identify Controller structure.
        smart_log: The SMART / Health Information log page.
        error: Why the device node could not be opened at all.
        identify_controller_error: Why Identify Controller was refused.
        smart_log_error: Why the health log was refused.
    """

    identify_controller: EncodedPayload | None = None
    smart_log: EncodedPayload | None = None
    error: DeviceText | None = None
    identify_controller_error: DeviceText | None = None
    smart_log_error: DeviceText | None = None


class UsbDeviceEntry(CaptureModel, frozen=True):
    """One USB device on the way from a USB disk to its root hub, as sysfs published it.

    Values stay the text the kernel wrote; decoding them is the builder's job.
    No serial, product or manufacturer string is recorded: nothing reads them,
    and a fixture would have to scrub each.

    The reader also records ``version`` (bcdUSB) and ``bDeviceClass`` for a bug
    report. They are not named here because no builder reads them: whether a
    device has a BOS is decided by the reader and arrives as ``bos_none``, a
    root hub is told by its directory name, and every device between a disk and
    its root hub is a hub by its place in the chain. A key this model does not
    name is ignored, so a capture carrying them still loads.

    Attributes:
        name: The device directory's name, ``usb1`` for a root hub, ``1-1.2`` below it.
        speed: The running speed in Mb/s, as the kernel writes it.
        rx_lanes: The lanes it receives on (USB 3.2).
        tx_lanes: The lanes it transmits on (USB 3.2).
        interface_drivers: The drivers bound to its interfaces, ``uas`` or ``usb-storage`` for a disk.
        peer_hub: The sysfs path of the hub owning the other-speed half of the socket it is in.
        bos: Its BOS descriptor, from sysfs or usbfs.
        bos_none: The device has no BOS: bcdUSB is below 2.01, or it STALLed the request.
        bos_error: Why the BOS could not be read.
    """

    name: DeviceText = ""
    speed: DeviceText | None = None
    rx_lanes: DeviceText | None = None
    tx_lanes: DeviceText | None = None
    interface_drivers: Entries[DeviceText] = ()
    peer_hub: DeviceText | None = None
    bos: EncodedPayload | None = None
    bos_none: bool = False
    bos_error: DeviceText | None = None


class LinuxCapture(CaptureHeader, frozen=True):
    """A whole Linux reading.

    Attributes:
        platform: Always Linux; it is what selects this model.
        euid: The effective user the reader ran as, which decides what it could
            read.
        devices_accessible: Whether the device nodes needed to interrogate a disk
            existed.
        environment: The evidence for bare metal, a guest or a container.
        pci: Every PCI device, keyed by address.
        pci_names: Device names the reader resolved, keyed ``vendor:device``, so
            a replay names devices the way the capturing machine did.
        classes: The sysfs classes that describe storage topology.
        block: Every block device, keyed by node name.
        ata: ATA passthrough results, keyed by node name.
        nvme: NVMe passthrough results, keyed by node name.
        usb: Every USB device between a USB disk and its root hub, keyed by sysfs path.
        mounts: Every mounted filesystem, from mountinfo. ``None`` when
            mountinfo could not be read at all, a different fact from a
            machine with no mounts, which mountinfo never reports.
        swaps: Every active swap.
        stacked: Every device-mapper device reached from a disk's or a
            partition's holders, keyed by kernel name (``dm-0``).
        signatures: What the udev database says each device is formatted as,
            keyed by ``maj:min``. ``None`` when the udev database itself is
            not there, such as in a container with no ``/run/udev/data`` -
            a different fact from no device carrying a signature.
    """

    platform: Literal[Platform.LINUX]
    euid: int | None = Field(default=None, ge=0, le=_UINT32_MAX)
    devices_accessible: bool = True
    environment: VirtualizationEvidence = VirtualizationEvidence()
    pci: EntryMap[DeviceText, PciEntry]
    pci_names: EntryMap[DeviceText, DeviceText] = Field(default_factory=dict[DeviceText, DeviceText])
    classes: SysfsClasses = SysfsClasses()
    block: EntryMap[DeviceText, BlockEntry] = Field(default_factory=dict[DeviceText, BlockEntry])
    ata: EntryMap[DeviceText, AtaBlobs] = Field(default_factory=dict[DeviceText, AtaBlobs])
    nvme: EntryMap[DeviceText, NvmeBlobs] = Field(default_factory=dict[DeviceText, NvmeBlobs])
    usb: EntryMap[DeviceText, UsbDeviceEntry] = Field(default_factory=dict[DeviceText, UsbDeviceEntry])
    mounts: Entries[MountEntry] | None = None
    swaps: Entries[SwapEntry] = ()
    stacked: EntryMap[DeviceText, StackedEntry] = Field(default_factory=dict[DeviceText, StackedEntry])
    signatures: EntryMap[DeviceText, FilesystemSignature] | None = None


__all__ = [
    "AhciRegisters",
    "AtaBlobs",
    "AtaLinkEntry",
    "BlockEntry",
    "ClassEntry",
    "DeviceAttributes",
    "FilesystemSignature",
    "HwmonEntry",
    "LinuxCapture",
    "MountEntry",
    "NvmeBlobs",
    "NvmeClassEntry",
    "PartitionEntry",
    "PciEntry",
    "QueueAttributes",
    "SasPhyEntry",
    "ScsiHostEntry",
    "StackedEntry",
    "SwapEntry",
    "SysfsClasses",
    "UsbDeviceEntry",
    "VpdPages",
]
