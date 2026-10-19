"""The drives inside a Storage Spaces pool are read, and each is linked to the Space it backs.

Windows moves a pooled drive's device interface from ``GUID_DEVINTERFACE_DISK`` to
``GUID_DEVINTERFACE_HIDDEN_DISK``, so a reader listing only the first class never sees it
(ADR 0007). These tests drive the reader through the same SetupAPI and cfgmgr32 fakes the
device-tree tests use, so the enumeration and the property reads run on every runner.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from test_windows_device_tree import FakeCfgmgr32, FakeSetupApi
from test_windows_device_tree import _tree as _wired_tree  # pyright: ignore[reportPrivateUsage]

from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.reader import DiskInterface

_DISK = 11
_POOLED = 12
_SPACE = 13

_SPACE_INSTANCE = "STORAGE\\DISK\\{69486059-9506-4070-8B3A-C7A565EE19AB}"
_POOLED_PATH = "\\\\?\\scsi#disk&ven_qemu&prod_harddisk#6&2b320803&0&010000#{7fccc86c-228a-40ad-8a58-f590af7bfdce}"


def _string_list_bytes(*items: str) -> bytes:
    """Encode a DEVPROP_TYPE_STRING_LIST the way SetupAPI hands one back: UTF-16LE multi-sz."""
    return "".join(f"{item}\x00" for item in items).encode("utf-16-le") + b"\x00\x00"


def _relations(devinst: int, *items: str) -> dict[tuple[int, str, int], tuple[int, bytes]]:
    key = (devinst, api.DEVICE_RELATIONS_FMTID, api.DEVICE_PROP_REMOVAL_RELATIONS)
    return {key: (api.DEVPROP_TYPE_STRING_LIST, _string_list_bytes(*items))}


def _tree(setupapi: FakeSetupApi, cfgmgr32: FakeCfgmgr32) -> reader._DeviceTree:  # pyright: ignore[reportPrivateUsage] - the seam under test
    """The device-tree tests' own wiring, which also answers ``GetLastError`` from the fake."""
    return _wired_tree(setupapi=setupapi, cfgmgr32=cfgmgr32)


@pytest.mark.os_agnostic
def test_the_constants_match_the_windows_sdk() -> None:
    """Written from ntddstor.h, devpkey.h and devpropdef.h, not from the module under test."""
    assert api.GUID_DEVINTERFACE_HIDDEN_DISK == "{7fccc86c-228a-40ad-8a58-f590af7bfdce}"
    assert api.DEVICE_RELATIONS_FMTID == "{4340a6c5-93fa-4706-972c-7b648008a5a7}"
    assert api.DEVICE_PROP_REMOVAL_RELATIONS == 5
    assert api.DEVPROP_TYPE_STRING_LIST == 0x12 | 0x2000


@pytest.mark.os_agnostic
def test_a_pooled_drive_is_listed_with_the_space_its_removal_relations_name() -> None:
    setupapi = FakeSetupApi(
        by_guid={
            api.GUID_DEVINTERFACE_DISK: [("\\\\?\\disk0", _DISK)],
            api.GUID_DEVINTERFACE_HIDDEN_DISK: [(_POOLED_PATH, _POOLED)],
        },
        properties=_relations(_POOLED, "STORAGE\\Volume\\{ce701527}#0000000000004400", _SPACE_INSTANCE),
    )
    cfgmgr32 = FakeCfgmgr32(device_ids={_DISK: "SCSI\\DISK\\0", _POOLED: "SCSI\\DISK\\1"})

    found = _tree(setupapi, cfgmgr32).disk_interfaces()

    assert found == [
        DiskInterface(path="\\\\?\\disk0", ancestors=(), instance="SCSI\\DISK\\0", hidden=False, removal_relations=()),
        DiskInterface(
            path=_POOLED_PATH,
            ancestors=(),
            instance="SCSI\\DISK\\1",
            hidden=True,
            removal_relations=("STORAGE\\Volume\\{ce701527}#0000000000004400", _SPACE_INSTANCE),
        ),
    ]


@pytest.mark.os_agnostic
def test_a_machine_with_no_pool_lists_no_hidden_drive() -> None:
    """The control: the hidden class is asked and answers nothing, so nothing changes."""
    setupapi = FakeSetupApi(
        by_guid={api.GUID_DEVINTERFACE_DISK: [("\\\\?\\disk0", _DISK)], api.GUID_DEVINTERFACE_HIDDEN_DISK: []}
    )
    found = _tree(setupapi, FakeCfgmgr32(device_ids={_DISK: "SCSI\\DISK\\0"})).disk_interfaces()
    assert [(interface.path, interface.hidden) for interface in found] == [("\\\\?\\disk0", False)]


@pytest.mark.os_agnostic
def test_a_relations_property_of_another_type_is_read_as_none() -> None:
    """A driver chooses the type; a plain string where a list belongs is not split into one."""
    key = (_POOLED, api.DEVICE_RELATIONS_FMTID, api.DEVICE_PROP_REMOVAL_RELATIONS)
    setupapi = FakeSetupApi(
        by_guid={api.GUID_DEVINTERFACE_HIDDEN_DISK: [(_POOLED_PATH, _POOLED)]},
        properties={key: (api.DEVPROP_TYPE_STRING, (_SPACE_INSTANCE + "\x00").encode("utf-16-le"))},
    )
    found = _tree(setupapi, FakeCfgmgr32(device_ids={_POOLED: "SCSI\\DISK\\1"})).disk_interfaces()
    assert [interface.removal_relations for interface in found] == [()]


@pytest.mark.os_agnostic
def test_a_hidden_list_cut_short_is_recorded_under_its_own_name() -> None:
    """A pooled drive lost to a failing enumeration is a lost drive, like any other."""
    setupapi = FakeSetupApi(
        by_guid={api.GUID_DEVINTERFACE_DISK: [], api.GUID_DEVINTERFACE_HIDDEN_DISK: [(_POOLED_PATH, _POOLED)]},
        fail_enum_at={2: 0},
    )
    tree = _tree(setupapi, FakeCfgmgr32(device_ids={_POOLED: "SCSI\\DISK\\1"}))
    assert tree.disk_interfaces() == []
    assert list(tree.lists_cut_short) == ["hidden disk interfaces"]


def _entry(*, node: str | None, hidden: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": f"{node}-{hidden}", "hidden": hidden}
    if node is not None:
        entry["node"] = node
    return entry


@pytest.mark.os_agnostic
def test_a_hidden_interface_of_a_drive_already_listed_is_dropped() -> None:
    disks = {
        "a": _entry(node="PhysicalDrive1", hidden=False),
        "b": _entry(node="PhysicalDrive1", hidden=True),
        "c": _entry(node="PhysicalDrive2", hidden=True),
    }
    assert list(reader.without_doubles(disks)) == ["a", "c"]


@pytest.mark.os_agnostic
def test_a_hidden_drive_whose_number_was_not_read_is_kept() -> None:
    """With no number there is nothing to say it is a drive already listed, so dropping it could lose one."""
    disks = {"a": _entry(node="PhysicalDrive1", hidden=False), "b": _entry(node=None, hidden=True)}
    assert list(reader.without_doubles(disks)) == ["a", "b"]


class _RefusingKernel:
    """A ``kernel32`` that opens nothing, so a disk's record holds only what the device tree gave it."""

    def CreateFileW(self, *_args: object) -> int:  # noqa: N802 - the Win32 name
        return cast("int", api.INVALID_HANDLE_VALUE)


@pytest.mark.os_agnostic
def test_a_pooled_drive_reaches_the_capture_with_its_instance_its_mark_and_the_space_it_backs() -> None:
    setupapi = FakeSetupApi(
        by_guid={
            api.GUID_DEVINTERFACE_DISK: [("\\\\?\\disk0", _DISK)],
            api.GUID_DEVINTERFACE_HIDDEN_DISK: [(_POOLED_PATH, _POOLED)],
        },
        properties=_relations(_POOLED, _SPACE_INSTANCE),
    )
    tree = reader._DeviceTree(  # pyright: ignore[reportPrivateUsage] - the seam under test
        setupapi=cast("api.WinLibrary", setupapi),
        cfgmgr32=cast("api.WinLibrary", FakeCfgmgr32(device_ids={_DISK: "SCSI\\DISK\\0", _POOLED: "SCSI\\DISK\\1"})),
        kernel32=cast("api.WinLibrary", _RefusingKernel()),
        last_error=setupapi.last_error,
    )

    disks = reader.read_disks(tree)

    ordinary = disks["\\\\?\\disk0"]
    pooled = disks[_POOLED_PATH]
    assert (ordinary["instance"], "hidden" in ordinary, "removal_relations" in ordinary) == (
        "SCSI\\DISK\\0",
        False,
        False,
    )
    assert (pooled["instance"], pooled["hidden"], pooled["removal_relations"]) == (
        "SCSI\\DISK\\1",
        True,
        [_SPACE_INSTANCE],
    )
