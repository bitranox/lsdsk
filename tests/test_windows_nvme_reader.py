"""An NVMe read Windows refuses is recorded, and reaches the caller as a refused reading.

``read_ata`` has always written ``<label>_error`` for a command that failed; the
NVMe half returned only payload keys, so a refused identify or health log left
``ok`` true and ``skipped`` empty on a privileged run. These tests drive the real
``read_disk`` through a fake ``kernel32`` - the edge it talks to - and then the
real Windows builder, so the refusal is followed all the way to the disk.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api

if TYPE_CHECKING:
    from collections.abc import Callable

    from lsdsk.domain.models import Inventory

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"
_NVME_BUS = 0x11
_DISK_PATH = "\\\\?\\nvme#disk#planted"

# Where the protocol-specific structure sits in a protocol query's buffer, and
# where the data follows it: the same two offsets the reader computes.
_PROTOCOL_AT = api.STORAGE_PROPERTY_QUERY.AdditionalParameters.offset
_DATA_AT = _PROTOCOL_AT + ctypes.sizeof(api.STORAGE_PROTOCOL_SPECIFIC_DATA)


def _referent(value: object) -> Any:
    """Return the structure a ``ctypes.byref()`` proxy wraps."""
    return getattr(value, "_obj")  # noqa: B009 - the documented way to reach a byref's referent


class FakeNvmeKernel32:
    """``kernel32`` for one NVMe disk.

    Attributes:
        answer: What the protocol query returns for ``(data_type)``: the payload bytes, or
            ``None`` for an ioctl failure.
        opens_for_passthrough: Whether ``CreateFileW`` grants the read-write access passthrough needs.
    """

    def __init__(
        self,
        *,
        answer: Callable[[int], bytes | None],
        opens_for_passthrough: bool = True,
    ) -> None:
        """Answer protocol queries through ``answer``."""
        self.answer = answer
        self.opens_for_passthrough = opens_for_passthrough

    def CreateFileW(self, path: str, access: int, *_rest: object) -> int:  # noqa: N802 - the Win32 name
        del path
        if access and not self.opens_for_passthrough:
            return cast("int", api.INVALID_HANDLE_VALUE)
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
        if code == api.IOCTL_STORAGE_GET_DEVICE_NUMBER:
            _referent(out_buffer).DeviceNumber = 3
            return 1
        if code == api.IOCTL_DISK_GET_LENGTH_INFO:
            _referent(out_buffer).value = 1_000_204_886_016
            _referent(returned).value = 8
            return 1
        if code != api.IOCTL_STORAGE_QUERY_PROPERTY:
            return 0
        if hasattr(in_buffer, "_obj"):
            return self._plain_property(_referent(in_buffer).PropertyId, out_buffer, out_size, returned)
        return self._protocol_query(cast("ctypes.Array[ctypes.c_char]", in_buffer), returned)

    @staticmethod
    def _plain_property(property_id: int, out_buffer: object, out_size: int, returned: object) -> int:
        """Answer the device descriptor with an NVMe bus type, and nothing else."""
        if property_id != api.STORAGE_DEVICE_PROPERTY:
            return 0
        descriptor = api.STORAGE_DEVICE_DESCRIPTOR()
        descriptor.Size = ctypes.sizeof(descriptor)
        descriptor.BusType = _NVME_BUS
        raw = bytes(descriptor)
        ctypes.memmove(cast("ctypes.Array[ctypes.c_char]", out_buffer), raw, min(len(raw), out_size))
        _referent(returned).value = len(raw)
        return 1

    def _protocol_query(self, buffer: ctypes.Array[ctypes.c_char], returned: object) -> int:
        """Answer one protocol-specific query: the payload after the structure, or a failure."""
        protocol = api.STORAGE_PROTOCOL_SPECIFIC_DATA.from_buffer(buffer, _PROTOCOL_AT)
        payload = self.answer(protocol.DataType)
        if payload is None:
            return 0
        ctypes.memmove(ctypes.addressof(buffer) + _DATA_AT, payload, len(payload))
        _referent(returned).value = _DATA_AT + len(payload)
        return 1


def _full(data_type: int) -> bytes:
    """A whole structure of the size asked for."""
    size = 4096 if data_type == api.NVME_DATA_TYPE_IDENTIFY else 512
    return bytes([0x5A]) * size


def _read(fake: FakeNvmeKernel32) -> dict[str, Any]:
    return reader.read_disk(cast("api.WinLibrary", fake), _DISK_PATH, [])


def _inventory_with(entry: dict[str, Any]) -> Inventory:
    """The committed Windows capture with its one disk replaced by a reading from ``read_disk``."""
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["disks"] = {_DISK_PATH: copy.deepcopy(entry)}
    return build_from(payload)


@pytest.mark.os_agnostic
def test_a_refused_nvme_query_is_recorded_under_both_error_keys() -> None:
    record = _read(FakeNvmeKernel32(answer=lambda _type: None))["nvme"]
    assert record["identify_controller_error"].startswith("Win32 error")
    assert record["smart_log_error"].startswith("Win32 error")
    assert "identify_controller" not in record
    assert "smart_log" not in record


@pytest.mark.os_agnostic
def test_an_answered_nvme_query_records_its_payload_and_no_error() -> None:
    record = _read(FakeNvmeKernel32(answer=_full))["nvme"]
    assert base64.b64decode(record["identify_controller"]) == _full(api.NVME_DATA_TYPE_IDENTIFY)
    assert base64.b64decode(record["smart_log"]) == _full(api.NVME_DATA_TYPE_LOG_PAGE)
    assert not [key for key in record if key.endswith("_error")]


@pytest.mark.os_agnostic
def test_one_refused_nvme_page_does_not_hide_the_other() -> None:
    only_identify = FakeNvmeKernel32(answer=lambda data_type: _full(data_type) if data_type == 1 else None)
    record = _read(only_identify)["nvme"]
    assert "identify_controller" in record
    assert "identify_controller_error" not in record
    assert record["smart_log_error"].startswith("Win32 error")


@pytest.mark.os_agnostic
def test_a_device_opened_without_passthrough_says_it_needs_administrator() -> None:
    record = _read(FakeNvmeKernel32(answer=lambda _type: None, opens_for_passthrough=False))["nvme"]
    assert "Administrator" in record["identify_controller_error"]
    assert "Administrator" in record["smart_log_error"]


@pytest.mark.os_agnostic
def test_a_refused_nvme_read_reaches_the_disk_as_a_refused_reading() -> None:
    entry = _read(FakeNvmeKernel32(answer=lambda _type: None))
    refused = {reading.reading for reading in _inventory_with(entry).disks[0].readings_refused}
    assert {"identify-controller", "smart-log"} <= refused


@pytest.mark.os_agnostic
def test_a_fully_answered_nvme_read_refuses_nothing() -> None:
    entry = _read(FakeNvmeKernel32(answer=_full))
    assert _inventory_with(entry).disks[0].readings_refused == ()
