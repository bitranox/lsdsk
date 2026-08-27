"""Read storage topology and device blobs from a live Linux system.

The impure half of the Linux adapter.  It never runs a subprocess: topology
comes from sysfs and the device structures come from ioctls issued directly.

Two privilege tiers, and the tool works in both:
    * unprivileged: sysfs topology, PCIe link state, SATA capability and
      negotiated speed from the world-readable ``vpd_pg89`` page, SAS phy rates,
      NVMe temperature from hwmon
    * root: ATA SMART through SG_IO passthrough and the NVMe health log through
      the admin passthrough ioctl, which is where wear-out lives; the PCIe
      capability structures past the first 64 bytes of config space, which carry
      the physical slot number and the Slot Implemented bit; and the AHCI
      ports-implemented bitmap, which needs BAR5 mapped and is refused on some
      hosts even as root

Listing all three matters because they fail the same way, as a ``-`` rather than
an error, and a reader who believes only SMART is gated reads the other two
blanks as absent hardware.

Every command issued is a read: ATA IDENTIFY DEVICE, ATA SMART READ DATA and
THRESHOLDS, NVMe Identify and NVMe Get Log Page.  Nothing is ever written to a
device.

System Role:
    Adapter layer, reading half.  Produces the plain mapping that
    :mod:`.builder` turns into domain objects, and that a snapshot stores.
"""

from __future__ import annotations

import base64
import ctypes
import mmap
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from ....domain.enums import Platform
from ..decode import ahci, pciids
from ..decode.virtualization import container_markers_in_mounts
from ..snapshot import SCHEMA_VERSION

# ioctl numbers. SG_IO is the generic SCSI passthrough; the NVMe admin command
# code is _IOWR('N', 0x41, struct nvme_passthru_cmd) evaluated for a 72-byte
# structure, which is fixed by the kernel ABI and identical on every port.
SG_IO = 0x2285
SG_DXFER_FROM_DEV = -3
NVME_IOCTL_ADMIN_CMD = 0xC0484E41

# ATA commands, all read-only.
ATA_IDENTIFY_DEVICE = 0xEC
ATA_SMART = 0xB0
SMART_READ_DATA = 0xD0
SMART_READ_THRESHOLDS = 0xD1
# SMART commands carry a fixed signature in the LBA mid and high registers.
SMART_LBA_SIGNATURE = 0xC24F00

# NVMe admin opcodes and the SMART/Health log page identifier.
NVME_IDENTIFY = 0x06
NVME_GET_LOG_PAGE = 0x02
NVME_SMART_LOG_ID = 0x02
NVME_ALL_NAMESPACES = 0xFFFFFFFF

_SECTOR_BYTES = 512
_IOCTL_TIMEOUT_MS = 10_000

# A sysfs child directory named like a PCI address, "0000:03:00.0", carries two colons.
_PCI_ADDRESS_COLONS = 2


class SgIoHeader(ctypes.Structure):
    """The ``sg_io_hdr_t`` structure the SG_IO ioctl expects."""

    _fields_ = (
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    )


class NvmePassthruCommand(ctypes.Structure):
    """The ``nvme_passthru_cmd`` structure the NVMe admin ioctl expects."""

    _fields_ = (
        ("opcode", ctypes.c_ubyte),
        ("flags", ctypes.c_ubyte),
        ("rsvd1", ctypes.c_ushort),
        ("nsid", ctypes.c_uint),
        ("cdw2", ctypes.c_uint),
        ("cdw3", ctypes.c_uint),
        ("metadata", ctypes.c_ulonglong),
        ("addr", ctypes.c_ulonglong),
        ("metadata_len", ctypes.c_uint),
        ("data_len", ctypes.c_uint),
        ("cdw10", ctypes.c_uint),
        ("cdw11", ctypes.c_uint),
        ("cdw12", ctypes.c_uint),
        ("cdw13", ctypes.c_uint),
        ("cdw14", ctypes.c_uint),
        ("cdw15", ctypes.c_uint),
        ("timeout_ms", ctypes.c_uint),
        ("result", ctypes.c_uint),
    )


def _ata_passthrough_cdb(*, command: int, feature: int, lba: int, count: int) -> ctypes.Array[ctypes.c_ubyte]:
    """Build a 16-byte SCSI ATA PASS-THROUGH command block.

    The 16-byte form splits each ATA register into a high and a low byte so that
    48-bit addressing fits, which is why the LBA bytes are not contiguous.
    """
    cdb = (ctypes.c_ubyte * 16)()
    cdb[0] = 0x85  # ATA PASS-THROUGH(16)
    cdb[1] = 4 << 1  # PIO data-in protocol
    cdb[2] = 0x0E  # transfer a sector count, in blocks, from the device
    cdb[3] = (feature >> 8) & 0xFF
    cdb[4] = feature & 0xFF
    cdb[5] = (count >> 8) & 0xFF
    cdb[6] = count & 0xFF
    cdb[7] = (lba >> 24) & 0xFF
    cdb[8] = lba & 0xFF
    cdb[9] = (lba >> 32) & 0xFF
    cdb[10] = (lba >> 8) & 0xFF
    cdb[11] = (lba >> 40) & 0xFF
    cdb[12] = (lba >> 16) & 0xFF
    cdb[13] = 0x00
    cdb[14] = command
    cdb[15] = 0x00
    return cdb


def ata_passthrough(fd: int, *, command: int, feature: int = 0, lba: int = 0, count: int = 1) -> bytes:
    """Issue one read-only ATA command and return its data.

    Args:
        fd: An open file descriptor for the block device.
        command: The ATA command code.
        feature: The feature register value.
        lba: The LBA register value, used by SMART as a fixed signature.
        count: Sector count to transfer.

    Returns:
        The data the device returned.

    Raises:
        OSError: If the ioctl fails or the transport reports an error.
    """
    buffer = ctypes.create_string_buffer(_SECTOR_BYTES * max(count, 1))
    sense = ctypes.create_string_buffer(32)
    cdb = _ata_passthrough_cdb(command=command, feature=feature, lba=lba, count=count)

    header = SgIoHeader()
    header.interface_id = ord("S")
    header.dxfer_direction = SG_DXFER_FROM_DEV
    header.cmd_len = 16
    header.mx_sb_len = 32
    header.dxfer_len = len(buffer)
    header.dxferp = ctypes.cast(buffer, ctypes.c_void_p)
    header.cmdp = ctypes.cast(cdb, ctypes.c_void_p)
    header.sbp = ctypes.cast(sense, ctypes.c_void_p)
    header.timeout = _IOCTL_TIMEOUT_MS

    # Imported here rather than at module scope because fcntl is Linux-only:
    # a module-level import makes this whole file unimportable on Windows, and
    # pytest --doctest-modules imports every module on every runner.
    import fcntl  # noqa: PLC0415 - platform-only dependency

    fcntl.ioctl(fd, SG_IO, header)
    if header.host_status or header.driver_status & 0x0F:
        message = f"SG_IO transport failure: host={header.host_status} driver={header.driver_status}"
        raise OSError(message)
    return bytes(buffer)


def nvme_admin(fd: int, *, opcode: int, nsid: int, cdw10: int, data_len: int) -> bytes:
    """Issue one read-only NVMe admin command and return its data.

    Args:
        fd: An open file descriptor for the NVMe device.
        opcode: The admin opcode.
        nsid: Namespace identifier.
        cdw10: Command dword 10, which carries the log or identify selector.
        data_len: Expected response length.

    Returns:
        The data the controller returned.

    Raises:
        OSError: If the ioctl fails.
    """
    buffer = ctypes.create_string_buffer(data_len)
    command = NvmePassthruCommand()
    command.opcode = opcode
    command.nsid = nsid
    command.addr = ctypes.cast(buffer, ctypes.c_void_p).value or 0
    command.data_len = data_len
    command.cdw10 = cdw10
    command.timeout_ms = _IOCTL_TIMEOUT_MS

    # Imported here rather than at module scope because fcntl is Linux-only:
    # a module-level import makes this whole file unimportable on Windows, and
    # pytest --doctest-modules imports every module on every runner.
    import fcntl  # noqa: PLC0415 - platform-only dependency

    fcntl.ioctl(fd, NVME_IOCTL_ADMIN_CMD, command)
    return bytes(buffer)


def smart_log_selector(length: int = 512, log_id: int = NVME_SMART_LOG_ID) -> int:
    """Build the command dword that selects a log page of a given length.

    NVMe counts the transfer in dwords, minus one, packed above the log
    identifier.

    Args:
        length: Response length in bytes.
        log_id: Log page identifier.

    Returns:
        The dword 10 value.

    Example:
        >>> hex(smart_log_selector())
        '0x7f0002'
    """
    return ((length // 4 - 1) << 16) | log_id


def _read_text(path: Path) -> str | None:
    """Read one sysfs attribute, returning ``None`` when it cannot be read."""
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return None


def _read_blob(path: Path) -> str | None:
    """Read one sysfs binary attribute as base64, or ``None``."""
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def _read_attrs(base: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Read a fixed set of attributes from one sysfs directory."""
    values: dict[str, str] = {}
    for name in names:
        value = _read_text(base / name)
        if value:
            values[name] = value
    return values


PCI_ATTRS = (
    "class",
    "vendor",
    "device",
    "subsystem_vendor",
    "subsystem_device",
    "revision",
    "current_link_speed",
    "current_link_width",
    "max_link_speed",
    "max_link_width",
)

CLASS_ATTRS: dict[str, tuple[str, ...]] = {
    "scsi_host": (
        "proc_name",
        "version_fw",
        "version_bios",
        "board_name",
        "board_assembly",
        "host_sas_address",
        "unique_id",
    ),
    "sas_phy": (
        "negotiated_linkrate",
        "minimum_linkrate_hw",
        "maximum_linkrate_hw",
        "phy_identifier",
        "sas_address",
        "device_type",
        "invalid_dword_count",
        "running_disparity_error_count",
        "loss_of_dword_sync_count",
    ),
    "ata_link": ("sata_spd", "sata_spd_max", "hw_sata_spd_limit", "sata_spd_limit"),
    "ata_port": ("port_no", "nr_pmp_links"),
    "nvme": ("model", "serial", "firmware_rev", "transport", "state"),
    "hwmon": ("name", "temp1_input", "temp1_label", "temp1_max", "temp1_crit", "temp1_alarm"),
}

BLOCK_QUEUE_ATTRS = ("rotational", "logical_block_size", "physical_block_size", "discard_granularity")
BLOCK_DEVICE_ATTRS = ("vendor", "model", "rev", "wwid", "sas_address", "state", "queue_depth", "type")
VPD_BLOBS = ("vpd_pg89", "vpd_pg80", "vpd_pg83", "vpd_pgb1", "inquiry")


# Offsets and identifiers within PCI configuration space.
_CONFIG_CAPABILITY_POINTER = 0x34
_CAPABILITY_ID_PCI_EXPRESS = 0x10
_CONFIG_HEADER_LENGTH = 0x40
# Bit 8 of the PCI Express Capabilities Register says this port terminates in a
# physical connector; bits 7:4 give the port type.
_SLOT_IMPLEMENTED_BIT = 1 << 8
_PORT_TYPE_SHIFT = 4
_PORT_TYPE_MASK = 0xF

# The Slot Capabilities register sits at +0x14 within the PCI Express capability
# structure and carries the board's own slot number in its top 13 bits.
_SLOT_CAPABILITIES_OFFSET = 0x14
_PHYSICAL_SLOT_SHIFT = 19


class PcieCapability(NamedTuple):
    """What the PCI Express capability structure says about a port.

    Attributes:
        port_type: The port type field, or ``None`` when undetermined.
        slot_implemented: Whether the port ends in a physical connector.
        physical_slot_number: The board's own number for that connector, which
            is what a mainboard manual labels its slots by. ``None`` unless a
            slot is implemented, because the register is only defined then.
    """

    port_type: int | None
    slot_implemented: bool | None
    physical_slot_number: int | None


def parse_pcie_capability(config: bytes) -> PcieCapability:
    """Return the PCIe port type, slot flag and physical slot number.

    Walks the PCI capability list looking for the PCI Express capability.  The
    list pointer lives in the first 64 bytes, which anyone may read, but the
    capability structures themselves sit beyond that and need root, so an
    unprivileged caller gets ``None`` rather than a wrong answer.

    A device with no PCI Express capability at all is a legacy PCI bridge, which
    is never a slot a card can be moved into.

    The slot number is the one datum tying a port to something a person can
    point at, because no readable source gives the form factor: firmware slot
    tables were measured on three boards here and named no M.2 socket at all.

    Args:
        config: PCI configuration space, however much of it could be read.

    Returns:
        The decoded capability, each field ``None`` when undetermined.

    Example:
        >>> parse_pcie_capability(bytes(16))
        PcieCapability(port_type=None, slot_implemented=None, physical_slot_number=None)
    """
    if len(config) <= _CONFIG_HEADER_LENGTH:
        return PcieCapability(port_type=None, slot_implemented=None, physical_slot_number=None)
    pointer = config[_CONFIG_CAPABILITY_POINTER]
    visited: set[int] = set()
    while pointer and pointer not in visited and pointer + 3 < len(config):
        visited.add(pointer)
        if config[pointer] == _CAPABILITY_ID_PCI_EXPRESS:
            capabilities = int.from_bytes(config[pointer + 2 : pointer + 4], "little")
            port_type = (capabilities >> _PORT_TYPE_SHIFT) & _PORT_TYPE_MASK
            implemented = bool(capabilities & _SLOT_IMPLEMENTED_BIT)
            number = _physical_slot_number(config, pointer, slot_implemented=implemented)
            return PcieCapability(port_type, implemented, number)
        pointer = config[pointer + 1]
    return PcieCapability(port_type=None, slot_implemented=False, physical_slot_number=None)


def _physical_slot_number(config: bytes, pointer: int, *, slot_implemented: bool) -> int | None:
    """Read the board's slot number out of the Slot Capabilities register.

    Undefined unless a slot is implemented, so a port with no connector reports
    nothing rather than the zero the register happens to hold.
    """
    start = pointer + _SLOT_CAPABILITIES_OFFSET
    if not slot_implemented or start + 4 > len(config):
        return None
    return int.from_bytes(config[start : start + 4], "little") >> _PHYSICAL_SLOT_SHIFT


def _to_class(value: object) -> int | None:
    """Return the PCI base and sub class from a class triple string."""
    if not isinstance(value, str):
        return None
    try:
        return int(value, 16) >> 8
    except ValueError:
        return None


def _read_config(device: Path) -> bytes:
    """Read as much PCI configuration space as this process is allowed."""
    try:
        return (device / "config").read_bytes()
    except OSError:
        return b""


# PCI class triple for a SATA controller in AHCI mode.
_AHCI_CLASS = 0x0106


def read_ahci_capabilities(device: Path) -> dict[str, int] | None:
    """Read an AHCI controller's own capability registers.

    They are memory mapped rather than in configuration space, so this needs the
    controller's sixth base address register and therefore root. Nothing else
    reports what an AHCI port can carry: libata publishes a speed limit only
    once one has been applied, so on healthy hardware sysfs simply has no answer.

    Only two read-only registers are touched, and the mapping is read-only.

    Args:
        device: The sysfs directory of the PCI device.

    Returns:
        The raw register values, or ``None`` when they cannot be reached.
    """
    resource = device / "resource5"
    try:
        size = resource.stat().st_size
    except OSError:
        return None
    if size < ahci.REGISTER_SPAN:
        return None
    try:
        handle = os.open(resource, os.O_RDONLY)
    except OSError:
        return None
    try:
        mapped = mmap.mmap(handle, min(size, mmap.PAGESIZE), prot=mmap.PROT_READ)
    except (OSError, ValueError):
        return None
    finally:
        os.close(handle)
    try:
        capability = int.from_bytes(mapped[ahci.CAPABILITY_OFFSET : ahci.CAPABILITY_OFFSET + 4], "little")
        implemented = int.from_bytes(
            mapped[ahci.PORTS_IMPLEMENTED_OFFSET : ahci.PORTS_IMPLEMENTED_OFFSET + 4], "little"
        )
    finally:
        mapped.close()
    return {"capability": capability, "ports_implemented": implemented}


def read_pci(root: Path = Path("/sys/bus/pci/devices")) -> dict[str, dict[str, Any]]:
    """Read every PCI device, with its link state, slot flag and children."""
    devices: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return devices
    for device in sorted(root.iterdir()):
        entry: dict[str, Any] = dict(_read_attrs(device, PCI_ATTRS))
        driver = device / "driver"
        if driver.is_symlink():
            entry["driver"] = Path(os.path.realpath(driver)).name
        entry["path"] = os.path.realpath(device)
        children = sorted(
            child.name for child in device.iterdir() if child.is_dir() and child.name.count(":") == _PCI_ADDRESS_COLONS
        )
        if children:
            entry["children"] = children
        if _to_class(entry.get("class")) == _AHCI_CLASS:
            registers = read_ahci_capabilities(device)
            if registers is not None:
                entry["ahci"] = registers
        capability = parse_pcie_capability(_read_config(device))
        if capability.port_type is not None:
            entry["pcie_port_type"] = capability.port_type
        if capability.slot_implemented is not None:
            entry["slot_implemented"] = capability.slot_implemented
        if capability.physical_slot_number is not None:
            entry["slot_number"] = capability.physical_slot_number
        devices[device.name] = entry
    return devices


def read_classes(root: Path = Path("/sys/class")) -> dict[str, dict[str, dict[str, str]]]:
    """Read the sysfs classes that describe storage topology."""
    classes: dict[str, dict[str, dict[str, str]]] = {}
    for class_name, attrs in CLASS_ATTRS.items():
        base = root / class_name
        if not base.is_dir():
            continue
        entries: dict[str, dict[str, str]] = {}
        for node in sorted(base.iterdir()):
            entry = _read_attrs(node, attrs)
            entry["path"] = os.path.realpath(node)
            entries[node.name] = entry
        classes[class_name] = entries
    return classes


def _is_kernel_virtual(node: Path, root: Path) -> bool:
    """Whether the kernel places this block device under its virtual tree.

    The name cannot answer this, in either direction. A prefix list written
    from the names known at the time called `zram` physical, so a RAM-backed
    device reached the inventory as a disk on a bus called `unknown` that
    reports no counters and never can; the same list called `sr` virtual, so an
    optical drive that does occupy an AHCI port was hidden from the port count.

    The kernel already answers it: a device with no physical parent resolves
    under ``/sys/devices/virtual``, while a real one resolves under its PCI
    path. That is a positive fact read from the system rather than an inference
    from a name.

    Args:
        node: A ``/sys/block`` entry, which is a symlink into ``/sys/devices``.
        root: The ``/sys/block`` directory, whose parent is the sysfs root.

    Returns:
        ``True`` when the device resolves under the sysfs virtual tree.
    """
    try:
        return node.resolve().is_relative_to((root.parent / "devices" / "virtual").resolve())
    except OSError:
        # An unresolvable link says nothing either way, and calling it virtual
        # on that basis would hide a real disk.
        return False


def read_block(root: Path = Path("/sys/block")) -> dict[str, dict[str, Any]]:
    """Read every block device's sysfs attributes and VPD pages.

    Kernel-virtual devices are kept and flagged rather than dropped. Dropping
    them made a device the machine really has disappear from its inventory,
    which reads as absent hardware rather than as hardware with nothing to
    report; the builder gives them ``BusType.VIRTUAL`` and every rule that
    needs a physical link then passes over them by class instead of by name.
    """
    disks: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return disks
    for node in sorted(root.iterdir()):
        entry: dict[str, Any] = {"size": _read_text(node / "size")}
        if _is_kernel_virtual(node, root):
            entry["virtual"] = True
        # The stable identifier lives at block level for NVMe and at device level
        # for SCSI and SATA, and both are readable without any privilege.
        for name in ("wwid", "uuid"):
            value = _read_text(node / name)
            if value:
                entry[name] = value
        entry["queue"] = _read_attrs(node / "queue", BLOCK_QUEUE_ATTRS)
        device = node / "device"
        if device.exists():
            entry["device"] = _read_attrs(device, BLOCK_DEVICE_ATTRS)
            entry["device_path"] = os.path.realpath(device)
            blobs = {name: _read_blob(device / name) for name in VPD_BLOBS}
            entry["vpd"] = {name: value for name, value in blobs.items() if value}
            monitors = sorted(device.glob("hwmon/hwmon*")) + sorted(device.glob("hwmon*"))
            if monitors:
                entry["hwmon"] = [os.path.realpath(monitor) for monitor in monitors]
        disks[node.name] = entry
    return disks


def read_ata_blobs(nodes: list[str]) -> dict[str, dict[str, str]]:
    """Read IDENTIFY and SMART structures from every ATA disk.

    A device that refuses passthrough, which happens behind some RAID drivers
    and always without root, records the reason instead of the data so the
    renderer can say why a column is empty rather than silently showing nothing.
    """
    commands = (
        ("identify", ATA_IDENTIFY_DEVICE, 0, 0),
        ("smart_data", ATA_SMART, SMART_READ_DATA, SMART_LBA_SIGNATURE),
        ("smart_thresholds", ATA_SMART, SMART_READ_THRESHOLDS, SMART_LBA_SIGNATURE),
    )
    results: dict[str, dict[str, str]] = {}
    for node in nodes:
        record: dict[str, str] = {}
        try:
            fd = os.open(f"/dev/{node}", os.O_RDONLY | os.O_NONBLOCK)
        except OSError as error:
            results[node] = {"error": str(error)}
            continue
        try:
            for label, command, feature, lba in commands:
                try:
                    payload = ata_passthrough(fd, command=command, feature=feature, lba=lba)
                    record[label] = base64.b64encode(payload).decode("ascii")
                except OSError as error:
                    record[f"{label}_error"] = str(error)
        finally:
            os.close(fd)
        results[node] = record
    return results


def read_nvme_blobs(nodes: list[str]) -> dict[str, dict[str, str]]:
    """Read Identify and the health log from every NVMe disk."""
    commands = (
        ("identify_controller", NVME_IDENTIFY, 0, 1, 4096),
        ("smart_log", NVME_GET_LOG_PAGE, NVME_ALL_NAMESPACES, smart_log_selector(), 512),
    )
    results: dict[str, dict[str, str]] = {}
    for node in nodes:
        record: dict[str, str] = {}
        try:
            fd = os.open(f"/dev/{node}", os.O_RDONLY)
        except OSError as error:
            results[node] = {"error": str(error)}
            continue
        try:
            for label, opcode, nsid, cdw10, length in commands:
                try:
                    payload = nvme_admin(fd, opcode=opcode, nsid=nsid, cdw10=cdw10, data_len=length)
                    record[label] = base64.b64encode(payload).decode("ascii")
                except OSError as error:
                    record[f"{label}_error"] = str(error)
        finally:
            os.close(fd)
        results[node] = record
    return results


# Files whose mere presence names a container runtime.
_CONTAINER_MARKER_FILES: dict[str, str] = {
    "/.dockerenv": "docker",
    "/run/.containerenv": "podman",
}


def read_environment() -> dict[str, Any]:
    """Gather the evidence that says whether this is metal, a guest or a container.

    Read rather than shelled out to, so it works where ``systemd-detect-virt``
    is absent, and it costs nothing.

    Returns:
        The raw strings, for the pure classifier to interpret.
    """
    evidence: dict[str, Any] = {}

    # PID 1's environment carries container=lxc for LXC and systemd-nspawn.
    try:
        entries = Path("/proc/1/environ").read_bytes().split(b"\0")
    except OSError:
        entries = []
    for entry in entries:
        name, _, value = entry.decode("utf-8", errors="replace").partition("=")
        if name == "container":
            evidence["container_marker"] = value
            break

    present = [path for path in _CONTAINER_MARKER_FILES if Path(path).exists()]
    if present:
        evidence["container_files"] = [_CONTAINER_MARKER_FILES[path] for path in present]

    # systemd records the runtime here and leaves it world readable, which is
    # what makes a container detectable without being root.
    runtime = _read_text(Path("/run/systemd/container"))
    if runtime and "container_marker" not in evidence:
        evidence["container_marker"] = runtime

    cgroup = _read_text(Path("/proc/1/cgroup"))
    if cgroup:
        evidence["cgroup"] = cgroup

    markers = container_markers_in_mounts(_read_text(Path("/proc/self/mountinfo")) or "")
    if markers:
        evidence["mount_markers"] = markers

    for key, path in (
        ("dmi_vendor", "/sys/class/dmi/id/sys_vendor"),
        ("dmi_product", "/sys/class/dmi/id/product_name"),
        ("dmi_board_vendor", "/sys/class/dmi/id/board_vendor"),
        ("dmi_board_name", "/sys/class/dmi/id/board_name"),
        ("hypervisor_type", "/sys/hypervisor/type"),
    ):
        value = _read_text(Path(path))
        if value:
            evidence[key] = value

    cpuinfo = _read_text(Path("/proc/cpuinfo")) or ""
    evidence["hypervisor_flag"] = " hypervisor" in cpuinfo or cpuinfo.startswith("hypervisor")
    return evidence


def _devices_accessible(nodes: list[str]) -> bool:
    """Whether the device nodes needed to interrogate a disk exist.

    In most containers ``/sys/block`` lists the host's disks while ``/dev`` has
    no matching nodes at all. Elevating changes nothing there, so the difference
    decides which advice is honest.
    """
    return any(Path(f"/dev/{node}").exists() for node in nodes) if nodes else True


def _resolve_pci_names(devices: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Resolve the names of the PCI devices actually present.

    Recorded alongside the reading so a snapshot renders the same names when it
    is replayed on a machine whose ``pci.ids`` differs, or which has none.
    """
    names: dict[str, str] = {}
    for entry in devices.values():
        vendor_text, device_text = entry.get("vendor"), entry.get("device")
        if not vendor_text or not device_text:
            continue
        vendor, device = int(vendor_text, 16), int(device_text, 16)
        resolved = pciids.lookup_device(vendor, device)
        if resolved:
            vendor_name = pciids.lookup_vendor(vendor)
            names[f"{vendor:04x}:{device:04x}"] = f"{vendor_name} {resolved}" if vendor_name else resolved
    return names


def read_system() -> dict[str, Any]:
    """Read the whole storage subsystem from this machine.

    Returns:
        A JSON-serialisable reading, the same shape a snapshot stores and
        :func:`~lsdsk.adapters.hw.linux.builder.build_inventory` consumes.
    """
    block = read_block()
    ata_nodes = [node for node in block if node.startswith("sd")]
    nvme_nodes = [node for node in block if node.startswith("nvme")]
    pci = read_pci()

    return {
        "schema": SCHEMA_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "platform": Platform.LINUX.value,
        "environment": read_environment(),
        "devices_accessible": _devices_accessible([*ata_nodes, *nvme_nodes]),
        "hostname": os.uname().nodename,
        "kernel": os.uname().release,
        "euid": os.geteuid(),
        "pci": pci,
        "pci_names": _resolve_pci_names(pci),
        "classes": read_classes(),
        "block": block,
        "ata": read_ata_blobs(ata_nodes),
        "nvme": read_nvme_blobs(nvme_nodes),
    }


__all__ = [
    "ATA_IDENTIFY_DEVICE",
    "ATA_SMART",
    "NVME_IOCTL_ADMIN_CMD",
    "SG_IO",
    "NvmePassthruCommand",
    "SgIoHeader",
    "ata_passthrough",
    "nvme_admin",
    "read_ahci_capabilities",
    "read_block",
    "read_classes",
    "read_environment",
    "read_pci",
    "read_system",
    "smart_log_selector",
]
