"""What ``_DeviceTree`` reads from SetupAPI and cfgmgr32, driven through fakes.

``_DeviceTree.__init__`` takes ``setupapi``/``cfgmgr32``/``kernel32`` as keyword
arguments defaulting to the real DLLs (``api.load_libraries()``), which is the
seam these tests drive it through: a fake of each, built from plain Python
objects over the same ``ctypes`` structures the production code uses, so the
request shapes and the enumeration bookkeeping are held on every runner rather
than only on a Windows machine. ``SP_DEVINFO_DATA``, ``DEVPROPKEY`` and
``GUID`` are declared with fixed-width ``ctypes`` fields (see ``winapi.py``'s
own note), so they import and construct cleanly off Windows too.

A fake method reached through ``ctypes.byref(x)`` reads and writes the wrapped
structure via the proxy's own ``_obj`` attribute, exactly as
``test_windows_volume_reader.py`` does for the kernel32 fakes; a fake reached
through ``ctypes.cast(buffer, ...)`` or a plain ``ctypes.Array`` gets the real
buffer and is written with ``ctypes.memmove``.
"""

from __future__ import annotations

import ctypes
import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.reader import (
    _MAX_SIBLINGS,  # pyright: ignore[reportPrivateUsage] - this is the seam under test
    _MAX_TREE_DEPTH,  # pyright: ignore[reportPrivateUsage] - this is the seam under test
    _DeviceTree,  # pyright: ignore[reportPrivateUsage] - this is the seam under test
)
from lsdsk.domain.text import MAX_DEVICE_TEXT


def _obj(value: object) -> Any:
    """Return the structure a ``ctypes.byref()`` proxy wraps."""
    return getattr(value, "_obj")  # noqa: B009 - the documented way to reach a byref's referent


def _guid_text(guid: api.GUID) -> str:
    """Invert :func:`api.parse_guid`, so a fake can match a property key by its text form."""
    data4 = bytes(guid.Data4)
    return f"{{{guid.Data1:08x}-{guid.Data2:04x}-{guid.Data3:04x}-{data4[:2].hex()}-{data4[2:].hex()}}}"


def _string_bytes(text: str) -> bytes:
    """Encode a device string property the way SetupAPI hands one back: UTF-16LE, NUL-terminated."""
    return text.encode("utf-16-le") + b"\x00\x00"


def _uint_bytes(value: int) -> bytes:
    """Encode a UINT32 device property."""
    return struct.pack("<I", value)


class _NoKernel:
    """A ``kernel32`` the tests never call: ``_DeviceTree`` only stores it for other callers."""


class FakeSetupApi:
    """``setupapi`` as ``_DeviceTree`` sees it: enumerate devices and interfaces, read properties.

    Attributes:
        by_enumerator: Devinst ids a class-based enumeration (``"PCI"``, ``"USB"``) yields.
        by_guid: ``(path, devinst)`` pairs a device-interface enumeration yields, keyed by
            the interface class GUID's text form.
        properties: One property's type and raw bytes, keyed by ``(devinst, fmtid text, pid)``.
        fail_enum_at: The index at which an enumeration on a given handle starts failing,
            although later indices exist - this is backlog [415]'s present behaviour.
        destroyed: Every handle passed to ``SetupDiDestroyDeviceInfoList``.
    """

    def __init__(
        self,
        *,
        by_enumerator: dict[str, list[int]] | None = None,
        by_guid: dict[str, list[tuple[str, int]]] | None = None,
        properties: dict[tuple[int, str, int], tuple[int, bytes]] | None = None,
        fail_enum_at: dict[int, int] | None = None,
    ) -> None:
        """Hold the enumeration, property and failure tables this fake answers from."""
        self.by_enumerator = by_enumerator or {}
        self.by_guid = by_guid or {}
        self.properties = properties or {}
        self.fail_enum_at = fail_enum_at or {}
        self.destroyed: list[int] = []
        self._next_handle = 1
        self._devices: dict[int, list[int]] = {}
        self._ifaces: dict[int, list[tuple[str, int]]] = {}

    def SetupDiGetClassDevsW(self, guid: object, enumerator: str | None, hwnd: object, flags: int) -> int:  # noqa: N802
        del hwnd, flags
        handle = self._next_handle
        if guid is not None:
            devices = self.by_guid.get(_guid_text(_obj(guid)))
            if devices is None:
                return cast("int", api.INVALID_HANDLE_VALUE)
            self._ifaces[handle] = list(devices)
        else:
            devices_by_devinst = self.by_enumerator.get(enumerator or "")
            if devices_by_devinst is None:
                return cast("int", api.INVALID_HANDLE_VALUE)
            self._devices[handle] = list(devices_by_devinst)
        self._next_handle += 1
        return handle

    def SetupDiEnumDeviceInfo(self, handle: int, index: int, info: object) -> int:  # noqa: N802
        devices = self._devices.get(handle, [])
        fail_at = self.fail_enum_at.get(handle)
        if (fail_at is not None and index >= fail_at) or index >= len(devices):
            return 0
        _obj(info).DevInst = devices[index]
        return 1

    def SetupDiDestroyDeviceInfoList(self, handle: int) -> int:  # noqa: N802
        self.destroyed.append(handle)
        return 1

    def SetupDiGetDevicePropertyW(  # noqa: N802
        self,
        handle: int,
        info: object,
        key: object,
        prop_type: object,
        buffer: ctypes.Array[ctypes.c_ubyte] | None,
        buffer_size: int,
        required: object,
        flags: int,
    ) -> int:
        del handle, flags
        devkey = (_obj(info).DevInst, _guid_text(_obj(key).fmtid), _obj(key).pid)
        entry = self.properties.get(devkey)
        if entry is None:
            _obj(required).value = 0
            return 0
        value_type, raw = entry
        _obj(prop_type).value = value_type
        _obj(required).value = len(raw)
        if buffer is None or buffer_size < len(raw):
            return 0
        ctypes.memmove(buffer, raw, len(raw))
        return 1

    def SetupDiEnumDeviceInterfaces(  # noqa: N802
        self, handle: int, device_info: object, guid: object, index: int, interface: object
    ) -> int:
        del device_info, guid
        devices = self._ifaces.get(handle, [])
        fail_at = self.fail_enum_at.get(handle)
        if (fail_at is not None and index >= fail_at) or index >= len(devices):
            return 0
        _obj(interface).Flags = index
        return 1

    def SetupDiGetDeviceInterfaceDetailW(  # noqa: N802
        self,
        handle: int,
        interface: object,
        buffer: ctypes.Array[ctypes.c_char] | None,
        buffer_size: int,
        required: object,
        info: object | None,
    ) -> int:
        devices: list[tuple[str, int]] = self._ifaces.get(handle, [])
        index: int = _obj(interface).Flags
        path, devinst = devices[index]
        # Written with the HOST's own wchar_t width (via create_unicode_buffer)
        # rather than a hardcoded UTF-16LE guess, so ctypes.wstring_at - which
        # reads wchar_t at its OWN platform's width - decodes it back correctly
        # on every runner. Production code only ever runs where both are the
        # same width (Windows), so this is purely the test's own encoding.
        source = ctypes.create_unicode_buffer(path)
        encoded = ctypes.string_at(ctypes.addressof(source), ctypes.sizeof(source))
        needed = 4 + len(encoded)
        _obj(required).value = needed
        if buffer is None or buffer_size < needed:
            return 0
        ctypes.memmove(ctypes.addressof(buffer) + 4, encoded, len(encoded))
        if info is not None:
            _obj(info).DevInst = devinst
        return 1


class FakeCfgmgr32:
    """``cfgmgr32`` as ``_DeviceTree`` sees it: instance ids and the parent/child/sibling walk.

    Attributes:
        device_ids: A devinst's instance identifier, or absent when
            ``CM_Get_Device_IDW`` should fail.
        parents: A devinst's parent devinst, or absent when it has none.
        children: A devinst's first child devinst, or absent when it has none.
        siblings: A devinst's next sibling devinst, or absent when it has none.
    """

    def __init__(
        self,
        *,
        device_ids: dict[int, str] | None = None,
        parents: dict[int, int] | None = None,
        children: dict[int, int] | None = None,
        siblings: dict[int, int] | None = None,
    ) -> None:
        """Hold the devinst graph and instance-id table this fake answers from."""
        self.device_ids = device_ids or {}
        self.parents = parents or {}
        self.children = children or {}
        self.siblings = siblings or {}

    def CM_Get_Device_IDW(self, devinst: int, buffer: ctypes.Array[ctypes.c_wchar], size: int, flags: int) -> int:  # noqa: N802
        del flags
        text = self.device_ids.get(devinst)
        if text is None or len(text) + 1 > size:
            return 1
        buffer.value = text
        return 0

    def CM_Get_Parent(self, parent: object, devinst: int, flags: int) -> int:  # noqa: N802
        del flags
        value = self.parents.get(devinst)
        if value is None:
            return 1
        _obj(parent).value = value
        return 0

    def CM_Get_Child(self, child: object, devinst: int, flags: int) -> int:  # noqa: N802
        del flags
        value = self.children.get(devinst)
        if value is None:
            return 1
        _obj(child).value = value
        return 0

    def CM_Get_Sibling(self, sibling: object, devinst: int, flags: int) -> int:  # noqa: N802
        del flags
        value = self.siblings.get(devinst)
        if value is None:
            return 1
        _obj(sibling).value = value
        return 0


def _tree(
    *,
    setupapi: FakeSetupApi | None = None,
    cfgmgr32: FakeCfgmgr32 | None = None,
) -> _DeviceTree:
    """Build a ``_DeviceTree`` wired to fakes, through the keyword seam ``__init__`` offers."""
    return _DeviceTree(
        setupapi=cast("api.WinLibrary", setupapi or FakeSetupApi()),
        cfgmgr32=cast("api.WinLibrary", cfgmgr32 or FakeCfgmgr32()),
        kernel32=cast("api.WinLibrary", _NoKernel()),
    )


# ---------------------------------------------------------------------------
# enumerate_pci(): the real entry point, driving _present_devices, _pci_entry,
# _pci_address, _property, _uint_property and _string_property together.
# ---------------------------------------------------------------------------

_PCI_DEVINST = 10
_PCI_INSTANCE = "PCI\\VEN_8086&DEV_A182&SUBSYS_00000000&REV_11\\3&11583659&0&A0"


def _pci_properties() -> dict[tuple[int, str, int], tuple[int, bytes]]:
    """The full, valid property set for one PCI device."""
    return {
        (_PCI_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_FRIENDLYNAME): (
            api.DEVPROP_TYPE_STRING,
            _string_bytes("Intel Root Port"),
        ),
        (_PCI_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_SERVICE): (
            api.DEVPROP_TYPE_STRING,
            _string_bytes("pci"),
        ),
        (_PCI_DEVINST, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_BASE_CLASS): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(0x06),
        ),
        (_PCI_DEVINST, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_SUB_CLASS): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(0x04),
        ),
        (_PCI_DEVINST, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_CURRENT_LINK_SPEED): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(3),
        ),
        (_PCI_DEVINST, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_MAX_LINK_SPEED): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(3),
        ),
        (_PCI_DEVINST, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_CURRENT_LINK_WIDTH): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(16),
        ),
        (_PCI_DEVINST, api.PCI_DEVICE_PROPERTY_FMTID, api.PCI_PROP_MAX_LINK_WIDTH): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(16),
        ),
        (_PCI_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_UINUMBER): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(1),
        ),
        (_PCI_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_BUSNUMBER): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(0),
        ),
        (_PCI_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes(0x00010000),
        ),
    }


@pytest.mark.os_agnostic
def test_enumerate_pci_reads_one_devices_identity_link_and_address() -> None:
    setupapi = FakeSetupApi(
        by_enumerator={"PCI": [_PCI_DEVINST]},
        properties=_pci_properties(),
    )
    cfgmgr32 = FakeCfgmgr32(device_ids={_PCI_DEVINST: _PCI_INSTANCE})
    entries = _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).enumerate_pci()
    entry = entries[_PCI_INSTANCE]
    assert entry["vendor"] == "0x8086"
    assert entry["device"] == "0xa182"
    assert entry["name"] == "Intel Root Port"
    assert entry["driver"] == "pci"
    assert entry["class"] == "0x060400"
    assert entry["current_link_speed"] == "8.0 GT/s PCIe"
    assert entry["current_link_width"] == "16"
    assert entry["address"] == "0000:00:01.0"
    assert "parent" not in entry
    assert "children" not in entry
    assert setupapi.destroyed == [1]


@pytest.mark.os_agnostic
def test_enumerate_pci_on_invalid_handle_value_yields_nothing_and_destroys_nothing() -> None:
    setupapi = FakeSetupApi(by_enumerator={})  # "PCI" is absent, so SetupDiGetClassDevsW refuses
    entries = _tree(setupapi=setupapi).enumerate_pci()
    assert entries == {}
    assert setupapi.destroyed == []


@pytest.mark.os_agnostic
def test_the_present_devices_loop_pins_current_behavior_of_stopping_on_any_failure() -> None:
    """Backlog [415]: a mid-list enumeration failure is indistinguishable from end-of-list.

    Three devices are present, but the second SetupDiEnumDeviceInfo call is made
    to fail as a transient error would (not because the list ended). The current
    code has no way to tell that apart from ERROR_NO_MORE_ITEMS, so it stops
    there and the third device is silently never read. This test pins that
    behaviour; it is backlog item [415] and is NOT fixed here.
    """
    setupapi = FakeSetupApi(
        by_enumerator={"PCI": [1, 2, 3]},
        properties={},
        fail_enum_at={1: 1},
    )
    cfgmgr32 = FakeCfgmgr32(device_ids={1: "PCI\\A", 2: "PCI\\B", 3: "PCI\\C"})
    entries = _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).enumerate_pci()
    assert set(entries) == {"PCI\\A"}


@pytest.mark.os_agnostic
def test_the_interfaces_loop_pins_current_behavior_of_stopping_on_any_failure() -> None:
    """The same backlog [415] shape, for the SetupDiEnumDeviceInterfaces loop."""
    setupapi = FakeSetupApi(
        by_guid={api.GUID_DEVINTERFACE_DISK: [("\\\\?\\disk0", 1), ("\\\\?\\disk1", 2)]},
        fail_enum_at={1: 1},
    )
    cfgmgr32 = FakeCfgmgr32(device_ids={1: "DISK\\A", 2: "DISK\\B"})
    found = _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).disk_interfaces()
    assert [path for path, _ in found] == ["\\\\?\\disk0"]


# ---------------------------------------------------------------------------
# _property / _uint_property / _string_property
# ---------------------------------------------------------------------------

_FMTID = api.DEVICE_PROPERTY_FMTID
_PID = api.DEVICE_PROP_FRIENDLYNAME
_DEVINST = 1
_DEVINST_ID = "PCI\\TEST_DEVICE"
_KEY: tuple[int, str, int] = (_DEVINST, _FMTID, _PID)


def _cfgmgr_for_devinst() -> FakeCfgmgr32:
    """A cfgmgr32 fake that resolves ``_DEVINST``'s own instance id, nothing else.

    ``_present_devices`` drops a device it cannot name (``_instance_id`` is
    ``None``), so every test that reaches a property through it needs this.
    """
    return FakeCfgmgr32(device_ids={_DEVINST: _DEVINST_ID})


def _read_uint(properties: dict[tuple[int, str, int], tuple[int, bytes]]) -> int | None:
    setupapi = FakeSetupApi(properties=properties)
    tree = _tree(setupapi=setupapi, cfgmgr32=_cfgmgr_for_devinst())
    handle, info, _ = next(iter(_present_one(tree, setupapi)))
    return tree._uint_property(handle, info, _FMTID, _PID)  # pyright: ignore[reportPrivateUsage] - this is the seam under test


def _read_string(properties: dict[tuple[int, str, int], tuple[int, bytes]]) -> str | None:
    setupapi = FakeSetupApi(properties=properties)
    tree = _tree(setupapi=setupapi, cfgmgr32=_cfgmgr_for_devinst())
    handle, info, _ = next(iter(_present_one(tree, setupapi)))
    return tree._string_property(handle, info, _FMTID, _PID)  # pyright: ignore[reportPrivateUsage] - this is the seam under test


def _present_one(tree: _DeviceTree, setupapi: FakeSetupApi) -> list[tuple[int, api.SP_DEVINFO_DATA, str]]:
    """Drive ``_present_devices`` for one devinst, so a property test gets a real handle and info."""
    setupapi.by_enumerator["PCI"] = [_DEVINST]
    return list(tree._present_devices("PCI"))  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_an_unpublished_property_reads_as_none() -> None:
    assert _read_uint({}) is None


@pytest.mark.os_agnostic
def test_a_uint_property_of_the_wrong_type_reads_as_none() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_STRING, _uint_bytes(42))}
    assert _read_uint(properties) is None


@pytest.mark.os_agnostic
def test_a_uint_property_shorter_than_four_bytes_reads_as_none() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_UINT32, b"\x01\x02")}
    assert _read_uint(properties) is None


@pytest.mark.os_agnostic
def test_a_well_formed_uint_property_reads_through() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_UINT32, _uint_bytes(7))}
    assert _read_uint(properties) == 7


@pytest.mark.os_agnostic
def test_a_string_property_of_the_wrong_type_reads_as_none() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_UINT32, _uint_bytes(1))}
    assert _read_string(properties) is None


@pytest.mark.os_agnostic
def test_a_string_property_past_max_device_text_is_refused() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_STRING, _string_bytes("a" * (MAX_DEVICE_TEXT + 1)))}
    assert _read_string(properties) is None


@pytest.mark.os_agnostic
def test_an_empty_string_property_reads_as_none() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_STRING, _string_bytes(""))}
    assert _read_string(properties) is None


@pytest.mark.os_agnostic
def test_a_well_formed_string_property_reads_through() -> None:
    properties = {_KEY: (api.DEVPROP_TYPE_STRING, _string_bytes("stornvme"))}
    assert _read_string(properties) == "stornvme"


# ---------------------------------------------------------------------------
# _pci_address
# ---------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_pci_address_with_both_bus_and_address_read_builds_the_familiar_string() -> None:
    properties: dict[tuple[int, str, int], tuple[int, bytes]] = {
        (_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_BUSNUMBER): (api.DEVPROP_TYPE_UINT32, _uint_bytes(2)),
        (_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS): (
            api.DEVPROP_TYPE_UINT32,
            _uint_bytes((3 << 16) | 1),
        ),
    }
    setupapi = FakeSetupApi(properties=properties)
    tree = _tree(setupapi=setupapi, cfgmgr32=_cfgmgr_for_devinst())
    handle, info, _ = next(iter(_present_one(tree, setupapi)))
    assert tree._pci_address(handle, info) == "0000:02:03.1"  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_pci_address_with_no_bus_read_returns_empty() -> None:
    properties: dict[tuple[int, str, int], tuple[int, bytes]] = {
        (_DEVINST, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS): (api.DEVPROP_TYPE_UINT32, _uint_bytes(1)),
    }
    setupapi = FakeSetupApi(properties=properties)
    tree = _tree(setupapi=setupapi, cfgmgr32=_cfgmgr_for_devinst())
    handle, info, _ = next(iter(_present_one(tree, setupapi)))
    assert tree._pci_address(handle, info) == ""  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_pci_address_with_neither_bus_nor_address_read_returns_empty() -> None:
    setupapi = FakeSetupApi()
    tree = _tree(setupapi=setupapi, cfgmgr32=_cfgmgr_for_devinst())
    handle, info, _ = next(iter(_present_one(tree, setupapi)))
    assert tree._pci_address(handle, info) == ""  # pyright: ignore[reportPrivateUsage] - this is the seam under test


# ---------------------------------------------------------------------------
# _present_devices / _interfaces on INVALID_HANDLE_VALUE
# ---------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_present_devices_on_invalid_handle_value_yields_nothing() -> None:
    tree = _tree(setupapi=FakeSetupApi(by_enumerator={}))
    assert list(tree._present_devices("PCI")) == []  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_interfaces_on_invalid_handle_value_returns_an_empty_list() -> None:
    tree = _tree(setupapi=FakeSetupApi(by_guid={}))
    assert tree._interfaces(api.GUID_DEVINTERFACE_DISK) == []  # pyright: ignore[reportPrivateUsage] - this is the seam under test


# ---------------------------------------------------------------------------
# _instance_id / _parent_instance / _ancestor_instances / _child_instances
# ---------------------------------------------------------------------------

_DISK = 1
_HUB = 2
_ROOT_HUB = 3


@pytest.mark.os_agnostic
def test_instance_id_of_an_unknown_devinst_is_none() -> None:
    tree = _tree()
    assert tree._instance_id(99) is None  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_parent_instance_of_a_rootless_device_is_none() -> None:
    tree = _tree(cfgmgr32=FakeCfgmgr32(device_ids={_DISK: "DISK\\1"}))
    assert tree._parent_instance(_DISK) is None  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_ancestor_instances_walks_the_whole_chain_nearest_first() -> None:
    cfgmgr32 = FakeCfgmgr32(
        device_ids={_DISK: "DISK\\1", _HUB: "USB\\HUB", _ROOT_HUB: "USB\\ROOT_HUB"},
        parents={_DISK: _HUB, _HUB: _ROOT_HUB},
    )
    tree = _tree(cfgmgr32=cfgmgr32)
    assert tree._ancestor_instances(_DISK) == ["USB\\HUB", "USB\\ROOT_HUB"]  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_ancestor_instances_stops_when_a_parent_has_no_readable_instance_id() -> None:
    """A parent devinst exists (CM_Get_Parent succeeds) but CM_Get_Device_IDW on it fails."""
    cfgmgr32 = FakeCfgmgr32(device_ids={_DISK: "DISK\\1"}, parents={_DISK: _HUB})
    tree = _tree(cfgmgr32=cfgmgr32)
    assert tree._ancestor_instances(_DISK) == []  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_ancestor_instances_is_bounded_against_a_parent_cycle() -> None:
    """Two devices naming each other's parent would loop forever without the depth bound."""
    cfgmgr32 = FakeCfgmgr32(
        device_ids={_DISK: "A", _HUB: "B"},
        parents={_DISK: _HUB, _HUB: _DISK},
    )
    tree = _tree(cfgmgr32=cfgmgr32)
    found = tree._ancestor_instances(_DISK)  # pyright: ignore[reportPrivateUsage] - this is the seam under test
    assert len(found) == _MAX_TREE_DEPTH
    assert found[0] == "B"
    assert found[1] == "A"


@pytest.mark.os_agnostic
def test_child_instances_walks_every_sibling() -> None:
    cfgmgr32 = FakeCfgmgr32(
        device_ids={10: "A", 11: "B", 12: "C"},
        children={_HUB: 10},
        siblings={10: 11, 11: 12},
    )
    tree = _tree(cfgmgr32=cfgmgr32)
    assert tree._child_instances(_HUB) == ["A", "B", "C"]  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_child_instances_of_a_childless_device_is_empty() -> None:
    tree = _tree(cfgmgr32=FakeCfgmgr32())
    assert tree._child_instances(_HUB) == []  # pyright: ignore[reportPrivateUsage] - this is the seam under test


@pytest.mark.os_agnostic
def test_child_instances_is_bounded_against_a_sibling_cycle() -> None:
    """A child whose sibling is itself would loop forever without the sibling-count bound."""
    cfgmgr32 = FakeCfgmgr32(
        device_ids={10: "A"},
        children={_HUB: 10},
        siblings={10: 10},
    )
    tree = _tree(cfgmgr32=cfgmgr32)
    found = tree._child_instances(_HUB)  # pyright: ignore[reportPrivateUsage] - this is the seam under test
    assert len(found) == _MAX_SIBLINGS
    assert set(found) == {"A"}


# ---------------------------------------------------------------------------
# disk_interfaces() / usb_devices() / usb_hubs(): the other real entry points
# that drive _interfaces, _interface_detail and _ancestor_instances together.
# ---------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_disk_interfaces_pairs_a_path_with_its_ancestor_chain() -> None:
    setupapi = FakeSetupApi(by_guid={api.GUID_DEVINTERFACE_DISK: [("\\\\?\\disk0", _DISK)]})
    cfgmgr32 = FakeCfgmgr32(
        device_ids={_DISK: "DISK\\1", _HUB: "USB\\HUB"},
        parents={_DISK: _HUB},
    )
    found = _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).disk_interfaces()
    assert found == [("\\\\?\\disk0", ["USB\\HUB"])]


@pytest.mark.os_agnostic
def test_usb_hubs_keys_every_present_hub_by_its_instance_id() -> None:
    setupapi = FakeSetupApi(by_guid={api.GUID_DEVINTERFACE_USB_HUB: [("\\\\?\\hub0", _HUB)]})
    cfgmgr32 = FakeCfgmgr32(device_ids={_HUB: "USB\\HUB"})
    found = _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).usb_hubs()
    assert found == {"USB\\HUB": "\\\\?\\hub0"}


@pytest.mark.os_agnostic
def test_usb_devices_reads_parent_port_and_service_for_every_present_device() -> None:
    properties: dict[tuple[int, str, int], tuple[int, bytes]] = {
        (_DISK, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_ADDRESS): (api.DEVPROP_TYPE_UINT32, _uint_bytes(3)),
        (_DISK, api.DEVICE_PROPERTY_FMTID, api.DEVICE_PROP_SERVICE): (
            api.DEVPROP_TYPE_STRING,
            _string_bytes("UASPStor"),
        ),
    }
    setupapi = FakeSetupApi(by_enumerator={"USB": [_DISK]}, properties=properties)
    cfgmgr32 = FakeCfgmgr32(device_ids={_DISK: "USB\\DISK"}, parents={_DISK: _HUB})
    cfgmgr32.device_ids[_HUB] = "USB\\HUB"
    found = _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).usb_devices()
    assert found["USB\\DISK"].parent == "USB\\HUB"
    assert found["USB\\DISK"].port == 3
    assert found["USB\\DISK"].service == "UASPStor"


# ---------------------------------------------------------------------------
# _pci_entry(): every field it writes, asserted exactly. One happy-path test
# above held the default case only; each test below varies ONE input.
# ---------------------------------------------------------------------------

_PCI_FMTID = api.PCI_DEVICE_PROPERTY_FMTID
_DEV_FMTID = api.DEVICE_PROPERTY_FMTID


def _uint(properties: dict[tuple[int, str, int], tuple[int, bytes]], fmtid: str, pid: int, value: int) -> None:
    """Set one UINT32 property of the PCI device under test."""
    properties[(_PCI_DEVINST, fmtid, pid)] = (api.DEVPROP_TYPE_UINT32, _uint_bytes(value))


def _entry_of(properties: dict[tuple[int, str, int], tuple[int, bytes]], **graph: Any) -> dict[str, Any]:
    """Run ``enumerate_pci`` over one device with the given properties and cfgmgr32 graph."""
    ids: dict[int, str] = {_PCI_DEVINST: _PCI_INSTANCE, **graph.pop("device_ids", {})}
    setupapi = FakeSetupApi(by_enumerator={"PCI": [_PCI_DEVINST]}, properties=properties)
    cfgmgr32 = FakeCfgmgr32(device_ids=ids, **graph)
    return _tree(setupapi=setupapi, cfgmgr32=cfgmgr32).enumerate_pci()[_PCI_INSTANCE]


@pytest.mark.os_agnostic
def test_a_current_link_width_of_zero_is_recorded_as_a_reading_not_dropped() -> None:
    properties = _pci_properties()
    _uint(properties, _PCI_FMTID, api.PCI_PROP_CURRENT_LINK_WIDTH, 0)
    entry = _entry_of(properties)
    assert entry["current_link_width"] == "0"
    assert entry["max_link_width"] == "16"


@pytest.mark.os_agnostic
def test_a_zero_width_link_read_off_the_device_tree_is_dead_once_built() -> None:
    """The reader's "0" must reach the domain as a dead link, not as an unread one."""
    properties = _pci_properties()
    _uint(properties, _PCI_FMTID, api.PCI_PROP_CURRENT_LINK_WIDTH, 0)
    entry = _entry_of(properties)
    payload = load_windows_capture()
    bridge = "PCI\\VEN_1B36&DEV_000C&SUBSYS_00001B36&REV_00\\3&11583659&0&E0"
    payload["pci"][bridge] = {**entry, "instance_id": bridge, "class": "0x060400", "address": "0000:00:1c.0"}
    slots = {slot.address: slot for slot in build_from(payload).slots}
    assert slots["0000:00:1c.0"].link.current_width == 0
    assert slots["0000:00:1c.0"].link.is_dead is True


def load_windows_capture() -> dict[str, Any]:
    """Load the committed Windows capture as a fresh dict a test may modify."""
    path = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"
    with path.open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


@pytest.mark.os_agnostic
def test_the_description_names_the_device_when_no_friendly_name_is_published() -> None:
    properties = _pci_properties()
    del properties[(_PCI_DEVINST, _DEV_FMTID, api.DEVICE_PROP_FRIENDLYNAME)]
    properties[(_PCI_DEVINST, _DEV_FMTID, api.DEVICE_PROP_DEVICEDESC)] = (
        api.DEVPROP_TYPE_STRING,
        _string_bytes("PCI Express Root Port"),
    )
    assert _entry_of(properties)["name"] == "PCI Express Root Port"


@pytest.mark.os_agnostic
def test_a_friendly_name_wins_over_the_description() -> None:
    properties = _pci_properties()
    properties[(_PCI_DEVINST, _DEV_FMTID, api.DEVICE_PROP_DEVICEDESC)] = (
        api.DEVPROP_TYPE_STRING,
        _string_bytes("the description"),
    )
    assert _entry_of(properties)["name"] == "Intel Root Port"


@pytest.mark.os_agnostic
def test_a_device_with_no_name_property_at_all_carries_no_name_key() -> None:
    properties = _pci_properties()
    del properties[(_PCI_DEVINST, _DEV_FMTID, api.DEVICE_PROP_FRIENDLYNAME)]
    assert "name" not in _entry_of(properties)


@pytest.mark.os_agnostic
def test_the_parent_and_every_child_are_recorded_by_instance_identifier() -> None:
    parent, first, second = 20, 21, 22
    entry = _entry_of(
        _pci_properties(),
        device_ids={parent: "PCI\\PARENT", first: "PCI\\FIRST", second: "PCI\\SECOND"},
        parents={_PCI_DEVINST: parent},
        children={_PCI_DEVINST: first},
        siblings={first: second},
    )
    assert entry["parent"] == "PCI\\PARENT"
    assert entry["children"] == ["PCI\\FIRST", "PCI\\SECOND"]


@pytest.mark.os_agnostic
def test_a_non_zero_programming_interface_completes_the_class_string() -> None:
    properties = _pci_properties()
    _uint(properties, _PCI_FMTID, api.PCI_PROP_BASE_CLASS, 0x01)
    _uint(properties, _PCI_FMTID, api.PCI_PROP_SUB_CLASS, 0x06)
    _uint(properties, _PCI_FMTID, api.PCI_PROP_PROG_IF, 0x01)
    assert _entry_of(properties)["class"] == "0x010601"


@pytest.mark.os_agnostic
def test_a_class_with_no_base_or_sub_class_published_carries_no_class_key() -> None:
    properties = _pci_properties()
    del properties[(_PCI_DEVINST, _PCI_FMTID, api.PCI_PROP_BASE_CLASS)]
    assert "class" not in _entry_of(properties)


@pytest.mark.os_agnostic
def test_the_address_carries_the_bus_the_device_and_the_function_exactly() -> None:
    properties = _pci_properties()
    _uint(properties, _DEV_FMTID, api.DEVICE_PROP_BUSNUMBER, 0x1A)
    _uint(properties, _DEV_FMTID, api.DEVICE_PROP_ADDRESS, (0x1C << 16) | 3)
    assert _entry_of(properties)["address"] == "0000:1a:1c.3"


@pytest.mark.os_agnostic
def test_the_slot_number_and_both_maximum_link_figures_are_recorded() -> None:
    properties = _pci_properties()
    _uint(properties, _PCI_FMTID, api.PCI_PROP_MAX_LINK_SPEED, 4)
    _uint(properties, _PCI_FMTID, api.PCI_PROP_MAX_LINK_WIDTH, 8)
    _uint(properties, _DEV_FMTID, api.DEVICE_PROP_UINUMBER, 7)
    entry = _entry_of(properties)
    assert entry["slot_number"] == 7
    assert entry["max_link_speed"] == "16.0 GT/s PCIe"
    assert entry["max_link_width"] == "8"


@pytest.mark.os_agnostic
def test_an_unknown_link_speed_code_leaves_the_speed_key_absent() -> None:
    properties = _pci_properties()
    _uint(properties, _PCI_FMTID, api.PCI_PROP_CURRENT_LINK_SPEED, 99)
    _uint(properties, _PCI_FMTID, api.PCI_PROP_MAX_LINK_SPEED, 0)
    entry = _entry_of(properties)
    assert "current_link_speed" not in entry
    assert "max_link_speed" not in entry


@pytest.mark.os_agnostic
def test_a_slot_number_of_zero_is_a_reading_not_a_missing_one() -> None:
    properties = _pci_properties()
    _uint(properties, _DEV_FMTID, api.DEVICE_PROP_UINUMBER, 0)
    assert _entry_of(properties)["slot_number"] == 0


@pytest.mark.os_agnostic
def test_a_device_publishing_no_slot_number_carries_no_slot_number_key() -> None:
    properties = _pci_properties()
    properties.pop((_PCI_DEVINST, _DEV_FMTID, api.DEVICE_PROP_UINUMBER), None)
    assert "slot_number" not in _entry_of(properties)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("published", "kept"), [(0, 0), (0x1FFF, 0x1FFF), (0x2000, None), (0xFFFFFFFF, None)])
def test_a_windows_slot_number_is_bounded_like_the_linux_one(published: int, kept: int | None) -> None:
    """The domain's slot number is the 13-bit field Linux reads; a wider UINumber is not one."""
    payload = load_windows_capture()
    bridge = "PCI\\VEN_1B36&DEV_000C&SUBSYS_00001B36&REV_00\\3&11583659&0&E0"
    payload["pci"][bridge] = {
        "instance_id": bridge,
        "class": "0x060400",
        "address": "0000:00:1c.0",
        "slot_number": published,
    }
    slots = {slot.address: slot for slot in build_from(payload).slots}
    assert slots["0000:00:1c.0"].physical_slot_number == kept
