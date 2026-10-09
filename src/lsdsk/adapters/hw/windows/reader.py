"""Read storage topology and device blobs from a live Windows system.

The Windows counterpart to the Linux reader, and it produces the same shape of
reading so a snapshot from either can be rendered anywhere.

A USB disk's link is read from the hub its device is plugged into: the hub is
opened and asked about that port, and every hub between the disk and the root
is asked about its own port the same way.

Two privilege tiers, as on Linux:
    * unprivileged: the device tree, PCIe link state from the PCI device
      properties, disk identity and bus type from ``IOCTL_STORAGE_QUERY_PROPERTY``,
      capacity, solid-state versus rotating, and often temperature
    * Administrator: ATA SMART through ``IOCTL_ATA_PASS_THROUGH_DIRECT``, or
      wrapped in SAT through ``IOCTL_SCSI_PASS_THROUGH`` for a drive behind a USB
      bridge or SAS adapter, and the NVMe health log through
      ``IOCTL_STORAGE_PROTOCOL_COMMAND``

Every command issued is a read.  Nothing is ever written to a device.

System Role:
    Adapter layer, reading half.  Produces the plain mapping that
    :mod:`.builder` turns into domain objects.
"""

from __future__ import annotations

import base64
import ctypes
import os
import platform
import re
from ctypes import wintypes
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from ....domain.enums import BusType, Platform
from ..ata_commands import (
    ATA_IDENTIFY_DEVICE,
    ATA_SMART,
    SMART_LBA_SIGNATURE,
    SMART_READ_DATA,
    SMART_READ_THRESHOLDS,
    ata_pass_through_16,
)
from ..capture import MAX_DEVICE_TEXT
from ..decode import pciids, usb
from ..snapshot import SCHEMA_VERSION
from . import volumes
from . import winapi as api
from .capture import bus_type_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

# PCI hardware identifiers look like PCI\VEN_8086&DEV_A182&SUBSYS_...&REV_11.
_HARDWARE_ID = re.compile(r"PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", re.IGNORECASE)

_SECTOR_BYTES = 512
_IOCTL_TIMEOUT_SECONDS = 10
_NVME_IDENTIFY_LENGTH = 4096
_NVME_SMART_LOG_LENGTH = 512
_NVME_SMART_LOG_ID = 0x02

# The reason recorded for a read the process could not even attempt.
_NEEDS_ADMINISTRATOR = "needs Administrator to open the device for passthrough"

# A device tree is a handful of levels deep. The bound only stops a malformed
# tree from being walked forever.
_MAX_TREE_DEPTH = 64

# One node's CHILDREN are a different quantity and get their own bound: a single
# PCI bus alone allows 32 devices of 8 functions, so a depth-sized cap here would
# truncate a real machine. This is high enough that nothing real reaches it and
# low enough that a driver returning a sibling cycle stops rather than filling
# memory.
_MAX_SIBLINGS = 4096

# A hub numbers its ports in one byte, and the capture model holds no other.
_MAX_HUB_PORT = 255

# Output buffers for the hub answers that carry a variable tail: the connection
# information lists the device's pipes and the connector properties its
# companion hub's name. Both fit well inside these on every hub measured.
_CONNECTION_ANSWER_BYTES = 512
_CONNECTOR_ANSWER_BYTES = 512
_HUB_INFORMATION_BYTES = 128

# The fixed part of a BOS descriptor, which names the length of the whole.
_BOS_HEADER_BYTES = 5

# The instance-ID segment Windows gives one interface of a composite device.
# Such an interface is not plugged into a port; the composite device above it is.
_COMPOSITE_INTERFACE = "&MI_"


class UsbDeviceFacts(NamedTuple):
    """Where one USB device sits in the device tree, and the driver bound to it.

    Attributes:
        parent: The instance identifier of the device it hangs off: for a
            device in a hub's port, that hub.
        port: The port number on that hub, as the device's address property
            publishes it.
        service: The driver bound to the device, such as ``UASPStor``.
    """

    parent: str | None
    port: int | None
    service: str | None


class UsbReading(NamedTuple):
    """What the hubs said about the ports USB disks hang off.

    Attributes:
        ports: One record per USB device above a disk, keyed by its instance
            identifier, shaped for the capture's ``usb_ports``.
        hubs: One record per hub asked, keyed by its instance identifier,
            shaped for the capture's ``usb_hubs``.
        disk_errors: Why a disk's OWN port could not be asked, keyed by the
            disk's interface path.
    """

    ports: dict[str, dict[str, Any]]
    hubs: dict[str, dict[str, Any]]
    disk_errors: dict[str, str]


def is_elevated() -> bool:
    """Whether this process can issue passthrough commands.

    Returns:
        ``True`` when running as Administrator.
    """
    try:
        return bool(api.load_library("shell32").IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


class _DeviceTree:
    """Reads the Windows device tree through SetupAPI and cfgmgr32."""

    def __init__(
        self,
        *,
        setupapi: api.WinLibrary | None = None,
        cfgmgr32: api.WinLibrary | None = None,
        kernel32: api.WinLibrary | None = None,
    ) -> None:
        """Bind the DLL entry points this class uses.

        Each library defaults to the real DLL, loaded through
        :func:`api.load_libraries`. A caller that is not on Windows - a test -
        supplies its own fakes here instead, which is the seam
        ``tests/test_windows_device_tree.py`` drives the class through.

        Args:
            setupapi: The device-enumeration library, or ``None`` to load it.
            cfgmgr32: The configuration-manager library, or ``None`` to load it.
            kernel32: The core library, or ``None`` to load it.
        """
        if setupapi is None or cfgmgr32 is None or kernel32 is None:
            loaded = api.load_libraries()
            setupapi = loaded.setupapi if setupapi is None else setupapi
            cfgmgr32 = loaded.cfgmgr32 if cfgmgr32 is None else cfgmgr32
            kernel32 = loaded.kernel32 if kernel32 is None else kernel32
        self.setupapi = setupapi
        self.cfgmgr32 = cfgmgr32
        self.kernel32 = kernel32
        # Why a DISK interface could not be turned into a path, one text per
        # interface that was dropped; refilled by every :meth:`disk_interfaces`.
        # Only disks are recorded: a hub that cannot be listed is not a disk the
        # machine has, and the USB pass says so for itself.
        self.unreadable_interfaces: list[str] = []

    def enumerate_pci(self) -> dict[str, dict[str, Any]]:
        """Read every present PCI device with its properties."""
        return {
            instance: self._pci_entry(handle, info, instance) for handle, info, instance in self._present_devices("PCI")
        }

    def usb_devices(self) -> dict[str, UsbDeviceFacts]:
        """Read where every present USB device is plugged in, root hubs included."""
        return {
            instance: UsbDeviceFacts(
                parent=self._parent_instance(info.DevInst),
                port=self._uint_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS),
                service=self._string_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_SERVICE),
            )
            for handle, info, instance in self._present_devices("USB")
        }

    def usb_hubs(self) -> dict[str, str]:
        """Return the interface path of every present USB hub, keyed by the hub's instance identifier."""
        found: dict[str, str] = {}
        for path, devinst in self._interfaces(api.GUID_DEVINTERFACE_USB_HUB):
            instance = None if devinst is None else self._instance_id(devinst)
            if instance is not None:
                found[instance] = path
        return found

    def _present_devices(self, enumerator: str) -> Iterator[tuple[int, api.SP_DEVINFO_DATA, str]]:
        """Yield every present device one enumerator created, with the handle and record to read it by.

        The record is one buffer the enumeration refills on every turn, so a
        caller reads what it needs from it before asking for the next device.
        """
        handle = self.setupapi.SetupDiGetClassDevsW(None, enumerator, None, api.DIGCF_PRESENT | api.DIGCF_ALLCLASSES)
        if handle == api.INVALID_HANDLE_VALUE:
            return
        try:
            info = api.SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(api.SP_DEVINFO_DATA)
            index = 0
            while self.setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                index += 1
                instance = self._instance_id(info.DevInst)
                if instance is not None:
                    yield handle, info, instance
        finally:
            self.setupapi.SetupDiDestroyDeviceInfoList(handle)

    def _pci_entry(self, handle: int, info: api.SP_DEVINFO_DATA, instance: str) -> dict[str, Any]:
        """Collect one PCI device's identity, link state and parentage."""
        entry: dict[str, Any] = {"instance_id": instance}
        match = _HARDWARE_ID.search(instance)
        if match:
            entry["vendor"] = f"0x{int(match.group(1), 16):04x}"
            entry["device"] = f"0x{int(match.group(2), 16):04x}"

        name = self._string_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_FRIENDLYNAME)
        if not name:
            name = self._string_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_DEVICEDESC)
        if name:
            entry["name"] = name

        driver = self._string_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_SERVICE)
        if driver:
            entry["driver"] = driver

        base = self._uint_property(handle, info, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_BASE_CLASS)
        sub = self._uint_property(handle, info, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_SUB_CLASS)
        prog = self._uint_property(handle, info, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_PROG_IF)
        if base is not None and sub is not None:
            entry["class"] = f"0x{base:02x}{sub:02x}{prog or 0:02x}"

        for key, prop in (
            ("current_link_speed", api.PCI_PROP_CURRENT_LINK_SPEED),
            ("max_link_speed", api.PCI_PROP_MAX_LINK_SPEED),
        ):
            encoded = self._uint_property(handle, info, api.PCI_DEVICE_PROPERTY_FMTID, prop)
            gtps = api.LINK_SPEED_GTPS.get(encoded or 0)
            if gtps is not None:
                entry[key] = f"{gtps} GT/s PCIe"
        for key, prop in (
            ("current_link_width", api.PCI_PROP_CURRENT_LINK_WIDTH),
            ("max_link_width", api.PCI_PROP_MAX_LINK_WIDTH),
        ):
            width = self._uint_property(handle, info, api.PCI_DEVICE_PROPERTY_FMTID, prop)
            # A width of zero is a reading, not a missing reading: it is a link
            # that failed to train, which the severity rules call critical. On a
            # truthiness test it reads as absent, current_width stays None, and
            # PcieLink.is_dead compares None == 0 and answers False, so the one
            # finding that matters most can never be raised on Windows. Linux
            # escapes this only because sysfs hands back the string "0".
            if width is not None:
                entry[key] = str(width)

        slot_number = self._uint_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_UINUMBER)
        if slot_number is not None:
            entry["slot_number"] = slot_number

        entry["address"] = self._pci_address(handle, info)
        parent = self._parent_instance(info.DevInst)
        if parent:
            entry["parent"] = parent
        children = self._child_instances(info.DevInst)
        if children:
            entry["children"] = children
        return entry

    def _pci_address(self, handle: int, info: api.SP_DEVINFO_DATA) -> str:
        """Build the familiar bus:device.function address for a PCI device.

        Windows identifies devices by instance string, which is far too long to
        show in a listing. The bus number and the packed device address are both
        plain integers, so the conventional address can be rebuilt from them
        without parsing the localised location sentence.

        The leading ``0000`` is the PCI SEGMENT and is not read - nothing here
        asks Windows for one. On a single-segment machine, which is every
        machine this has been run on, it is right. On a multi-segment one it is
        a guess that makes two different devices share one address string, and
        an address is what the fabric keys devices by, so they would merge. The
        Linux side does read the domain, and both tree builders accept four
        digits OR MORE because an Intel VMD re-enumerates its drives into
        0x10000 - so the width is not the problem here, the missing reading is.

        Left as it is because no multi-segment Windows machine is available to
        anyone working on this, and both alternatives are worse UNVERIFIED:
        dropping the field changes the address format every committed Windows
        fixture and every Windows reader of this tool already uses, and reading
        a segment property that cannot be tested would ship an untried code
        path to exactly the users who cannot report what it did.
        """
        bus = self._uint_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_BUSNUMBER)
        address = self._uint_property(handle, info, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS)
        if bus is None or address is None:
            return ""
        return f"0000:{bus:02x}:{(address >> 16) & 0xFFFF:02x}.{address & 0xFFFF}"

    def _instance_id(self, devinst: int) -> str | None:
        """Return one device's instance identifier."""
        buffer = ctypes.create_unicode_buffer(512)
        if self.cfgmgr32.CM_Get_Device_IDW(devinst, buffer, 512, 0) != 0:
            return None
        return buffer.value

    def _parent_instance(self, devinst: int) -> str | None:
        """Return the instance identifier of a device's parent."""
        parent = wintypes.DWORD()
        if self.cfgmgr32.CM_Get_Parent(ctypes.byref(parent), devinst, 0) != 0:
            return None
        return self._instance_id(parent.value)

    def _ancestor_instances(self, devinst: int) -> list[str]:
        """Return the instance identifiers above a device, nearest first, up to the root.

        The whole chain is recorded rather than the parent alone, because the
        controller a disk hangs off is often not its parent: behind a USB bridge
        the parent is the mass-storage device and the host controller sits two
        levels higher. Which ancestor is the controller is the builder's decision,
        so this records the tree and decides nothing.
        """
        found: list[str] = []
        current = devinst
        for _ in range(_MAX_TREE_DEPTH):
            parent = wintypes.DWORD()
            if self.cfgmgr32.CM_Get_Parent(ctypes.byref(parent), current, 0) != 0:
                return found
            instance = self._instance_id(parent.value)
            if instance is None:
                return found
            found.append(instance)
            current = parent.value
        return found

    def _child_instances(self, devinst: int) -> list[str]:
        """Return the instance identifiers of a device's children."""
        child = wintypes.DWORD()
        if self.cfgmgr32.CM_Get_Child(ctypes.byref(child), devinst, 0) != 0:
            return []
        found: list[str] = []
        for _ in range(_MAX_SIBLINGS):
            instance = self._instance_id(child.value)
            if instance:
                found.append(instance)
            sibling = wintypes.DWORD()
            if self.cfgmgr32.CM_Get_Sibling(ctypes.byref(sibling), child.value, 0) != 0:
                return found
            child = sibling
        return found

    def _property(
        self,
        handle: int,
        info: api.SP_DEVINFO_DATA,
        fmtid: str,
        pid: int,
    ) -> tuple[int, bytes] | None:
        """Read one device property, returning its type and raw bytes."""
        key = api.make_property_key(fmtid, pid)
        prop_type = wintypes.DWORD()
        required = wintypes.DWORD()
        self.setupapi.SetupDiGetDevicePropertyW(
            handle,
            ctypes.byref(info),
            ctypes.byref(key),
            ctypes.byref(prop_type),
            None,
            0,
            ctypes.byref(required),
            0,
        )
        if required.value == 0:
            return None
        buffer = ctypes.create_string_buffer(required.value)
        ok = self.setupapi.SetupDiGetDevicePropertyW(
            handle,
            ctypes.byref(info),
            ctypes.byref(key),
            ctypes.byref(prop_type),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            required.value,
            ctypes.byref(required),
            0,
        )
        if not ok:
            return None
        return prop_type.value, buffer.raw[: required.value]

    def _uint_property(self, handle: int, info: api.SP_DEVINFO_DATA, fmtid: str, pid: int) -> int | None:
        """Read one 32-bit device property."""
        result = self._property(handle, info, fmtid, pid)
        if result is None:
            return None
        prop_type, raw = result
        if prop_type != api.DEVPROP_TYPE_UINT32 or len(raw) < 4:  # noqa: PLR2004 - the width of a UINT32
            return None
        return int.from_bytes(raw[:4], "little")

    def _string_property(self, handle: int, info: api.SP_DEVINFO_DATA, fmtid: str, pid: int) -> str | None:
        """Read one string device property, or ``None`` past the capture's bound.

        A driver chooses the length, so one past :data:`MAX_DEVICE_TEXT` is not
        read rather than stored: stored, the capture model would refuse the
        whole scan over it.
        """
        result = self._property(handle, info, fmtid, pid)
        if result is None:
            return None
        prop_type, raw = result
        if prop_type != api.DEVPROP_TYPE_STRING:
            return None
        text = raw.decode("utf-16-le", errors="replace").rstrip("\x00").strip()
        return text if 0 < len(text) <= MAX_DEVICE_TEXT else None

    def disk_interfaces(self) -> list[tuple[str, list[str]]]:
        """Return every disk's interface path and the instances above it, nearest first."""
        self.unreadable_interfaces = []
        return [
            (path, [] if devinst is None else self._ancestor_instances(devinst))
            for path, devinst in self._interfaces(api.GUID_DEVINTERFACE_DISK, unreadable=self.unreadable_interfaces)
        ]

    def _interfaces(self, interface_class: str, *, unreadable: list[str] | None = None) -> list[tuple[str, int | None]]:
        """Return the path and device instance of every present interface of one class.

        Args:
            interface_class: The interface class GUID, as text.
            unreadable: Where to record why an interface was dropped, or ``None``
                to drop it without a record.
        """
        guid = api.parse_guid(interface_class)
        handle = self.setupapi.SetupDiGetClassDevsW(
            ctypes.byref(guid), None, None, api.DIGCF_PRESENT | api.DIGCF_DEVICEINTERFACE
        )
        if handle == api.INVALID_HANDLE_VALUE:
            return []
        found: list[tuple[str, int | None]] = []
        try:
            interface = api.SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(api.SP_DEVICE_INTERFACE_DATA)
            index = 0
            while self.setupapi.SetupDiEnumDeviceInterfaces(
                handle, None, ctypes.byref(guid), index, ctypes.byref(interface)
            ):
                index += 1
                path, devinst, error = self._interface_detail(handle, interface)
                if path:
                    found.append((path, devinst))
                elif error is not None and unreadable is not None:
                    unreadable.append(error)
            return found
        finally:
            self.setupapi.SetupDiDestroyDeviceInfoList(handle)

    def _interface_detail(
        self, handle: int, interface: api.SP_DEVICE_INTERFACE_DATA
    ) -> tuple[str | None, int | None, str | None]:
        """Return one interface's device path, its device instance and why neither was had.

        A path is the only handle the reader has on a disk, so an interface whose
        path could not be read cannot be opened or asked anything. Dropping it
        silently would show a machine with one disk fewer than it has, which is
        the same picture as a machine that has that many; the reason is returned
        so the caller can say a disk went unread.
        """
        required = wintypes.DWORD()
        self.setupapi.SetupDiGetDeviceInterfaceDetailW(
            handle, ctypes.byref(interface), None, 0, ctypes.byref(required), None
        )
        if required.value == 0:
            return None, None, f"Win32 error {api.last_error()}"
        buffer = ctypes.create_string_buffer(required.value)
        # SP_DEVICE_INTERFACE_DETAIL_DATA_W begins with its own size, and that
        # size is the BUILD's rather than a constant - see the constant's own
        # note. Hardcoded to the 64-bit figure, a 32-bit Python returned NO
        # disks at all: SetupDiGetDeviceInterfaceDetailW refuses the call
        # outright rather than answering wrongly, so it would have read as a
        # machine with no storage rather than as an error.
        ctypes.memmove(buffer, ctypes.byref(wintypes.DWORD(api.SP_DEVICE_INTERFACE_DETAIL_DATA_W_CBSIZE)), 4)
        info = api.SP_DEVINFO_DATA()
        info.cbSize = ctypes.sizeof(api.SP_DEVINFO_DATA)
        ok = self.setupapi.SetupDiGetDeviceInterfaceDetailW(
            handle,
            ctypes.byref(interface),
            buffer,
            required.value,
            ctypes.byref(required),
            ctypes.byref(info),
        )
        if not ok:
            return None, None, f"Win32 error {api.last_error()}"
        path = ctypes.wstring_at(ctypes.addressof(buffer) + 4)
        return path, info.DevInst, None


def _open_device(kernel32: api.WinLibrary, path: str) -> tuple[int | None, bool]:
    """Open a device, preferring the access level that allows passthrough.

    ``IOCTL_ATA_PASS_THROUGH_DIRECT`` is defined with both read and write
    access, so a handle opened read-only fails it with ACCESS_DENIED however
    elevated the process is. ``IOCTL_STORAGE_QUERY_PROPERTY`` needs no access
    rights at all, so falling back to a zero-access handle still yields identity,
    capacity and bus type for an ordinary user.

    Returns:
        The handle, and whether it can carry passthrough commands.
    """
    share = api.FILE_SHARE_READ | api.FILE_SHARE_WRITE
    for access, passthrough in ((api.GENERIC_READ | api.GENERIC_WRITE, True), (0, False)):
        handle = kernel32.CreateFileW(path, access, share, None, api.OPEN_EXISTING, 0, None)
        if handle != api.INVALID_HANDLE_VALUE:
            return handle, passthrough
    return None, False


def _device_control_out(
    kernel32: api.WinLibrary, handle: int, code: int, response: ctypes.c_longlong, size: int
) -> int:
    """Issue one output-only DeviceIoControl call and return the bytes returned."""
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        code,
        None,
        0,
        ctypes.byref(response),
        size,
        ctypes.byref(returned),
        None,
    )
    return returned.value if ok else 0


def query_property(kernel32: api.WinLibrary, handle: int, property_id: int, size: int = 1024) -> bytes:
    """Run IOCTL_STORAGE_QUERY_PROPERTY and return the raw response.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: An open handle to the device.
        property_id: Which storage property to ask for.
        size: How large a buffer to offer the driver.

    Returns:
        The bytes the driver wrote, for a decoder to read.
    """
    request = api.STORAGE_PROPERTY_QUERY()
    request.PropertyId = property_id
    request.QueryType = api.PROPERTY_STANDARD_QUERY
    response = ctypes.create_string_buffer(size)
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_STORAGE_QUERY_PROPERTY,
        ctypes.byref(request),
        ctypes.sizeof(request),
        response,
        size,
        ctypes.byref(returned),
        None,
    )
    return response.raw[: returned.value] if ok else b""


def _descriptor_strings(raw: bytes) -> dict[str, str]:
    """Pull the identity strings out of a STORAGE_DEVICE_DESCRIPTOR."""
    if len(raw) < ctypes.sizeof(api.STORAGE_DEVICE_DESCRIPTOR):
        return {}
    descriptor = api.STORAGE_DEVICE_DESCRIPTOR.from_buffer_copy(raw)

    def text_at(offset: int) -> str:
        if not offset or offset >= len(raw):
            return ""
        end = raw.find(b"\x00", offset)
        return raw[offset : end if end != -1 else len(raw)].decode("ascii", errors="replace").strip()

    values = {
        "vendor": text_at(descriptor.VendorIdOffset),
        "model": text_at(descriptor.ProductIdOffset),
        "rev": text_at(descriptor.ProductRevisionOffset),
        "serial": text_at(descriptor.SerialNumberOffset),
        "bus_type": api.BUS_TYPE_NAMES.get(descriptor.BusType, "unknown"),
    }
    return {key: value for key, value in values.items() if value}


def _shortfall(moved: int) -> str | None:
    """Say why a transfer that moved less than a whole sector is not a reading.

    Both passthrough requests carry ``DataTransferLength`` in and out: the kernel
    overwrites it with the bytes the device actually moved. A command can end
    with a success status having moved nothing - a translator answering GOOD to a
    command it did not carry out - and the buffer then still holds the zeros it
    was allocated with, which decode as a drive with no identity and no counters.

    Args:
        moved: The ``DataTransferLength`` the kernel returned.

    Returns:
        ``None`` for a whole sector, otherwise the refusal text naming the count.

    Example:
        >>> _shortfall(512) is None
        True
        >>> _shortfall(0)
        '0 of 512 bytes returned'
    """
    if moved == _SECTOR_BYTES:
        return None
    return f"{moved} of {_SECTOR_BYTES} bytes returned"


def ata_passthrough(
    kernel32: api.WinLibrary, handle: int, *, command: int, feature: int = 0, lba: int = 0
) -> tuple[bytes, str | None]:
    """Issue one read-only ATA command through the Windows passthrough ioctl.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: An open handle to the device.
        command: The ATA command code.
        feature: The feature register, where the command takes one.
        lba: The LBA register, where the command takes one.

    Returns:
        The sector the drive returned and ``None``, or empty bytes and why it
        was refused: the Win32 error when the ioctl failed, which matters because
        a driver that does not implement passthrough at all and a request this
        code built wrongly both return nothing and only the error tells them
        apart, or the byte count when the call succeeded without moving a sector.
    """
    buffer = ctypes.create_string_buffer(_SECTOR_BYTES)
    request = api.ATA_PASS_THROUGH_DIRECT()
    request.Length = ctypes.sizeof(api.ATA_PASS_THROUGH_DIRECT)
    request.AtaFlags = api.ATA_FLAGS_DATA_IN | api.ATA_FLAGS_DRDY_REQUIRED
    request.DataTransferLength = _SECTOR_BYTES
    request.TimeOutValue = _IOCTL_TIMEOUT_SECONDS
    request.DataBuffer = ctypes.cast(buffer, ctypes.c_void_p)
    # The task file mirrors the ATA registers: features, sector count, then the
    # three LBA bytes, the device register and the command.
    task = (ctypes.c_ubyte * 8)()
    task[0] = feature & 0xFF
    task[1] = 1
    task[2] = lba & 0xFF
    task[3] = (lba >> 8) & 0xFF
    task[4] = (lba >> 16) & 0xFF
    # The drive/head register. Some storage drivers reject a passthrough whose
    # device register is left at zero, so the legacy master value is used.
    task[5] = 0xA0
    task[6] = command
    request.CurrentTaskFile = task

    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_ATA_PASS_THROUGH_DIRECT,
        ctypes.byref(request),
        ctypes.sizeof(request),
        ctypes.byref(request),
        ctypes.sizeof(request),
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return b"", f"Win32 error {api.last_error()}"
    shortfall = _shortfall(request.DataTransferLength)
    if shortfall:
        return b"", shortfall
    return bytes(buffer), None


#: How much sense data a refused SAT command may return.
_SENSE_BYTES = 32


class _SatRequest(ctypes.Structure):
    """One SCSI passthrough request with its sense and data buffers after it.

    The buffered form of ``IOCTL_SCSI_PASS_THROUGH`` names its buffers by their
    offset into this block, so they travel in it rather than as pointers, and
    no alignment the adapter demands of a direct buffer applies.
    """

    _fields_ = (
        ("request", api.SCSI_PASS_THROUGH),
        ("filler", api.ULONG),
        ("sense", ctypes.c_ubyte * _SENSE_BYTES),
        ("data", ctypes.c_ubyte * _SECTOR_BYTES),
    )


def sat_passthrough(
    kernel32: api.WinLibrary, handle: int, *, command: int, feature: int = 0, lba: int = 0
) -> tuple[bytes, str | None]:
    """Issue one read-only ATA command wrapped in SAT, for a drive the ATA ioctl cannot reach.

    Behind a USB bridge or a SAS adapter a SATA drive is a SCSI device, and the
    storage stack refuses ``IOCTL_ATA_PASS_THROUGH`` for it (Win32 error 1). The
    same ATA command wrapped in the SCSI ATA PASS-THROUGH command reaches it
    through the bridge's own translator - measured on a USB SATA SSD that
    refused every ATA passthrough and answered IDENTIFY and SMART READ DATA this
    way.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: A handle opened for passthrough.
        command: The ATA command code.
        feature: The features register.
        lba: The LBA registers, which SMART uses as a signature.

    Returns:
        The sector the drive returned and ``None``, or empty bytes and why it
        was refused: the Win32 error when the ioctl failed, the SCSI status
        when the translator rejected the command, the byte count when it
        accepted the command without moving a sector.
    """
    block = ata_pass_through_16(command=command, feature=feature, lba=lba)
    request = _SatRequest()
    request.request.Length = ctypes.sizeof(api.SCSI_PASS_THROUGH)
    request.request.CdbLength = len(block)
    request.request.SenseInfoLength = _SENSE_BYTES
    request.request.DataIn = api.SCSI_IOCTL_DATA_IN
    request.request.DataTransferLength = _SECTOR_BYTES
    request.request.TimeOutValue = _IOCTL_TIMEOUT_SECONDS
    request.request.DataBufferOffset = _SatRequest.data.offset
    request.request.SenseInfoOffset = _SatRequest.sense.offset
    ctypes.memmove(request.request.Cdb, block, len(block))

    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_SCSI_PASS_THROUGH,
        ctypes.byref(request),
        ctypes.sizeof(request),
        ctypes.byref(request),
        ctypes.sizeof(request),
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return b"", f"Win32 error {api.last_error()}"
    if request.request.ScsiStatus:
        return b"", f"SCSI status 0x{request.request.ScsiStatus:02X}"
    shortfall = _shortfall(request.request.DataTransferLength)
    if shortfall:
        return b"", shortfall
    return bytes(request.data), None


def nvme_protocol_data(
    kernel32: api.WinLibrary, handle: int, *, data_type: int, request_value: int, length: int
) -> tuple[bytes, str | None]:
    """Fetch an NVMe identify structure or log page through the storage stack.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: An open handle to the device.
        data_type: Which NVMe protocol structure to ask for.
        request_value: Which identify page or log page within that type.
        length: How many bytes the structure occupies.

    Returns:
        The raw structure for the shared NVMe decoder and ``None``, or empty
        bytes and why it is not a reading: the Win32 error when the storage
        stack refused the query, the byte count when it accepted the query
        without moving the whole structure.
    """
    header = ctypes.sizeof(api.STORAGE_PROPERTY_QUERY) + ctypes.sizeof(api.STORAGE_PROTOCOL_SPECIFIC_DATA)
    total = header + length
    buffer = ctypes.create_string_buffer(total)

    query = ctypes.cast(buffer, ctypes.POINTER(api.STORAGE_PROPERTY_QUERY)).contents
    query.PropertyId = api.STORAGE_DEVICE_PROTOCOL_SPECIFIC_PROPERTY
    query.QueryType = api.PROPERTY_STANDARD_QUERY

    # The overlay goes where AdditionalParameters begins, which is where the
    # storage driver reads it. That is 8, not 11: the structure is two DWORDs
    # plus one byte, so sizeof() pads it to 12 and "sizeof minus the byte" lands
    # three bytes late. Measured against a Samsung 9100 PRO: at 11 every request
    # is rejected with ERROR_INVALID_PARAMETER, at 8 it returns the identify page.
    offset = api.STORAGE_PROPERTY_QUERY.AdditionalParameters.offset
    protocol = ctypes.cast(
        ctypes.addressof(buffer) + offset, ctypes.POINTER(api.STORAGE_PROTOCOL_SPECIFIC_DATA)
    ).contents
    protocol.ProtocolType = api.PROTOCOL_TYPE_NVME
    protocol.DataType = data_type
    protocol.ProtocolDataRequestValue = request_value
    protocol.ProtocolDataRequestSubValue = 0
    protocol.ProtocolDataOffset = ctypes.sizeof(api.STORAGE_PROTOCOL_SPECIFIC_DATA)
    protocol.ProtocolDataLength = length

    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_STORAGE_QUERY_PROPERTY,
        buffer,
        total,
        buffer,
        total,
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return b"", f"Win32 error {api.last_error()}"
    start = offset + protocol.ProtocolDataOffset
    # A success that moved nothing leaves the zeros the buffer was allocated
    # with, which decode as a healthy drive. The driver rewrites the length
    # field to what it moved and the call reports how far into the buffer it
    # wrote; the smaller of the two is what can be believed.
    moved = min(protocol.ProtocolDataLength, max(returned.value - start, 0), length)
    if moved < length:
        return b"", f"{moved} of {length} bytes returned"
    return buffer.raw[start : start + length], None


def _disk_length(kernel32: api.WinLibrary, handle: int) -> int | None:
    """Return a disk's capacity in bytes."""
    response = ctypes.c_longlong()
    returned = _device_control_out(kernel32, handle, api.IOCTL_DISK_GET_LENGTH_INFO, response, 8)
    return response.value if returned else None


def _device_number(kernel32: api.WinLibrary, handle: int) -> int | None:
    """Return the PhysicalDrive number Windows assigned to a disk."""
    response = api.STORAGE_DEVICE_NUMBER()
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        api.IOCTL_STORAGE_GET_DEVICE_NUMBER,
        None,
        0,
        ctypes.byref(response),
        ctypes.sizeof(response),
        ctypes.byref(returned),
        None,
    )
    return response.DeviceNumber if ok else None


def _seek_penalty(kernel32: api.WinLibrary, handle: int) -> bool | None:
    """Whether the device has a seek penalty, which means rotating media."""
    raw = query_property(kernel32, handle, api.STORAGE_DEVICE_SEEK_PENALTY_PROPERTY, 64)
    if len(raw) < ctypes.sizeof(api.DEVICE_SEEK_PENALTY_DESCRIPTOR):
        return None
    return bool(api.DEVICE_SEEK_PENALTY_DESCRIPTOR.from_buffer_copy(raw).IncursSeekPenalty)


def _temperature(kernel32: api.WinLibrary, handle: int) -> dict[str, int]:
    """Read the device's temperature and its own thresholds, when offered.

    The structure ends in an array of sensors, so a driver with one sensor
    sends the 24-byte header plus one 16-byte sensor: 40 bytes, the size of
    the structure as declared. The answer is judged at the size the driver
    returned, so a reply too short for its first sensor is no reading.
    """
    raw = query_property(kernel32, handle, api.STORAGE_DEVICE_TEMPERATURE_PROPERTY, 512)
    header = api.STORAGE_TEMPERATURE_DATA_DESCRIPTOR.TemperatureInfo.offset
    if len(raw) < header:
        return {}
    descriptor = api.STORAGE_TEMPERATURE_DATA_DESCRIPTOR.from_buffer_copy(
        raw.ljust(ctypes.sizeof(api.STORAGE_TEMPERATURE_DATA_DESCRIPTOR), b"\x00")
    )
    if not descriptor.InfoCount or len(raw) < header + ctypes.sizeof(api.STORAGE_TEMPERATURE_INFO):
        return {}
    values: dict[str, int] = {"temperature_c": descriptor.TemperatureInfo[0].Temperature}
    if descriptor.WarningTemperature:
        values["warning_c"] = descriptor.WarningTemperature
    if descriptor.CriticalTemperature:
        values["critical_c"] = descriptor.CriticalTemperature
    return values


def read_disk(kernel32: api.WinLibrary, path: str, ancestors: list[str]) -> dict[str, Any]:
    """Read one disk: identity, geometry, health blobs and the devices above it.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        path: The device path to open.
        ancestors: The device instance ids between this disk and the root.

    Returns:
        One disk's reading, shaped for the capture model to type.
    """
    # The parent is kept beside the ancestry, because an older lsdsk replaying
    # this capture reads only the parent.
    entry: dict[str, Any] = {"path": path, "parent": ancestors[0] if ancestors else None, "ancestors": ancestors}
    handle, passthrough = _open_device(kernel32, path)
    if handle is None:
        entry["error"] = "could not open the device"
        return entry
    entry["passthrough"] = passthrough
    try:
        number = _device_number(kernel32, handle)
        if number is not None:
            entry["node"] = f"PhysicalDrive{number}"
        entry["device"] = _descriptor_strings(query_property(kernel32, handle, api.STORAGE_DEVICE_PROPERTY))
        length = _disk_length(kernel32, handle)
        if length is not None:
            entry["size_bytes"] = length
        penalty = _seek_penalty(kernel32, handle)
        if penalty is not None:
            entry["rotating"] = penalty
        temperature = _temperature(kernel32, handle)
        if temperature:
            entry["temperature"] = temperature

        # The same boundary conversion `StorageDescriptor.bus_type` validates
        # through on replay, so this decision and a replayed one can never read
        # a transport name two different ways.
        bus = bus_type_of(entry["device"].get("bus_type", ""))
        if bus is BusType.NVME:
            entry["nvme"] = _read_nvme(kernel32, handle, passthrough=passthrough)
        elif passthrough:
            entry["ata"] = read_ata(kernel32, handle)
        else:
            entry["ata"] = {"identify_error": _NEEDS_ADMINISTRATOR}
    finally:
        kernel32.CloseHandle(handle)
    return entry


def _read_nvme(kernel32: api.WinLibrary, handle: int, *, passthrough: bool) -> dict[str, str]:
    """Read the NVMe identify structure and health log.

    Each structure is recorded under its label, or the reason it was refused
    under ``<label>_error``, as :func:`read_ata` does: a refusal that left no
    trace would read as a drive with nothing to report.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: An open handle to the device.
        passthrough: Whether the handle carries write access. Without it a
            refusal is named for what the person can change, not for the
            Win32 code.

    Returns:
        The reading, shaped for the capture model to type.
    """
    record: dict[str, str] = {}
    for label, error_label, data_type, request_value, length in (
        ("identify_controller", "identify_controller_error", api.NVME_DATA_TYPE_IDENTIFY, 1, _NVME_IDENTIFY_LENGTH),
        ("smart_log", "smart_log_error", api.NVME_DATA_TYPE_LOG_PAGE, _NVME_SMART_LOG_ID, _NVME_SMART_LOG_LENGTH),
    ):
        payload, refusal = nvme_protocol_data(
            kernel32, handle, data_type=data_type, request_value=request_value, length=length
        )
        if payload:
            record[label] = base64.b64encode(payload).decode("ascii")
        else:
            record[error_label] = _refusal_text(refusal, passthrough=passthrough)
    return record


def _refusal_text(refusal: str | None, *, passthrough: bool) -> str:
    """Say why an NVMe page was not read.

    A Win32 error from a handle opened without write access is most likely the
    missing privilege, so it is named for what the person can change. A short
    answer is a different fact and keeps its own count.

    Args:
        refusal: What the query reported, if anything.
        passthrough: Whether the handle carries write access.

    Returns:
        The text recorded under the page's ``_error`` key.
    """
    if refusal is None:
        return "no data returned"
    if not passthrough and refusal.startswith("Win32 error"):
        return _NEEDS_ADMINISTRATOR
    return refusal


def read_ata(kernel32: api.WinLibrary, handle: int) -> dict[str, str]:
    """Read the ATA identity and SMART structures.

    The ATA ioctl is asked first, because it is how the storage stack reaches a
    drive on an ATA transport. Where it refuses, the same command goes through
    SAT, which is how a drive behind a USB bridge or a SAS adapter is reached;
    a reading refused both ways records both reasons.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        handle: A handle opened for passthrough.

    Returns:
        Each structure base64-encoded under its label, or the refusal under
        ``<label>_error``, shaped for the capture model to type.
    """
    record: dict[str, str] = {}
    for label, command, feature in (
        ("identify", ATA_IDENTIFY_DEVICE, 0),
        ("smart_data", ATA_SMART, SMART_READ_DATA),
        ("smart_thresholds", ATA_SMART, SMART_READ_THRESHOLDS),
    ):
        lba = SMART_LBA_SIGNATURE if command == ATA_SMART else 0
        payload, ata_refusal = ata_passthrough(kernel32, handle, command=command, feature=feature, lba=lba)
        refused = f"passthrough refused, {ata_refusal}"
        if not payload:
            payload, sat_refusal = sat_passthrough(kernel32, handle, command=command, feature=feature, lba=lba)
            refused = f"{refused}; SAT refused, {sat_refusal}"
        if payload:
            record[label] = base64.b64encode(payload).decode("ascii")
        else:
            record[f"{label}_error"] = refused
    return record


def _hub_request(
    kernel32: api.WinLibrary, handle: int, code: int, request: bytes, answer_bytes: int = 0
) -> bytes | None:
    """Issue one hub ioctl and return as many bytes as the hub wrote, or ``None`` when it refused.

    The request and the answer share one buffer, as the hub ioctls define
    them, sized for whichever is larger. The input length is the request's
    own, because the hub refuses one a byte short or long.
    """
    buffer = ctypes.create_string_buffer(request, max(len(request), answer_bytes))
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(handle, code, buffer, len(request), buffer, len(buffer), ctypes.byref(returned), None)
    return buffer.raw[: returned.value] if ok else None


def _encoded(answer: bytes | None) -> str | None:
    """Base64 for a capture, with an empty answer recorded as no answer."""
    return base64.b64encode(answer).decode("ascii") if answer else None


def _read_bos(kernel32: api.WinLibrary, handle: int, port: int) -> bytes | None:
    """Ask the device in a port for its BOS: its header first, which names the whole length."""
    ask = usb.bos_request(port, _BOS_HEADER_BYTES)
    head = _hub_request(
        kernel32, handle, api.IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION, ask, len(ask) + _BOS_HEADER_BYTES
    )
    header = usb.descriptor_payload(head or b"")
    if len(header) < _BOS_HEADER_BYTES:
        return None
    total = int.from_bytes(header[2:4], "little")
    request = usb.bos_request(port, total)
    answer = _hub_request(
        kernel32, handle, api.IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION, request, len(request) + total
    )
    return None if answer is None else usb.descriptor_payload(answer)


def _ask_port(kernel32: api.WinLibrary, handle: int, port: int) -> dict[str, str | None]:
    """Ask a hub every question about one port, or say why it would not answer the first.

    The first question is the one every hub answers for a connected port, so a
    refusal there means the port could not be read at all. The others are
    answers either way: a port not running SuperSpeedPlus refuses that request,
    and a device with no BOS STALLs the request for it.
    """
    connection = _hub_request(
        kernel32,
        handle,
        api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX,
        usb.connection_request(port),
        _CONNECTION_ANSWER_BYTES,
    )
    if connection is None:
        return {"error": f"Win32 error {api.last_error()}"}
    if not connection:
        return {"error": "the hub returned no connection information"}
    return {
        "connection": _encoded(connection),
        "connection_v2": _encoded(
            _hub_request(
                kernel32, handle, api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2, usb.connection_v2_request(port)
            )
        ),
        "connector": _encoded(
            _hub_request(
                kernel32,
                handle,
                api.IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES,
                usb.connector_request(port),
                _CONNECTOR_ANSWER_BYTES,
            )
        ),
        "superspeedplus": _encoded(
            _hub_request(
                kernel32,
                handle,
                api.IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION,
                usb.superspeedplus_request(port),
            )
        ),
        "bos": _encoded(_read_bos(kernel32, handle, port)),
    }


_UNANSWERED_PORT: dict[str, str | None] = dict.fromkeys(
    ("connection", "connection_v2", "connector", "superspeedplus", "bos", "error")
)


class _HubPorts:
    """One run's hub handles and port records, so a hub several disks share is opened and asked once."""

    def __init__(
        self, kernel32: api.WinLibrary, devices: Mapping[str, UsbDeviceFacts], hubs: Mapping[str, str]
    ) -> None:
        """Ask through ``kernel32``, placing devices by ``devices`` and opening hubs by ``hubs``."""
        self.kernel32 = kernel32
        self.devices = devices
        self.hubs = hubs
        self.ports: dict[str, dict[str, Any]] = {}
        self.hub_entries: dict[str, dict[str, Any]] = {}
        self._handles: dict[str, int | None] = {}
        self._unasked: dict[str, str] = {}

    def read_chain(self, ancestors: Sequence[str]) -> str | None:
        """Record the port of every USB device above a disk, and say why the disk's own was not read.

        Only devices plugged into another USB device are in a port: a root hub
        hangs off its PCI controller, and one interface of a composite device
        off the device itself. The nearest of what remains is the disk's own
        port; the rest are the hubs between it and the root.
        """
        plugged = [
            instance
            for instance in ancestors
            if instance in self.devices
            and self.devices[instance].parent in self.devices
            and _COMPOSITE_INTERFACE not in instance.upper()
        ]
        errors = [self._record(instance) for instance in plugged]
        return errors[0] if errors else None

    def _record(self, instance: str) -> str | None:
        """Ask about the port one device is plugged into, once per run, and return why it could not be."""
        if instance in self.ports:
            return self.ports[instance]["error"]
        if instance in self._unasked:
            return self._unasked[instance]
        facts = self.devices[instance]
        place = _place(facts, self.hubs)
        if isinstance(place, str):
            self._unasked[instance] = place
            return place
        hub, port = place
        handle = self._open(hub)
        answers = (
            {"error": self.hub_entries[hub]["error"]} if handle is None else _ask_port(self.kernel32, handle, port)
        )
        self.ports[instance] = {
            "hub": hub,
            "port": port,
            "service": facts.service,
            **_UNANSWERED_PORT,
            **answers,
        }
        return self.ports[instance]["error"]

    def _open(self, hub: str) -> int | None:
        """Open a hub once per run, asking it about itself the first time."""
        if hub in self._handles:
            return self._handles[hub]
        handle = self.kernel32.CreateFileW(
            self.hubs[hub], api.GENERIC_WRITE, api.FILE_SHARE_WRITE, None, api.OPEN_EXISTING, 0, None
        )
        if handle == api.INVALID_HANDLE_VALUE:
            self._handles[hub] = None
            self.hub_entries[hub] = {"information": None, "error": f"Win32 error {api.last_error()}"}
            return None
        self._handles[hub] = handle
        information = _hub_request(
            self.kernel32, handle, api.IOCTL_USB_GET_HUB_INFORMATION_EX, b"", _HUB_INFORMATION_BYTES
        )
        self.hub_entries[hub] = {"information": _encoded(information), "error": None}
        return handle

    def close(self) -> None:
        """Close every hub this run opened."""
        for handle in self._handles.values():
            if handle is not None:
                self.kernel32.CloseHandle(handle)


def _place(facts: UsbDeviceFacts, hubs: Mapping[str, str]) -> tuple[str, int] | str:
    """The hub and port a device is plugged into, or why that port cannot be asked about."""
    if facts.parent is None or facts.parent not in hubs:
        return f"no hub interface was found for {facts.parent}, the device above it"
    if facts.port is None:
        return "the hub published no port number for it"
    if not 1 <= facts.port <= _MAX_HUB_PORT:
        return f"the hub published port number {facts.port}, which no hub port can have"
    return facts.parent, facts.port


def read_usb_ports(
    kernel32: api.WinLibrary,
    disks: Mapping[str, Mapping[str, Any]],
    devices: Mapping[str, UsbDeviceFacts],
    hubs: Mapping[str, str],
) -> UsbReading:
    """Ask the hubs about the port every USB disk hangs off, and every port above it.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        disks: Every disk's reading, keyed by interface path; only its
            ``ancestors`` are read.
        devices: Every present USB device, keyed by instance identifier.
        hubs: Every present hub's interface path, keyed by instance identifier.

    Returns:
        The port and hub records, and why any disk's own port could not be read.
    """
    asked = _HubPorts(kernel32, devices, hubs)
    disk_errors: dict[str, str] = {}
    try:
        for path, record in disks.items():
            error = asked.read_chain(record.get("ancestors") or ())
            if error is not None:
                disk_errors[path] = error
    finally:
        asked.close()
    return UsbReading(ports=asked.ports, hubs=asked.hub_entries, disk_errors=disk_errors)


def read_environment(*, read_value: Callable[[str, str], str] = api.read_registry_string) -> dict[str, Any]:
    """Gather the evidence that says whether this is metal, a guest or a container.

    The system manufacturer and product the firmware reports are the Windows
    equivalent of DMI, and they live in the registry, so this needs no extra
    dependency and no subprocess.

    A value longer than :data:`MAX_DEVICE_TEXT` is left out rather than stored:
    firmware chooses these strings, and stored, one oversized value would make
    the capture model refuse the whole scan.

    Args:
        read_value: Reads one registry string by key path and value name.

    Returns:
        The raw strings, for the pure classifier to interpret.
    """
    evidence: dict[str, Any] = {}
    for name, field in (
        ("SystemManufacturer", "dmi_vendor"),
        ("SystemProductName", "dmi_product"),
        ("BaseBoardManufacturer", "dmi_board_vendor"),
        ("BaseBoardProduct", "dmi_board_name"),
    ):
        value = read_value(api.SYSTEM_BIOS_KEY, name)
        if value and len(value) <= MAX_DEVICE_TEXT:
            evidence[field] = value
    # Windows containers set this, and it is the only signal a guest process has.
    if os.environ.get("CONTAINER_SANDBOX_MOUNT_POINT"):
        evidence["container_marker"] = "docker"
    return evidence


def read_volumes_section(
    kernel32: api.WinLibrary, *, last_error: Callable[[], int] = api.last_error
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    r"""Read every volume and the Windows volume, keyed by a per-capture ordinal.

    ``volumes.read_volumes`` and ``volumes.read_windows_volume`` key and name
    volumes by their ``\\?\Volume{...}\`` GUID path, which is machine-unique
    and otherwise unused: :mod:`.usage` only ever joins ``windows_volume``
    into ``volumes`` by looking the key up, never reads the key's shape, and
    nothing downstream parses a GUID out of it either. This is the one place
    that join is rebuilt on an opaque ordinal string (``"0"``, ``"1"``, ... in
    enumeration order) instead, so no GUID path reaches the capture a snapshot
    writes to disk.

    Args:
        kernel32: The typed facade over the Win32 entry points.
        last_error: Answers ``GetLastError`` for the call that just failed.

    Returns:
        Every volume keyed by its ordinal, and the Windows volume's ordinal
        key (``None`` when it could not be resolved); ``(None, None)`` when the
        volumes could not be enumerated.
    """
    by_guid = volumes.read_volumes(kernel32, last_error=last_error)
    if by_guid is None:
        return None, None
    windows_guid = volumes.read_windows_volume(kernel32)
    ordinals = {guid: str(index) for index, guid in enumerate(by_guid)}
    by_ordinal = {ordinals[guid]: entry for guid, entry in by_guid.items()}
    windows_ordinal = ordinals.get(windows_guid) if windows_guid is not None else None
    return by_ordinal, windows_ordinal


def read_disks(tree: _DeviceTree) -> dict[str, dict[str, Any]]:
    """Read every disk the device tree lists, and name the ones it could not list a path for.

    Args:
        tree: The device tree to enumerate disk interfaces through.

    Returns:
        One reading per disk keyed by its interface path, plus one record
        carrying only an ``error`` for each interface whose path could not be
        read, keyed by a label saying so, which the builder reports as a
        refused ``device`` reading.
    """
    disks: dict[str, dict[str, Any]] = {}
    for path, ancestors in tree.disk_interfaces():
        disks[path] = read_disk(tree.kernel32, path, ancestors)
    for position, reason in enumerate(tree.unreadable_interfaces, start=1):
        label = f"unreadable disk interface {position}"
        disks[label] = {
            "path": label,
            "parent": None,
            "ancestors": [],
            "error": f"could not read the device interface path, {reason}",
        }
    return disks


def devices_accessible(disks: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether any disk could be opened, which is what the capture's flag means.

    A disk that was listed but refused to open is a record carrying ``error``,
    so counting records would call a machine whose every disk refused
    "accessible" and hide the one cause that explains an empty report.

    Args:
        disks: The readings :func:`read_disks` returned.

    Returns:
        ``True`` when at least one record is a disk that was opened.

    Example:
        >>> devices_accessible({"a": {"error": "could not open the device"}})
        False
        >>> devices_accessible({"a": {"error": "x"}, "b": {"path": "b"}})
        True
    """
    return any("error" not in record for record in disks.values())


def read_system() -> dict[str, Any]:
    """Read the whole storage subsystem from this Windows machine.

    Returns:
        A JSON-serialisable reading, the same shape a snapshot stores.
    """
    tree = _DeviceTree()
    pci = tree.enumerate_pci()
    disks = read_disks(tree)
    usb_reading = read_usb_ports(tree.kernel32, disks, tree.usb_devices(), tree.usb_hubs())
    for path, error in usb_reading.disk_errors.items():
        disks[path]["usb_link_error"] = error
    volume_entries, windows_volume = read_volumes_section(tree.kernel32)

    return {
        "schema": SCHEMA_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "platform": Platform.WINDOWS.value,
        "hostname": platform.node(),
        "kernel": platform.version(),
        "euid": 0 if is_elevated() else 1,
        "elevated": is_elevated(),
        "environment": read_environment(),
        "devices_accessible": devices_accessible(disks),
        "pci": pci,
        # Resolved HERE and not in the builder: the builder is pure, so a name
        # it looked up itself would come from whichever machine replays the
        # capture rather than from the one that took it.
        "pci_names": pciids.resolve_names(pci),
        "disks": disks,
        "usb_ports": usb_reading.ports,
        "usb_hubs": usb_reading.hubs,
        "volumes": volume_entries,
        "windows_volume": windows_volume,
        "cwd": os.getcwd(),  # noqa: PTH109 - recorded as context for a bug report, not used as a path
    }


__all__ = [
    "UsbDeviceFacts",
    "UsbReading",
    "ata_passthrough",
    "devices_accessible",
    "is_elevated",
    "nvme_protocol_data",
    "query_property",
    "read_ata",
    "read_disk",
    "read_disks",
    "read_environment",
    "read_system",
    "read_usb_ports",
    "read_volumes_section",
    "sat_passthrough",
]
