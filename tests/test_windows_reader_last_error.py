"""A refused Win32 call is named by the error the caller's own getter answers.

The device tree takes its ``GetLastError`` reader as a keyword, so a fake can
answer it off Windows, where ``ctypes`` answers 0 for every call. A reader that
asked ``ctypes`` directly would record "Win32 error 0" for every refusal in a
test, and no test could tell the right number from that one. These drive the
disk, hub, passthrough and NVMe readers through the tree's own getter, and a
source scan holds every Windows reader module to reaching it through a parameter.
"""

from __future__ import annotations

import ast
import ctypes
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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

if TYPE_CHECKING:
    from collections.abc import Callable

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


#: A second error, so the ATA and the SAT refusal of one command can each be told apart: ERROR_IO_DEVICE.
ERROR_IO_DEVICE = 1117
_SATA_PATH = "\\\\?\\sata#disk#planted"


class _RefusingSataKernel:
    """A ``kernel32`` for one SATA disk opened for passthrough, whose drive refuses every ATA question.

    The ATA ioctl fails leaving ``ERROR_NOT_FOUND`` and the SAT one leaving
    ``ERROR_IO_DEVICE``, so a refusal recorded under the wrong getter, or under
    the right getter read at the wrong failure, names the wrong number.

    Attributes:
        error: What ``GetLastError`` answers, as the last failing call left it.
    """

    def __init__(self) -> None:
        """Start with no failure recorded."""
        self.error = 0

    def CreateFileW(self, path: str, access: int, *_rest: object) -> int:  # noqa: N802 - the Win32 name
        del path, access
        return 7

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
        if code == api.IOCTL_ATA_PASS_THROUGH_DIRECT:
            self.error = ERROR_NOT_FOUND
            return 0
        if code == api.IOCTL_SCSI_PASS_THROUGH:
            self.error = ERROR_IO_DEVICE
            return 0
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


def _read_through_the_tree(kernel: object, path: str, last_error: Callable[[], int]) -> dict[str, Any]:
    """Read the one disk ``kernel`` answers for through ``read_disks``, as a scan does."""
    tree = _DeviceTree(
        setupapi=cast("api.WinLibrary", FakeSetupApi(by_guid={api.GUID_DEVINTERFACE_DISK: [(path, _DISK_DEVINST)]})),
        cfgmgr32=cast("api.WinLibrary", FakeCfgmgr32(device_ids={_DISK_DEVINST: "DISK\\A"})),
        kernel32=cast("api.WinLibrary", kernel),
        last_error=last_error,
    )
    return reader.read_disks(tree)[path]


def test_a_refused_ata_and_sat_question_each_name_the_error_the_trees_getter_answers() -> None:
    kernel = _RefusingSataKernel()

    entry = _read_through_the_tree(kernel, _SATA_PATH, lambda: kernel.error)

    expected = f"passthrough refused, Win32 error {ERROR_NOT_FOUND}; SAT refused, Win32 error {ERROR_IO_DEVICE}"
    assert entry["ata"] == {
        "identify_error": expected,
        "smart_data_error": expected,
        "smart_thresholds_error": expected,
    }


def test_a_refused_nvme_query_names_the_error_the_trees_getter_answers() -> None:
    kernel = FakeNvmeKernel32(answer=lambda _type: None)

    entry = _read_through_the_tree(kernel, _DISK_PATH, lambda: ERROR_NOT_FOUND)

    assert entry["nvme"] == {
        "identify_controller_error": f"Win32 error {ERROR_NOT_FOUND}",
        "smart_log_error": f"Win32 error {ERROR_NOT_FOUND}",
    }


def _calls_of_the_process_wide_getter(source: str) -> list[int]:
    """The lines on which ``source`` calls ``api.last_error()`` itself rather than a getter it was handed."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "last_error"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "api"
    ]


def test_no_windows_reader_asks_the_process_wide_getter_for_an_error() -> None:
    """``api.last_error`` may be a DEFAULT, never CALLED: a call reads 0 off Windows whatever a fake answers.

    Named where a refusal is recorded, the process-wide getter makes every
    recorded number read "Win32 error 0" in a test, so no test can tell the
    right number from that one. The caller's own getter is the only one a fake
    can answer, so the reader is held to reaching it through a parameter.
    """
    package = Path(reader.__file__).parent
    sources = sorted(package.glob("*.py"))
    assert reader.__file__ is not None and Path(reader.__file__) in sources, "the scan does not cover the reader"

    called = {
        path.name: lines
        for path in sources
        if (lines := _calls_of_the_process_wide_getter(path.read_text(encoding="utf-8")))
    }

    assert called == {}


def test_the_getter_scan_finds_a_call_of_the_process_wide_getter() -> None:
    """The control: the scan above must see the shape it forbids, or its empty answer means nothing."""
    planted = 'def refused(api):\n    return f"Win32 error {api.last_error()}"\n'
    assert _calls_of_the_process_wide_getter(planted) == [2]
    assert _calls_of_the_process_wide_getter("def f(last_error=api.last_error):\n    return last_error()\n") == []
