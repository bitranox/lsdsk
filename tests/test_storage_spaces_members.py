"""The drives inside a Storage Spaces pool are read, and each is linked to the Space it backs.

Windows moves a pooled drive's device interface from ``GUID_DEVINTERFACE_DISK`` to
``GUID_DEVINTERFACE_HIDDEN_DISK``, so a reader listing only the first class never sees it
(ADR 0007). These tests drive the reader through the same SetupAPI and cfgmgr32 fakes the
device-tree tests use, so the enumeration and the property reads run on every runner.
"""

from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass, field
from operator import attrgetter
from typing import TYPE_CHECKING, Any, cast

import pytest
from test_refused_readings import envelope_of
from test_windows_device_tree import FakeCfgmgr32, FakeSetupApi
from test_windows_device_tree import _tree as _wired_tree  # pyright: ignore[reportPrivateUsage]
from test_windows_volume_reader import FakeVolume, FakeVolumeKernel

from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.reader import DiskInterface

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

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


# ---------------------------------------------------------------------------
# A drive under BOTH interface classes is one drive, read once - and the record
# kept for it still carries what its hidden twin said, which is the only place
# the Space it backs is named (ADR 0007, decision 1).
# ---------------------------------------------------------------------------

_BOTH_DISK_PATH = "\\\\?\\scsi#disk&ven_qemu&prod_harddisk#6&2b320803&0&010000#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
_SPACE_PATH = "\\\\?\\storage#disk#{69486059-9506-4070-8b3a-c7a565ee19ab}#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
_SPACE_AS_NAMED = "STORAGE\\Disk\\{69486059-9506-4070-8b3a-c7a565ee19ab}"
_POOLED_INSTANCE = "SCSI\\DISK&VEN_QEMU&PROD_HARDDISK\\6&2B320803&0&010000"
_BUS_SATA = 0x0B
_BUS_SPACES = 0x10
_VOLUME_D = "\\\\?\\Volume{d}\\"


#: A ``ctypes.byref()`` proxy's referent, the structure a fake reads and writes in the caller's place.
_referent = attrgetter("_obj")


@dataclass
class _DisksAndVolumesKernel(FakeVolumeKernel):
    """The volume reader's ``kernel32``, which also opens disks and answers their number and transport.

    The two Win32 entry points are bound to their Win32 names below rather than
    defined under them, which is all the name has to be for the reader to call it.

    Attributes:
        disks: Each disk's ``PhysicalDrive`` number and ``STORAGE_BUS_TYPE``, keyed by its interface path.
            Every other disk ioctl is refused, so a disk's health is recorded as refused.
    """

    disks: dict[str, tuple[int, int]] = field(default_factory=dict[str, tuple[int, int]])
    _disk_handles: dict[int, str] = field(default_factory=dict[int, str])

    def _create_file(
        self, path: str, access: int, share: int, security: object, disposition: int, flags: int, template: object
    ) -> int:
        """Open a disk this fake knows, and anything else as the volume fake does."""
        if path not in self.disks:
            return super().CreateFileW(path, access, share, security, disposition, flags, template)
        handle = 500 + len(self._disk_handles)
        self._disk_handles[handle] = path
        return handle

    def _device_io_control(
        self,
        handle: int,
        code: int,
        in_buffer: object,
        in_size: int,
        out_buffer: ctypes.Array[ctypes.c_char],
        out_size: int,
        returned: object,
        overlapped: object,
    ) -> int:
        """Answer a disk's number and descriptor, refuse its other ioctls, and pass a volume's on."""
        path = self._disk_handles.get(handle)
        if path is None:
            return super().DeviceIoControl(handle, code, in_buffer, in_size, out_buffer, out_size, returned, overlapped)
        number, bus = self.disks[path]
        if code == api.IOCTL_STORAGE_GET_DEVICE_NUMBER:
            _referent(out_buffer).DeviceNumber = number
            return 1
        asks_descriptor = code == api.IOCTL_STORAGE_QUERY_PROPERTY and hasattr(in_buffer, "_obj")
        if not asks_descriptor or _referent(in_buffer).PropertyId != api.STORAGE_DEVICE_PROPERTY:
            self.error = 1
            return 0
        descriptor = api.STORAGE_DEVICE_DESCRIPTOR()
        descriptor.Size = ctypes.sizeof(descriptor)
        descriptor.BusType = bus
        raw = bytes(descriptor)
        ctypes.memmove(out_buffer, raw, min(len(raw), out_size))
        _referent(returned).value = len(raw)
        return 1

    CreateFileW = _create_file
    DeviceIoControl = _device_io_control


def _read_a_drive_under_both_classes() -> dict[str, Any]:
    """Run ``read_system`` over a machine whose one pooled drive publishes both disk classes.

    The ordinary class lists the drive and the Space; the hidden class lists the same
    drive again, as the same device, naming the Space among its removal relations.
    The Space carries a lettered volume. Disks are read by the production reader.
    """
    setupapi = FakeSetupApi(
        by_guid={
            api.GUID_DEVINTERFACE_DISK: [(_BOTH_DISK_PATH, _POOLED), (_SPACE_PATH, _SPACE)],
            api.GUID_DEVINTERFACE_HIDDEN_DISK: [(_POOLED_PATH, _POOLED)],
        },
        properties=_relations(_POOLED, _SPACE_AS_NAMED),
    )
    kernel = _DisksAndVolumesKernel(
        {_VOLUME_D: FakeVolume(paths=("D:\\",), disks=(4,))},
        disks={_BOTH_DISK_PATH: (1, _BUS_SATA), _POOLED_PATH: (1, _BUS_SATA), _SPACE_PATH: (4, _BUS_SPACES)},
    )

    def tree() -> reader._DeviceTree:  # pyright: ignore[reportPrivateUsage] - the seam under test
        return reader._DeviceTree(  # pyright: ignore[reportPrivateUsage] - the seam under test
            setupapi=cast("api.WinLibrary", setupapi),
            cfgmgr32=cast(
                "api.WinLibrary", FakeCfgmgr32(device_ids={_POOLED: _POOLED_INSTANCE, _SPACE: _SPACE_INSTANCE})
            ),
            kernel32=cast("api.WinLibrary", kernel),
            last_error=lambda: kernel.error or setupapi.error,
        )

    return reader.read_system(
        reader.SystemReaders(tree=tree, elevated=lambda: False, environment=dict, pci_names=lambda _pci: {})
    )


def test_a_drive_under_both_disk_classes_is_read_once() -> None:
    reading = _read_a_drive_under_both_classes()

    numbered = sorted(record["node"] for record in reading["disks"].values() if "node" in record)
    assert numbered == ["PhysicalDrive1", "PhysicalDrive4"], list(reading["disks"])


def test_the_record_kept_for_a_drive_under_both_classes_carries_its_hidden_twins_link() -> None:
    """The ordinary record is kept, so what only the hidden twin read must reach it, or the link is lost."""
    reading = _read_a_drive_under_both_classes()

    kept = reading["disks"][_BOTH_DISK_PATH]
    assert (kept.get("hidden"), kept.get("removal_relations"), kept.get("instance")) == (
        True,
        [_SPACE_AS_NAMED],
        _POOLED_INSTANCE,
    )


def test_a_drive_under_both_classes_is_used_by_the_space_it_backs(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """End to end: the reading replayed, the drive is used by its Space and the Space refuses nothing."""
    capture = tmp_path / "both-classes.json"
    capture.write_text(json.dumps(_read_a_drive_under_both_classes()), encoding="utf-8")

    envelope = envelope_of(capture, cli_runner, production_factory)

    disks = {disk["node"]: disk for disk in (*envelope["data"]["disks"], *envelope["data"]["virtual_disks"])}
    assert disks["PhysicalDrive1"]["usage"]["uses"] == [{"kind": "space", "name": "PhysicalDrive4", "mounts": ["D:\\"]}]
    assert disks["PhysicalDrive4"]["readings_refused"] == [], "the Space's drive was read, so nothing is missing"
