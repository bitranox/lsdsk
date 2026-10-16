"""A refused Win32 call is named by the error the caller's own getter answers.

The device tree already takes its ``GetLastError`` reader as a keyword, so a
fake can answer it off Windows, where ``ctypes`` answers 0 for every call. The
disk reader and the hub reader read ``ctypes`` directly instead, so off Windows
every refusal they recorded said "Win32 error 0" and no test could tell the
right number from that one. These drive each through the tree's own getter.
"""

from __future__ import annotations

import struct
from typing import Any, cast

from test_windows_device_tree import FakeCfgmgr32, FakeSetupApi
from test_windows_nvme_reader import FakeNvmeKernel32
from test_windows_usb_reader import DEVICES, HUBS, ROOT_HUB_PATH, SSD, USB2_HUB, USB2_HUB_PATH, FakeHubKernel
from test_windows_volume_reader import FakeVolumeKernel

from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.reader import (
    SystemReaders,
    _DeviceTree,  # pyright: ignore[reportPrivateUsage] - the tree is the seam the fakes reach the reader through
    read_system,
)

#: An error no successful call leaves and no fake answers by accident: ERROR_NOT_FOUND.
ERROR_NOT_FOUND = 1168
_DISK_PATH = "\\\\?\\disk0"
_DISK_DEVINST = 1


def _full_page(data_type: int) -> bytes:
    return bytes(4096 if data_type == api.NVME_DATA_TYPE_IDENTIFY else 512)


def test_a_refused_device_number_names_the_error_the_trees_getter_answers() -> None:
    setupapi = FakeSetupApi(by_guid={api.GUID_DEVINTERFACE_DISK: [(_DISK_PATH, _DISK_DEVINST)]})
    kernel = FakeNvmeKernel32(answer=_full_page, number_error=ERROR_NOT_FOUND)
    tree = _DeviceTree(
        setupapi=cast("api.WinLibrary", setupapi),
        cfgmgr32=cast("api.WinLibrary", FakeCfgmgr32(device_ids={_DISK_DEVINST: "DISK\\A"})),
        kernel32=cast("api.WinLibrary", kernel),
        # The disk is read after its interface list ended, so kernel32's error is the latest once it has one.
        last_error=lambda: kernel.error or setupapi.error,
    )

    disks = reader.read_disks(tree)

    assert disks[_DISK_PATH]["device_number_error"] == f"Win32 error {ERROR_NOT_FOUND}"
    assert tree.lists_cut_short == {}, "the interface list itself ended as a complete one does"


def test_a_hub_that_will_not_open_and_a_port_that_will_not_answer_name_the_getters_error() -> None:
    kernel = FakeHubKernel({USB2_HUB_PATH: {}, ROOT_HUB_PATH: {}}, unopenable=frozenset({USB2_HUB_PATH}))

    reading = reader.read_usb_ports(
        cast("api.WinLibrary", kernel),
        {"disk0": {"ancestors": [SSD, USB2_HUB]}},
        DEVICES,
        HUBS,
        last_error=lambda: ERROR_NOT_FOUND,
    )

    assert reading.disk_errors["disk0"] == f"Win32 error {ERROR_NOT_FOUND}", "the hub the disk is in did not open"
    assert reading.ports[USB2_HUB]["error"] == f"Win32 error {ERROR_NOT_FOUND}", "the root hub's port did not answer"


#: The USB device a disk hangs off and the hub it is plugged into, as the device tree lists them.
_USB_DISK_DEVICE, _HUB_DEVICE = 21, 22
_HUB_INTERFACE = "\\\\?\\usb#hub"


def _usb_disk(tree: _DeviceTree) -> dict[str, dict[str, Any]]:
    del tree
    return {_DISK_PATH: {"path": _DISK_PATH, "parent": "USB\\DEV", "ancestors": ["USB\\DEV"], "node": "PhysicalDrive0"}}


def test_read_system_hands_the_trees_getter_to_the_usb_reader() -> None:
    setupapi = FakeSetupApi(
        by_enumerator={"USB": [_USB_DISK_DEVICE, _HUB_DEVICE]},
        by_guid={api.GUID_DEVINTERFACE_USB_HUB: [(_HUB_INTERFACE, _HUB_DEVICE)]},
        properties={
            (_USB_DISK_DEVICE, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS): (
                api.DEVPROP_TYPE_UINT32,
                struct.pack("<I", 3),
            )
        },
    )
    cfgmgr32 = FakeCfgmgr32(
        device_ids={_USB_DISK_DEVICE: "USB\\DEV", _HUB_DEVICE: "USB\\HUB"}, parents={_USB_DISK_DEVICE: _HUB_DEVICE}
    )
    # A machine with no volumes whose one hub refuses to open.
    kernel = FakeVolumeKernel({}, refused_opens={_HUB_INTERFACE: ERROR_NOT_FOUND})

    def tree() -> _DeviceTree:
        return _DeviceTree(
            setupapi=cast("api.WinLibrary", setupapi),
            cfgmgr32=cast("api.WinLibrary", cfgmgr32),
            kernel32=cast("api.WinLibrary", kernel),
            last_error=lambda: kernel.error or setupapi.error,
        )

    reading = read_system(
        SystemReaders(tree=tree, disks=_usb_disk, elevated=lambda: False, environment=dict, pci_names=lambda pci: {})
    )

    assert reading["disks"][_DISK_PATH]["usb_link_error"] == f"Win32 error {ERROR_NOT_FOUND}"


def test_an_interface_whose_path_cannot_be_sized_names_the_trees_error() -> None:
    setupapi = FakeSetupApi(
        by_guid={api.GUID_DEVINTERFACE_DISK: [(_DISK_PATH, _DISK_DEVINST)]},
        refuse_sizing=True,
        fail_error=ERROR_NOT_FOUND,
    )
    tree = _DeviceTree(
        setupapi=cast("api.WinLibrary", setupapi),
        cfgmgr32=cast("api.WinLibrary", FakeCfgmgr32()),
        kernel32=cast("api.WinLibrary", FakeNvmeKernel32(answer=_full_page)),
        last_error=setupapi.last_error,
    )

    assert tree.disk_interfaces() == []
    assert tree.unreadable_interfaces == [f"Win32 error {ERROR_NOT_FOUND}"]
