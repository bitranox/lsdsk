"""Read storage topology and device blobs from a live Windows system.

The Windows counterpart to the Linux reader, and it produces the same shape of
reading so a snapshot from either can be rendered anywhere.

Two privilege tiers, as on Linux:
    * unprivileged: the device tree, PCIe link state from the PCI device
      properties, disk identity and bus type from ``IOCTL_STORAGE_QUERY_PROPERTY``,
      capacity, solid-state versus rotating, and often temperature
    * Administrator: ATA SMART through ``IOCTL_ATA_PASS_THROUGH_DIRECT`` and the
      NVMe health log through ``IOCTL_STORAGE_PROTOCOL_COMMAND``

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
from typing import Any

from ....domain.enums import BusType, Platform
from ..snapshot import SCHEMA_VERSION
from . import winapi as api

# PCI hardware identifiers look like PCI\VEN_8086&DEV_A182&SUBSYS_...&REV_11.
_HARDWARE_ID = re.compile(r"PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", re.IGNORECASE)

# ATA commands, all read-only, matching the Linux reader.
ATA_IDENTIFY_DEVICE = 0xEC
ATA_SMART = 0xB0
SMART_READ_DATA = 0xD0
SMART_READ_THRESHOLDS = 0xD1

_SECTOR_BYTES = 512
_IOCTL_TIMEOUT_SECONDS = 10
_NVME_IDENTIFY_LENGTH = 4096
_NVME_SMART_LOG_LENGTH = 512
_NVME_SMART_LOG_ID = 0x02


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

    def __init__(self) -> None:
        """Bind the DLL entry points this class uses."""
        self.setupapi, self.cfgmgr32, self.kernel32 = api.load_libraries()

    def enumerate_pci(self) -> dict[str, dict[str, Any]]:
        """Read every present PCI device with its properties."""
        handle = self.setupapi.SetupDiGetClassDevsW(None, "PCI", None, api.DIGCF_PRESENT | api.DIGCF_ALLCLASSES)
        if handle == api.INVALID_HANDLE_VALUE:
            return {}
        devices: dict[str, dict[str, Any]] = {}
        try:
            info = api.SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(api.SP_DEVINFO_DATA)
            index = 0
            while self.setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                index += 1
                instance = self._instance_id(info.DevInst)
                if instance is None:
                    continue
                devices[instance] = self._pci_entry(handle, info, instance)
            return devices
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

    def _child_instances(self, devinst: int) -> list[str]:
        """Return the instance identifiers of a device's children."""
        child = wintypes.DWORD()
        if self.cfgmgr32.CM_Get_Child(ctypes.byref(child), devinst, 0) != 0:
            return []
        found: list[str] = []
        while True:
            instance = self._instance_id(child.value)
            if instance:
                found.append(instance)
            sibling = wintypes.DWORD()
            if self.cfgmgr32.CM_Get_Sibling(ctypes.byref(sibling), child.value, 0) != 0:
                return found
            child = sibling

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
        """Read one string device property."""
        result = self._property(handle, info, fmtid, pid)
        if result is None:
            return None
        prop_type, raw = result
        if prop_type != api.DEVPROP_TYPE_STRING:
            return None
        return raw.decode("utf-16-le", errors="replace").rstrip("\x00").strip() or None

    def disk_interfaces(self) -> list[tuple[str, str | None]]:
        """Return every disk's interface path and its parent instance."""
        guid = api.parse_guid(api.GUID_DEVINTERFACE_DISK)
        handle = self.setupapi.SetupDiGetClassDevsW(
            ctypes.byref(guid), None, None, api.DIGCF_PRESENT | api.DIGCF_DEVICEINTERFACE
        )
        if handle == api.INVALID_HANDLE_VALUE:
            return []
        found: list[tuple[str, str | None]] = []
        try:
            interface = api.SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(api.SP_DEVICE_INTERFACE_DATA)
            index = 0
            while self.setupapi.SetupDiEnumDeviceInterfaces(
                handle, None, ctypes.byref(guid), index, ctypes.byref(interface)
            ):
                index += 1
                path, devinst = self._interface_detail(handle, interface)
                if path:
                    found.append((path, None if devinst is None else self._parent_instance(devinst)))
            return found
        finally:
            self.setupapi.SetupDiDestroyDeviceInfoList(handle)

    def _interface_detail(self, handle: int, interface: api.SP_DEVICE_INTERFACE_DATA) -> tuple[str | None, int | None]:
        """Return one interface's device path and its device instance."""
        required = wintypes.DWORD()
        self.setupapi.SetupDiGetDeviceInterfaceDetailW(
            handle, ctypes.byref(interface), None, 0, ctypes.byref(required), None
        )
        if required.value == 0:
            return None, None
        buffer = ctypes.create_string_buffer(required.value)
        # SP_DEVICE_INTERFACE_DETAIL_DATA_W begins with its own size, which is
        # 8 on 64-bit builds because of the alignment of the trailing string.
        ctypes.memmove(buffer, ctypes.byref(wintypes.DWORD(8)), 4)
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
            return None, None
        path = ctypes.wstring_at(ctypes.addressof(buffer) + 4)
        return path, info.DevInst


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
    """Run IOCTL_STORAGE_QUERY_PROPERTY and return the raw response."""
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


def ata_passthrough(
    kernel32: api.WinLibrary, handle: int, *, command: int, feature: int = 0, lba: int = 0
) -> tuple[bytes, int]:
    """Issue one read-only ATA command through the Windows passthrough ioctl.

    Returns:
        The data returned and the Win32 error code, which is zero on success.
        The code matters: a driver that does not implement passthrough at all
        and a request this code built wrongly both return nothing, and only the
        error tells them apart.
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
        return b"", api.last_error()
    return bytes(buffer), 0


def nvme_protocol_data(
    kernel32: api.WinLibrary, handle: int, *, data_type: int, request_value: int, length: int
) -> bytes:
    """Fetch an NVMe identify structure or log page through the storage stack."""
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
        return b""
    start = offset + protocol.ProtocolDataOffset
    return buffer.raw[start : start + length]


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
    """Read the device's temperature and its own thresholds, when offered."""
    raw = query_property(kernel32, handle, api.STORAGE_DEVICE_TEMPERATURE_PROPERTY, 512)
    if len(raw) < ctypes.sizeof(api.STORAGE_TEMPERATURE_DATA_DESCRIPTOR):
        return {}
    descriptor = api.STORAGE_TEMPERATURE_DATA_DESCRIPTOR.from_buffer_copy(raw)
    if not descriptor.InfoCount:
        return {}
    values: dict[str, int] = {"temperature_c": descriptor.TemperatureInfo[0].Temperature}
    if descriptor.WarningTemperature:
        values["warning_c"] = descriptor.WarningTemperature
    if descriptor.CriticalTemperature:
        values["critical_c"] = descriptor.CriticalTemperature
    return values


def read_disk(kernel32: api.WinLibrary, path: str, parent: str | None) -> dict[str, Any]:
    """Read one disk: identity, geometry, health blobs and its controller."""
    entry: dict[str, Any] = {"path": path, "parent": parent}
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

        bus = entry["device"].get("bus_type", "")
        if bus == BusType.NVME:
            entry["nvme"] = _read_nvme(kernel32, handle)
        elif passthrough:
            entry["ata"] = _read_ata(kernel32, handle)
        else:
            entry["ata"] = {"identify_error": "needs Administrator to open the device for passthrough"}
    finally:
        kernel32.CloseHandle(handle)
    return entry


def _read_nvme(kernel32: api.WinLibrary, handle: int) -> dict[str, str]:
    """Read the NVMe identify structure and health log."""
    record: dict[str, str] = {}
    identify = nvme_protocol_data(
        kernel32, handle, data_type=api.NVME_DATA_TYPE_IDENTIFY, request_value=1, length=_NVME_IDENTIFY_LENGTH
    )
    if identify:
        record["identify_controller"] = base64.b64encode(identify).decode("ascii")
    log = nvme_protocol_data(
        kernel32,
        handle,
        data_type=api.NVME_DATA_TYPE_LOG_PAGE,
        request_value=_NVME_SMART_LOG_ID,
        length=_NVME_SMART_LOG_LENGTH,
    )
    if log:
        record["smart_log"] = base64.b64encode(log).decode("ascii")
    return record


def _read_ata(kernel32: api.WinLibrary, handle: int) -> dict[str, str]:
    """Read the ATA identity and SMART structures."""
    record: dict[str, str] = {}
    for label, command, feature in (
        ("identify", ATA_IDENTIFY_DEVICE, 0),
        ("smart_data", ATA_SMART, SMART_READ_DATA),
        ("smart_thresholds", ATA_SMART, SMART_READ_THRESHOLDS),
    ):
        lba = 0xC24F00 if command == ATA_SMART else 0
        payload, error = ata_passthrough(kernel32, handle, command=command, feature=feature, lba=lba)
        if payload:
            record[label] = base64.b64encode(payload).decode("ascii")
        else:
            record[f"{label}_error"] = f"passthrough refused, Win32 error {error}"
    return record


def read_environment() -> dict[str, Any]:
    """Gather the evidence that says whether this is metal, a guest or a container.

    The system manufacturer and product the firmware reports are the Windows
    equivalent of DMI, and they live in the registry, so this needs no extra
    dependency and no subprocess.

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
        value = api.read_registry_string(api.SYSTEM_BIOS_KEY, name)
        if value:
            evidence[field] = value
    # Windows containers set this, and it is the only signal a guest process has.
    if os.environ.get("CONTAINER_SANDBOX_MOUNT_POINT"):
        evidence["container_marker"] = "docker"
    return evidence


def read_system() -> dict[str, Any]:
    """Read the whole storage subsystem from this Windows machine.

    Returns:
        A JSON-serialisable reading, the same shape a snapshot stores.
    """
    tree = _DeviceTree()
    pci = tree.enumerate_pci()
    disks: dict[str, dict[str, Any]] = {}
    for path, parent in tree.disk_interfaces():
        record = read_disk(tree.kernel32, path, parent)
        disks[path] = record

    return {
        "schema": SCHEMA_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "platform": Platform.WINDOWS.value,
        "hostname": platform.node(),
        "kernel": platform.version(),
        "euid": 0 if is_elevated() else 1,
        "elevated": is_elevated(),
        "environment": read_environment(),
        "devices_accessible": bool(disks),
        "pci": pci,
        "disks": disks,
        "cwd": os.getcwd(),  # noqa: PTH109 - recorded as context for a bug report, not used as a path
    }


__all__ = [
    "ATA_IDENTIFY_DEVICE",
    "ATA_SMART",
    "SMART_READ_DATA",
    "SMART_READ_THRESHOLDS",
    "ata_passthrough",
    "is_elevated",
    "nvme_protocol_data",
    "query_property",
    "read_disk",
    "read_environment",
    "read_system",
]
