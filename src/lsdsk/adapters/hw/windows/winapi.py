"""Win32 structures and bindings used to read storage hardware.

Pure ``ctypes``: no pywin32, no WMI, no subprocess.  That keeps the install
small enough for ``uvx`` and means topology and PCIe placement work without
Administrator, which WMI cannot offer.

Everything issued here is a read.  ``IOCTL_STORAGE_QUERY_PROPERTY`` and the
device-tree calls need no privilege at all; only the passthrough commands that
fetch SMART data require Administrator, and they degrade to nothing when it is
absent.

System Role:
    Adapter layer, Windows bindings.  Imported only on Windows.
"""

from __future__ import annotations

import ctypes
import importlib
from ctypes import wintypes
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

# Device enumeration flags for SetupDiGetClassDevsW.
DIGCF_PRESENT = 0x00000002
DIGCF_ALLCLASSES = 0x00000004
DIGCF_DEVICEINTERFACE = 0x00000010

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NOT_FOUND = 1168
ERROR_NO_MORE_ITEMS = 259

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

# Storage ioctls.
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_STORAGE_PROTOCOL_COMMAND = 0x002DD480
IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
IOCTL_SCSI_GET_ADDRESS = 0x00041018
IOCTL_ATA_PASS_THROUGH_DIRECT = 0x0004D030
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080

# STORAGE_PROPERTY_ID values.
STORAGE_DEVICE_PROPERTY = 0
STORAGE_ADAPTER_PROPERTY = 1
STORAGE_DEVICE_SEEK_PENALTY_PROPERTY = 7
STORAGE_DEVICE_TEMPERATURE_PROPERTY = 24
STORAGE_ADAPTER_PROTOCOL_SPECIFIC_PROPERTY = 49
STORAGE_DEVICE_PROTOCOL_SPECIFIC_PROPERTY = 50

# STORAGE_QUERY_TYPE.
PROPERTY_STANDARD_QUERY = 0

# STORAGE_PROTOCOL_TYPE.
PROTOCOL_TYPE_ATA = 2
PROTOCOL_TYPE_NVME = 3

# STORAGE_PROTOCOL_NVME_DATA_TYPE.
NVME_DATA_TYPE_IDENTIFY = 1
NVME_DATA_TYPE_LOG_PAGE = 2

# ATA_PASS_THROUGH_DIRECT flags.
ATA_FLAGS_DRDY_REQUIRED = 0x01
ATA_FLAGS_DATA_IN = 0x02

# The device interface class for disks, GUID_DEVINTERFACE_DISK.
GUID_DEVINTERFACE_DISK = "{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"

# DEVPROPKEY category for PCI device properties, from pciprop.h. The property
# identifiers under it are stable across Windows releases.
PCI_DEVICE_PROPERTY_FMTID = "{3ab22e31-8264-4b4e-9af5-a8d2d8e33e62}"
PCI_PROP_BASE_CLASS = 3
PCI_PROP_SUB_CLASS = 4
PCI_PROP_PROG_IF = 5
PCI_PROP_CURRENT_LINK_SPEED = 9
PCI_PROP_CURRENT_LINK_WIDTH = 10
PCI_PROP_MAX_LINK_SPEED = 11
PCI_PROP_MAX_LINK_WIDTH = 12

# DEVPKEY_Device_* identifiers, from devpkey.h.
DEVICE_PROPERTY_FMTID = "{a45c254e-df1c-4efd-8020-67d146a850e0}"
DEVICE_PROP_DEVICEDESC = 2
DEVICE_PROP_FRIENDLYNAME = 14
DEVICE_PROP_CLASS = 9
DEVICE_PROP_DRIVER = 11
# The service that drives the device - stornvme, storahci, iaStorVD. This is
# the counterpart of the bound kernel module Linux reports, which is what the
# driver column means. DEVICE_PROP_DRIVER above is the driver's registry key,
# a GUID and an index, and is not a name anybody would recognise.
DEVICE_PROP_SERVICE = 6
DEVICE_PROP_LOCATION_INFO = 15
# The slot number firmware assigned this device, from ACPI _SUN. It is the only
# readable pointer to a physical connector on Windows, which cannot read PCI
# configuration space the way Linux does.
DEVICE_PROP_UINUMBER = 18
# Bus number and device address, which together give the familiar PCI address.
# These are numeric, unlike LocationInfo, which is a localised sentence and
# therefore useless for parsing on a non-English Windows.
DEVICE_PROP_BUSNUMBER = 23
DEVICE_PROP_ADDRESS = 30

# DEVPROP_TYPE values we handle.
DEVPROP_TYPE_UINT32 = 0x00000007
DEVPROP_TYPE_STRING = 0x00000012


class GUID(ctypes.Structure):
    """A Windows GUID."""

    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )


class DEVPROPKEY(ctypes.Structure):
    """A device property key: a format GUID plus an identifier."""

    _fields_ = (("fmtid", GUID), ("pid", ctypes.c_ulong))


class SP_DEVINFO_DATA(ctypes.Structure):
    """Identifies one device in a device information set."""

    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    )


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    """Identifies one device interface in a device information set."""

    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    )


class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    """The request header for IOCTL_STORAGE_QUERY_PROPERTY."""

    _fields_ = (
        ("PropertyId", wintypes.DWORD),
        ("QueryType", wintypes.DWORD),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    )


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    """Identity of a storage device, with strings at trailing offsets."""

    _fields_ = (
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("DeviceType", ctypes.c_ubyte),
        ("DeviceTypeModifier", ctypes.c_ubyte),
        ("RemovableMedia", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("VendorIdOffset", wintypes.DWORD),
        ("ProductIdOffset", wintypes.DWORD),
        ("ProductRevisionOffset", wintypes.DWORD),
        ("SerialNumberOffset", wintypes.DWORD),
        ("BusType", wintypes.DWORD),
        ("RawPropertiesLength", wintypes.DWORD),
    )


class DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
    """Whether a device has a seek penalty, which distinguishes disk from SSD."""

    _fields_ = (
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("IncursSeekPenalty", wintypes.BOOLEAN),
    )


class STORAGE_TEMPERATURE_INFO(ctypes.Structure):
    """One temperature sensor's reading and its thresholds."""

    _fields_ = (
        ("Index", wintypes.WORD),
        ("Temperature", ctypes.c_short),
        ("OverThreshold", ctypes.c_short),
        ("UnderThreshold", ctypes.c_short),
        ("OverThresholdChanged", wintypes.BOOLEAN),
        ("UnderThresholdChanged", wintypes.BOOLEAN),
    )


class STORAGE_TEMPERATURE_DATA_DESCRIPTOR(ctypes.Structure):
    """The header of a temperature query response."""

    _fields_ = (
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("CriticalTemperature", ctypes.c_short),
        ("WarningTemperature", ctypes.c_short),
        ("InfoCount", wintypes.WORD),
        ("Reserved0", ctypes.c_ubyte * 2),
        ("Reserved1", ctypes.c_ulong * 2),
        ("TemperatureInfo", STORAGE_TEMPERATURE_INFO * 1),
    )


class STORAGE_PROTOCOL_SPECIFIC_DATA(ctypes.Structure):
    """Selects a protocol-specific payload, such as an NVMe log page."""

    _fields_ = (
        ("ProtocolType", wintypes.DWORD),
        ("DataType", wintypes.DWORD),
        ("ProtocolDataRequestValue", wintypes.DWORD),
        ("ProtocolDataRequestSubValue", wintypes.DWORD),
        ("ProtocolDataOffset", wintypes.DWORD),
        ("ProtocolDataLength", wintypes.DWORD),
        ("FixedProtocolReturnData", wintypes.DWORD),
        ("ProtocolDataRequestSubValue2", wintypes.DWORD),
        ("ProtocolDataRequestSubValue3", wintypes.DWORD),
        ("ProtocolDataRequestSubValue4", wintypes.DWORD),
    )


class ATA_PASS_THROUGH_DIRECT(ctypes.Structure):
    """The request for IOCTL_ATA_PASS_THROUGH_DIRECT."""

    _fields_ = (
        ("Length", wintypes.USHORT),
        ("AtaFlags", wintypes.USHORT),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("ReservedAsUchar", ctypes.c_ubyte),
        ("DataTransferLength", wintypes.DWORD),
        ("TimeOutValue", wintypes.DWORD),
        ("ReservedAsUlong", wintypes.DWORD),
        ("DataBuffer", ctypes.c_void_p),
        ("PreviousTaskFile", ctypes.c_ubyte * 8),
        ("CurrentTaskFile", ctypes.c_ubyte * 8),
    )


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    """Which PhysicalDrive number the operating system gave a disk."""

    _fields_ = (
        ("DeviceType", wintypes.DWORD),
        ("DeviceNumber", wintypes.DWORD),
        ("PartitionNumber", wintypes.DWORD),
    )


class SCSI_ADDRESS(ctypes.Structure):
    """Where a device sits on its SCSI-style bus."""

    _fields_ = (
        ("Length", wintypes.DWORD),
        ("PortNumber", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
    )


def parse_guid(text: str) -> GUID:
    """Build a GUID structure from its brace-and-hyphen text form.

    Args:
        text: A GUID such as ``{53f56307-b6bf-11d0-94f2-00a0c91efb8b}``.

    Returns:
        The GUID structure.

    Example:
        >>> guid = parse_guid("{53f56307-b6bf-11d0-94f2-00a0c91efb8b}")
        >>> hex(guid.Data1)
        '0x53f56307'
    """
    cleaned = text.strip("{}")
    parts = cleaned.split("-")
    guid = GUID()
    guid.Data1 = int(parts[0], 16)
    guid.Data2 = int(parts[1], 16)
    guid.Data3 = int(parts[2], 16)
    tail = bytes.fromhex(parts[3] + parts[4])
    guid.Data4 = (ctypes.c_ubyte * 8)(*tail)
    return guid


def make_property_key(fmtid: str, pid: int) -> DEVPROPKEY:
    """Build a device property key.

    Args:
        fmtid: The property category GUID.
        pid: The identifier within that category.

    Returns:
        The property key structure.
    """
    key = DEVPROPKEY()
    key.fmtid = parse_guid(fmtid)
    key.pid = pid
    return key


# Bus types reported in STORAGE_DEVICE_DESCRIPTOR.BusType.
BUS_TYPE_NAMES: dict[int, str] = {
    0x01: "scsi",
    0x02: "atapi",
    0x03: "ata",
    0x04: "1394",
    0x05: "ssa",
    0x06: "fibre",
    0x07: "usb",
    0x08: "raid",
    0x09: "iscsi",
    0x0A: "sas",
    0x0B: "sata",
    0x0C: "sd",
    0x0D: "mmc",
    0x0E: "virtual",
    0x0F: "file-backed virtual",
    0x10: "spaces",
    0x11: "nvme",
    0x12: "scm",
    0x13: "ufs",
}

# PCI Express link speed encodings, as the PCI property reports them.
LINK_SPEED_GTPS: dict[int, float] = {1: 2.5, 2: 5.0, 3: 8.0, 4: 16.0, 5: 32.0, 6: 64.0}


class WinFunction(Protocol):
    """One exported DLL entry point.

    ``ctypes`` builds these at attribute-access time, so they cannot be declared
    individually.  Declaring the shape they all share gives call sites real
    types without pretending to know each signature.
    """

    restype: object
    argtypes: object

    def __call__(self, *args: object) -> int: ...


class WinLibraries(NamedTuple):
    """The three Windows libraries this reader binds.

    Named because all three are the same type: unpacked positionally, a
    reordering type-checks perfectly and then calls SetupAPI functions on
    kernel32. There is nothing else to catch that.

    Attributes:
        setupapi: Device enumeration.
        cfgmgr32: Configuration manager properties.
        kernel32: Handles and DeviceIoControl.
    """

    setupapi: WinLibrary
    cfgmgr32: WinLibrary
    kernel32: WinLibrary


class WinLibrary(Protocol):
    """A loaded Windows DLL, whose members are resolved on first access."""

    def __getattr__(self, name: str) -> WinFunction: ...


def last_error() -> int:
    """Return the last Win32 error for the current thread.

    Resolved by name for the same reason as ``WinDLL``: it exists only on
    Windows, and this module has to stay type-checkable elsewhere.
    """
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def load_library(name: str) -> WinLibrary:
    """Load one Windows DLL with a typed handle.

    ``ctypes.WinDLL`` exists only on Windows, so it is resolved by name rather
    than referenced directly.  That keeps this module importable, and
    type-checkable, on the Linux and macOS machines the project is developed and
    checked on.

    Args:
        name: The DLL to load.

    Returns:
        The loaded library.

    Raises:
        OSError: If this platform has no ``WinDLL`` or the library is missing.
    """
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        message = f"{name} can only be loaded on Windows"
        raise OSError(message)
    return cast("WinLibrary", loader(name, use_last_error=True))


def configure_prototypes(setupapi: WinLibrary, cfgmgr32: WinLibrary, kernel32: WinLibrary) -> None:
    """Declare argument and return types for every entry point used.

    This is not optional tidiness.  An unprototyped ``ctypes`` call marshals
    each argument as a C ``int``, and a Windows HANDLE on a 64-bit build does
    not fit in one, so the first call that receives a real handle fails with
    "int too long to convert".  Declaring the types is what makes the handle be
    passed as a pointer-width value.
    """
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = (
        ctypes.POINTER(GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    )
    setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInfo.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(SP_DEVINFO_DATA),
    )
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = (wintypes.HANDLE,)
    setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
    setupapi.SetupDiGetDevicePropertyW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(SP_DEVINFO_DATA),
        ctypes.POINTER(DEVPROPKEY),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    )
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    )
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(SP_DEVINFO_DATA),
    )

    # CM_* take a DEVINST, which is a DWORD, not a handle.
    cfgmgr32.CM_Get_Device_IDW.restype = wintypes.DWORD
    cfgmgr32.CM_Get_Device_IDW.argtypes = (wintypes.DWORD, wintypes.LPWSTR, ctypes.c_ulong, ctypes.c_ulong)
    for name in ("CM_Get_Parent", "CM_Get_Child", "CM_Get_Sibling"):
        entry = getattr(cfgmgr32, name)
        entry.restype = wintypes.DWORD
        entry.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_ulong)

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.DeviceIoControl.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


# The registry hive that holds the firmware's own description of the machine.
HKEY_LOCAL_MACHINE = 0x80000002
SYSTEM_BIOS_KEY = r"HARDWARE\DESCRIPTION\System\BIOS"


def read_registry_string(path: str, name: str, hive: int = HKEY_LOCAL_MACHINE) -> str:
    """Read one string value from the registry.

    ``winreg`` exists only on Windows, so it is imported by name for the same
    reason ``WinDLL`` is: this module has to stay importable, and type-checkable,
    on the machines the project is developed on.

    Args:
        path: The key path under the hive.
        name: The value to read.
        hive: The hive handle.

    Returns:
        The value, or an empty string when it is absent or not a string.
    """
    try:
        winreg = importlib.import_module("winreg")
    except ImportError:
        return ""
    open_key = cast("Callable[[int, str], AbstractContextManager[object]]", winreg.OpenKey)
    query = cast("Callable[[object, str], tuple[object, int]]", winreg.QueryValueEx)
    try:
        with open_key(hive, path) as key:
            value, _ = query(key, name)
    except OSError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def load_libraries() -> WinLibraries:
    """Load the three DLLs this adapter needs, with prototypes declared.

    Returns:
        The setupapi, cfgmgr32 and kernel32 handles.
    """
    setupapi = load_library("setupapi")
    cfgmgr32 = load_library("cfgmgr32")
    kernel32 = load_library("kernel32")
    configure_prototypes(setupapi, cfgmgr32, kernel32)
    return WinLibraries(setupapi, cfgmgr32, kernel32)


__all__ = [
    "ATA_FLAGS_DATA_IN",
    "ATA_FLAGS_DRDY_REQUIRED",
    "ATA_PASS_THROUGH_DIRECT",
    "BUS_TYPE_NAMES",
    "DEVICE_PROPERTY_FMTID",
    "DEVICE_PROP_ADDRESS",
    "DEVICE_PROP_BUSNUMBER",
    "DEVICE_PROP_DEVICEDESC",
    "DEVICE_PROP_DRIVER",
    "DEVICE_PROP_FRIENDLYNAME",
    "DEVICE_PROP_LOCATION_INFO",
    "DEVICE_PROP_SERVICE",
    "DEVICE_SEEK_PENALTY_DESCRIPTOR",
    "DEVPROPKEY",
    "DEVPROP_TYPE_STRING",
    "DEVPROP_TYPE_UINT32",
    "DIGCF_ALLCLASSES",
    "DIGCF_DEVICEINTERFACE",
    "DIGCF_PRESENT",
    "GENERIC_WRITE",
    "GUID",
    "GUID_DEVINTERFACE_DISK",
    "HKEY_LOCAL_MACHINE",
    "INVALID_HANDLE_VALUE",
    "IOCTL_ATA_PASS_THROUGH_DIRECT",
    "IOCTL_DISK_GET_LENGTH_INFO",
    "IOCTL_SCSI_GET_ADDRESS",
    "IOCTL_STORAGE_GET_DEVICE_NUMBER",
    "IOCTL_STORAGE_PROTOCOL_COMMAND",
    "IOCTL_STORAGE_QUERY_PROPERTY",
    "LINK_SPEED_GTPS",
    "NVME_DATA_TYPE_IDENTIFY",
    "NVME_DATA_TYPE_LOG_PAGE",
    "PCI_DEVICE_PROPERTY_FMTID",
    "PCI_PROP_BASE_CLASS",
    "PCI_PROP_CURRENT_LINK_SPEED",
    "PCI_PROP_CURRENT_LINK_WIDTH",
    "PCI_PROP_MAX_LINK_SPEED",
    "PCI_PROP_MAX_LINK_WIDTH",
    "PCI_PROP_PROG_IF",
    "PCI_PROP_SUB_CLASS",
    "PROTOCOL_TYPE_NVME",
    "SCSI_ADDRESS",
    "SP_DEVICE_INTERFACE_DATA",
    "SP_DEVINFO_DATA",
    "STORAGE_ADAPTER_PROPERTY",
    "STORAGE_DEVICE_DESCRIPTOR",
    "STORAGE_DEVICE_NUMBER",
    "STORAGE_DEVICE_PROPERTY",
    "STORAGE_DEVICE_PROTOCOL_SPECIFIC_PROPERTY",
    "STORAGE_DEVICE_SEEK_PENALTY_PROPERTY",
    "STORAGE_DEVICE_TEMPERATURE_PROPERTY",
    "STORAGE_PROPERTY_QUERY",
    "STORAGE_PROTOCOL_SPECIFIC_DATA",
    "STORAGE_TEMPERATURE_DATA_DESCRIPTOR",
    "SYSTEM_BIOS_KEY",
    "WinFunction",
    "WinLibraries",
    "WinLibrary",
    "configure_prototypes",
    "last_error",
    "load_libraries",
    "load_library",
    "make_property_key",
    "parse_guid",
    "read_registry_string",
]
