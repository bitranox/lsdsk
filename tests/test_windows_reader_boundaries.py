"""Where the Windows reader meets input it cannot trust, driven through fakes.

Four small gaps, each held at the seam the reader already takes: a disk whose
interface path could not be read is named instead of dropped, ``devices_accessible``
means a disk could be opened, one oversized registry string cannot refuse a whole
capture, and a temperature answer is accepted at the size the driver really sends.
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


class _Setupapi:
    """``setupapi`` enumerating disk interfaces, where the listed ones cannot be read.

    Attributes:
        paths: The interface paths whose detail call succeeds, in order.
        unreadable_at: Indexes whose detail call fails, so no path can be had for them.
    """

    def __init__(self, *, paths: list[str], unreadable_at: set[int]) -> None:
        """Hold the interface table this fake enumerates."""
        self.paths = paths
        self.unreadable_at = unreadable_at
        self.total = len(paths) + len(unreadable_at)

    def SetupDiGetClassDevsW(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return 1

    def SetupDiEnumDeviceInterfaces(  # noqa: N802 - the Win32 name
        self, handle: int, device_info: object, guid: object, index: int, interface: object
    ) -> int:
        del handle, device_info, guid
        if index >= self.total:
            return 0
        getattr(interface, "_obj").Flags = index  # noqa: B009 - the documented way to reach a byref's referent
        return 1

    def SetupDiGetDeviceInterfaceDetailW(  # noqa: N802 - the Win32 name
        self,
        handle: int,
        interface: object,
        buffer: ctypes.Array[ctypes.c_char] | None,
        buffer_size: int,
        required: object,
        info: object | None,
    ) -> int:
        del handle, info
        index: int = getattr(interface, "_obj").Flags  # noqa: B009 - the documented way to reach a byref's referent
        if index in self.unreadable_at:
            getattr(required, "_obj").value = 0  # noqa: B009 - the documented way to reach a byref's referent
            return 0
        readable = [position for position in range(self.total) if position not in self.unreadable_at]
        path = self.paths[readable.index(index)]
        source = ctypes.create_unicode_buffer(path)
        encoded = ctypes.string_at(ctypes.addressof(source), ctypes.sizeof(source))
        needed = 4 + len(encoded)
        getattr(required, "_obj").value = needed  # noqa: B009 - the documented way to reach a byref's referent
        if buffer is None or buffer_size < needed:
            return 0
        ctypes.memmove(ctypes.addressof(buffer) + 4, encoded, len(encoded))
        return 1

    def SetupDiDestroyDeviceInfoList(self, handle: int) -> int:  # noqa: N802 - the Win32 name
        del handle
        return 1


class _Cfgmgr32:
    """``cfgmgr32`` that knows no device."""

    def CM_Get_Device_IDW(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return 1

    def CM_Get_Parent(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return 1


class _RefusingKernel:
    """A ``kernel32`` on which no device can be opened."""

    def CreateFileW(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return cast("int", api.INVALID_HANDLE_VALUE)


def _tree(setupapi: _Setupapi, kernel32: object) -> reader._DeviceTree:  # pyright: ignore[reportPrivateUsage] - the seam under test
    return reader._DeviceTree(  # pyright: ignore[reportPrivateUsage] - the seam under test
        setupapi=cast("api.WinLibrary", setupapi),
        cfgmgr32=cast("api.WinLibrary", _Cfgmgr32()),
        kernel32=cast("api.WinLibrary", kernel32),
    )


@pytest.mark.os_agnostic
def test_a_disk_whose_interface_path_cannot_be_read_is_named_not_dropped() -> None:
    tree = _tree(_Setupapi(paths=["\\\\?\\disk0"], unreadable_at={1}), _RefusingKernel())
    disks = reader.read_disks(tree)
    refused = [record for record in disks.values() if "error" in record]
    assert len(disks) == 2
    assert any("interface" in key for key in disks)
    unreadable = next(record for key, record in disks.items() if "interface" in key)
    assert unreadable["error"].startswith("could not read the device interface path")
    assert refused


@pytest.mark.os_agnostic
def test_an_unreadable_interface_reaches_the_inventory_as_a_refused_device_reading() -> None:
    tree = _tree(_Setupapi(paths=[], unreadable_at={0}), _RefusingKernel())
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["disks"] = reader.read_disks(tree)
    disk = build_from(payload).disks[0]
    assert [reading.reading for reading in disk.readings_refused] == ["device"]


@pytest.mark.os_agnostic
def test_every_interface_readable_adds_no_placeholder_record() -> None:
    tree = _tree(_Setupapi(paths=["\\\\?\\disk0"], unreadable_at=set()), _RefusingKernel())
    assert list(reader.read_disks(tree)) == ["\\\\?\\disk0"]
