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

from typing import Literal

from pydantic import Field

from ....domain.enums import Platform
from ..capture import CaptureHeader, CaptureModel
from ..decode.virtualization import VirtualizationEvidence


class AhciRegisters(CaptureModel):
    """The two AHCI registers a privileged run read from the controller itself.

    Attributes:
        capability: The HBA capability register.
        ports_implemented: The ports-implemented bitmap.
    """

    capability: int
    ports_implemented: int


class PciEntry(CaptureModel):
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
        slot_implemented: Whether the port ends in a physical connector, read
            from configuration space and therefore only with privilege.
        slot_number: The physical slot number the board assigned the port.
        pcie_port_type: The PCIe capability's port type byte, which names a
            root port against a switch's upstream and downstream legs. The
            reader has always written it; declaring it here stops the parse
            from discarding it.
    """

    class_code: str | None = Field(default=None, alias="class")
    vendor: str | None = None
    device: str | None = None
    driver: str | None = None
    current_link_speed: str | None = None
    current_link_width: str | None = None
    max_link_speed: str | None = None
    max_link_width: str | None = None
    path: str = ""
    children: tuple[str, ...] = ()
    ahci: AhciRegisters | None = None
    slot_implemented: bool | None = None
    slot_number: int | None = None
    pcie_port_type: int | None = None


class ClassEntry(CaptureModel):
    """What every sysfs class entry carries.

    Attributes:
        path: The entry's resolved sysfs path, which names the controller it
            hangs off.
    """

    path: str = ""


class ScsiHostEntry(ClassEntry):
    """A SCSI host, which is where an HBA publishes its own identity.

    Attributes:
        board_name: The board the driver reports, when it reports one.
        version_fw: The HBA firmware version.
    """

    board_name: str | None = None
    version_fw: str | None = None


class SasPhyEntry(ClassEntry):
    """A SAS phy.

    Attributes:
        negotiated_linkrate: The rate the phy negotiated, such as ``6.0 Gbit``.
        maximum_linkrate_hw: The fastest rate the phy hardware supports.
    """

    negotiated_linkrate: str | None = None
    maximum_linkrate_hw: str | None = None


class AtaLinkEntry(ClassEntry):
    """A libata link.

    Attributes:
        sata_spd: The negotiated SATA speed, such as ``6.0 Gbps``.
        sata_spd_max: The fastest speed libata allows on the link.
    """

    sata_spd: str | None = None
    sata_spd_max: str | None = None


class NvmeClassEntry(ClassEntry):
    """An NVMe controller's sysfs identity, readable without privilege.

    Attributes:
        model: The model string.
        serial: The serial number.
        firmware_rev: The firmware revision.
    """

    model: str | None = None
    serial: str | None = None
    firmware_rev: str | None = None


class HwmonEntry(ClassEntry):
    """A hardware monitor.

    Attributes:
        temp1_input: The first temperature, in thousandths of a degree.
    """

    temp1_input: str | None = None


class SysfsClasses(CaptureModel):
    """The sysfs classes that describe storage topology, each keyed by device name.

    Attributes:
        scsi_host: SCSI hosts.
        sas_phy: SAS phys.
        ata_link: libata links.
        ata_port: libata ports, counted rather than read.
        nvme: NVMe controllers.
        hwmon: Hardware monitors.
    """

    scsi_host: dict[str, ScsiHostEntry] = Field(default_factory=dict[str, ScsiHostEntry])
    sas_phy: dict[str, SasPhyEntry] = Field(default_factory=dict[str, SasPhyEntry])
    ata_link: dict[str, AtaLinkEntry] = Field(default_factory=dict[str, AtaLinkEntry])
    ata_port: dict[str, ClassEntry] = Field(default_factory=dict[str, ClassEntry])
    nvme: dict[str, NvmeClassEntry] = Field(default_factory=dict[str, NvmeClassEntry])
    hwmon: dict[str, HwmonEntry] = Field(default_factory=dict[str, HwmonEntry])


class QueueAttributes(CaptureModel):
    """A block device's queue attributes.

    Attributes:
        rotational: ``1`` for rotating media, ``0`` otherwise.
    """

    rotational: str | None = None


class DeviceAttributes(CaptureModel):
    """The attributes of the SCSI or SATA device behind a block node.

    Attributes:
        model: The model string.
        rev: The firmware revision.
        wwid: The world-wide identifier.
    """

    model: str | None = None
    rev: str | None = None
    wwid: str | None = None


class VpdPages(CaptureModel):
    """The vital product data pages read without privilege, base64 encoded.

    Attributes:
        vpd_pg89: The ATA Information page, which embeds the IDENTIFY data.
    """

    vpd_pg89: str | None = None


class BlockEntry(CaptureModel):
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
    """

    size: str | None = None
    virtual: bool = False
    wwid: str | None = None
    uuid: str | None = None
    queue: QueueAttributes = QueueAttributes()
    device: DeviceAttributes = DeviceAttributes()
    device_path: str = ""
    vpd: VpdPages = VpdPages()
    hwmon: tuple[str, ...] = ()


class AtaBlobs(CaptureModel):
    """What ATA passthrough returned for one disk, base64 encoded.

    Attributes:
        identify: The IDENTIFY DEVICE data.
        smart_data: The SMART READ DATA structure.
        smart_thresholds: The SMART READ THRESHOLDS structure.
    """

    identify: str | None = None
    smart_data: str | None = None
    smart_thresholds: str | None = None


class NvmeBlobs(CaptureModel):
    """What NVMe admin passthrough returned for one disk, base64 encoded.

    Attributes:
        identify_controller: The Identify Controller structure.
        smart_log: The SMART / Health Information log page.
    """

    identify_controller: str | None = None
    smart_log: str | None = None


class LinuxCapture(CaptureHeader):
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
    """

    platform: Literal[Platform.LINUX]
    euid: int | None = None
    devices_accessible: bool = True
    environment: VirtualizationEvidence = VirtualizationEvidence()
    pci: dict[str, PciEntry]
    pci_names: dict[str, str] = Field(default_factory=dict[str, str])
    classes: SysfsClasses = SysfsClasses()
    block: dict[str, BlockEntry] = Field(default_factory=dict[str, BlockEntry])
    ata: dict[str, AtaBlobs] = Field(default_factory=dict[str, AtaBlobs])
    nvme: dict[str, NvmeBlobs] = Field(default_factory=dict[str, NvmeBlobs])


__all__ = [
    "AhciRegisters",
    "AtaBlobs",
    "AtaLinkEntry",
    "BlockEntry",
    "ClassEntry",
    "DeviceAttributes",
    "HwmonEntry",
    "LinuxCapture",
    "NvmeBlobs",
    "NvmeClassEntry",
    "PciEntry",
    "QueueAttributes",
    "SasPhyEntry",
    "ScsiHostEntry",
    "SysfsClasses",
    "VpdPages",
]
