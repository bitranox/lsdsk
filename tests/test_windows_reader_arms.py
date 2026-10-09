"""Windows reader arms no other test reached.

A fill call that fails after the sizing call succeeded, an enumeration that is
told not to record what it drops, and a disk opened without write access.
Each is driven through the fake Win32 edge the reader already takes.
"""

from __future__ import annotations

import ctypes
from typing import cast

import pytest

from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api


class _Setupapi:
    """``setupapi`` listing one disk interface whose sizing call succeeds and whose fill call fails."""

    def SetupDiGetClassDevsW(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return 1

    def SetupDiEnumDeviceInterfaces(  # noqa: N802 - the Win32 name
        self, handle: int, device_info: object, guid: object, index: int, interface: object
    ) -> int:
        del handle, device_info, guid, interface
        return 1 if index == 0 else 0

    def SetupDiGetDeviceInterfaceDetailW(  # noqa: N802 - the Win32 name
        self,
        handle: int,
        interface: object,
        buffer: object | None,
        buffer_size: int,
        required: object,
        info: object | None,
    ) -> int:
        del handle, interface, buffer_size, info
        getattr(required, "_obj").value = 64  # noqa: B009 - the documented way to reach a byref's referent
        return 0

    def SetupDiDestroyDeviceInfoList(self, handle: int) -> int:  # noqa: N802 - the Win32 name
        del handle
        return 1


class _Cfgmgr32:
    """``cfgmgr32`` that knows no device."""

    def CM_Get_Device_IDW(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return 1

    def CM_Get_Parent(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return 1


class _ReadOnlySataKernel:
    """A ``kernel32`` opening one SATA disk, but only without write access."""

    def CreateFileW(self, path: str, access: int, *_rest: object) -> int:  # noqa: N802 - the Win32 name
        del path
        return cast("int", api.INVALID_HANDLE_VALUE) if access else 7

    def CloseHandle(self, handle: int) -> int:  # noqa: N802 - the Win32 name
        del handle
        return 1

    def DeviceIoControl(  # noqa: N802 - the Win32 name
        self,
        handle: int,
        code: int,
        in_buffer: object,
        in_size: int,
        out_buffer: object,
        out_size: int,
        returned: object,
        overlapped: object,
    ) -> int:
        del handle, in_size, overlapped
        if code != api.IOCTL_STORAGE_QUERY_PROPERTY or not hasattr(in_buffer, "_obj"):
            return 0
        if getattr(in_buffer, "_obj").PropertyId != api.STORAGE_DEVICE_PROPERTY:  # noqa: B009 - CArgObject
            return 0
        descriptor = api.STORAGE_DEVICE_DESCRIPTOR()
        descriptor.Size = ctypes.sizeof(descriptor)
        descriptor.BusType = 0x0B  # BusTypeSata
        raw = bytes(descriptor)
        ctypes.memmove(cast("ctypes.Array[ctypes.c_char]", out_buffer), raw, min(len(raw), out_size))
        getattr(returned, "_obj").value = len(raw)  # noqa: B009 - the documented way to reach a byref's referent
        return 1


def _tree(setupapi: object, kernel32: object) -> reader._DeviceTree:  # pyright: ignore[reportPrivateUsage] - the seam under test
    return reader._DeviceTree(  # pyright: ignore[reportPrivateUsage] - the seam under test
        setupapi=cast("api.WinLibrary", setupapi),
        cfgmgr32=cast("api.WinLibrary", _Cfgmgr32()),
        kernel32=cast("api.WinLibrary", kernel32),
    )


@pytest.mark.os_agnostic
def test_a_fill_call_that_fails_after_a_good_sizing_call_is_named_not_dropped() -> None:
    disks = reader.read_disks(_tree(_Setupapi(), _ReadOnlySataKernel()))
    assert list(disks) == ["unreadable disk interface 1"]
    assert disks["unreadable disk interface 1"]["error"].startswith(
        "could not read the device interface path, Win32 error"
    )


@pytest.mark.os_agnostic
def test_an_enumeration_told_not_to_record_drops_an_unreadable_interface_without_raising() -> None:
    tree = _tree(_Setupapi(), _ReadOnlySataKernel())
    found = tree._interfaces(api.GUID_DEVINTERFACE_DISK)  # pyright: ignore[reportPrivateUsage] - the seam under test
    assert found == []
    assert tree.unreadable_interfaces == []


@pytest.mark.os_agnostic
def test_an_ata_disk_opened_without_write_access_says_it_needs_administrator() -> None:
    entry = reader.read_disk(cast("api.WinLibrary", _ReadOnlySataKernel()), "\\\\?\\sata#disk", [])
    assert entry["passthrough"] is False
    assert "Administrator" in entry["ata"]["identify_error"]
