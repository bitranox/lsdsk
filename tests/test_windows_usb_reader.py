"""A USB disk's hub is asked about the port the disk is plugged into, on Windows.

The answers are the hub's own buffers, recorded raw: the speed the port runs,
what it and the device in it speak, the socket it belongs to, and the device's
BOS. Decoding them is the builder's job. These tests drive the reader through a
fake ``kernel32``, the edge it talks to, with the buffers a real hub returned for
a 10 Gb/s UAS SSD sitting behind a USB 2 hub, so the request shapes and the
bookkeeping are held on every runner rather than only on the one machine with
such a disk.
"""

from __future__ import annotations

import base64
import ctypes
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.decode import usb
from lsdsk.adapters.hw.windows import reader
from lsdsk.adapters.hw.windows import winapi as api
from lsdsk.adapters.hw.windows.capture import UsbHubEntry, UsbPortEntry

# The device tree above the disk, nearest first: the SSD in port 3 of a USB 2
# hub, which sits in port 13 of the xHCI root hub. The SSD's own instance ID
# ends in its serial, so a made-up one of the same width stands in for it.
SSD = "USB\\VID_0781&PID_558C\\TESTSERIAL0123456789"
SECOND_SSD = "USB\\VID_0781&PID_558C\\TESTSERIAL9876543210"
USB2_HUB = "USB\\VID_05E3&PID_0608\\5&CC3F949&0&13"
ROOT_HUB = "USB\\ROOT_HUB30\\4&2FD48294&0&0"
HOST_CONTROLLER = "PCI\\VEN_8086&DEV_7AE0&SUBSYS_86941043&REV_11\\3&11583659&0&A0"
USB2_HUB_PATH = "\\\\?\\usb#vid_05e3&pid_0608#5&cc3f949&0&13#{f18a0e88-c30c-11d0-8815-00a0c906bed8}"
ROOT_HUB_PATH = "\\\\?\\usb#root_hub30#4&2fd48294&0&0#{f18a0e88-c30c-11d0-8815-00a0c906bed8}"

DEVICES = {
    SSD: reader.UsbDeviceFacts(parent=USB2_HUB, port=3, service="UASPStor"),
    SECOND_SSD: reader.UsbDeviceFacts(parent=USB2_HUB, port=4, service="USBSTOR"),
    USB2_HUB: reader.UsbDeviceFacts(parent=ROOT_HUB, port=13, service="USBHUB3"),
    ROOT_HUB: reader.UsbDeviceFacts(parent=HOST_CONTROLLER, port=None, service="USBHUB3"),
}
HUBS = {USB2_HUB: USB2_HUB_PATH, ROOT_HUB: ROOT_HUB_PATH}

# What the real hubs answered, measured on Windows 11 with the step-0 probe.
SSD_CONNECTION = bytes.fromhex(
    "03000000120110020000004081078c551210020301010102000a0004000000010000000705810200020000000000"
    "070502020002000000000007058302000200000000000705040200020000000000"
)
SSD_CONNECTION_V2 = bytes.fromhex("0300000010000000030000000a000000")
SSD_CONNECTOR = bytes.fromhex("030000001200000001000000000000000000")
SSD_BOS = bytes.fromhex("050f2a00030710021ef400000a1003000e00010aff0714100a00010000000011000030400a00b0400a00")
HUB_CONNECTION = bytes.fromhex(
    "0d0000001201000209000140e3050806706000010001010201050001000000010000000705810301000c00000000"
)
HUB_CONNECTION_V2 = bytes.fromhex("0d000000100000000300000000000000")
HUB_CONNECTOR = bytes.fromhex("0d0000001200000000000000000000000000")
USB2_HUB_INFORMATION = bytes.fromhex("020000000400092904e000326400ff00") + bytes(60)
ROOT_HUB_INFORMATION = bytes.fromhex("0100000019000000") + bytes(68)

#: The Win32 error a USB 2 hub's STALL of GET_DESCRIPTOR(BOS) came back as.
ERROR_GEN_FAILURE = 31
#: What a port not running SuperSpeedPlus answers the SuperSpeedPlus request with.
ERROR_INVALID_PARAMETER = 87

#: One hub's answers: IOCTL code to port to the buffer, or ``None`` for a refusal.
HubAnswers = dict[int, dict[int, bytes | None]]


def _answers() -> dict[str, HubAnswers]:
    """Every answer the two real hubs gave, keyed by the hub's interface path."""
    return {
        USB2_HUB_PATH: {
            api.IOCTL_USB_GET_HUB_INFORMATION_EX: {0: USB2_HUB_INFORMATION},
            api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX: {3: SSD_CONNECTION, 4: SSD_CONNECTION},
            api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2: {3: SSD_CONNECTION_V2, 4: SSD_CONNECTION_V2},
            api.IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES: {3: SSD_CONNECTOR, 4: SSD_CONNECTOR},
            api.IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION: {3: None, 4: None},
            api.IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION: {3: SSD_BOS, 4: SSD_BOS},
        },
        ROOT_HUB_PATH: {
            api.IOCTL_USB_GET_HUB_INFORMATION_EX: {0: ROOT_HUB_INFORMATION},
            api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX: {13: HUB_CONNECTION},
            api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2: {13: HUB_CONNECTION_V2},
            api.IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES: {13: HUB_CONNECTOR},
            api.IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION: {13: None},
            api.IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION: {13: None},
        },
    }


class FakeHubKernel:
    """``kernel32`` as the hub reader sees it: open a hub, ask it, close it.

    Each hub answers from a table keyed by IOCTL code and by the port the
    request names in its first four bytes. A descriptor request is answered
    the way a hub answers one: its own 12-byte header, then as much of the
    descriptor as its ``wLength`` asked for. An answer larger than the buffer
    offered fails, as the real IOCTL does, so an undersized buffer is caught.
    """

    def __init__(self, answers: dict[str, HubAnswers], *, unopenable: frozenset[str] = frozenset()) -> None:
        """Answer from ``answers``; refuse to open any path in ``unopenable``."""
        self.answers = answers
        self.unopenable = unopenable
        self.opened: list[tuple[str, int, int, int]] = []
        self.closed: list[int] = []
        self.calls: list[tuple[str, int, int, int]] = []
        self._paths: dict[int, str] = {}

    def CreateFileW(  # noqa: N802 - the Win32 entry point's own name
        self, path: str, access: int, share: int, security: object, disposition: int, flags: int, template: object
    ) -> int:
        del security, flags, template
        self.opened.append((path, access, share, disposition))
        if path in self.unopenable:
            return cast("int", api.INVALID_HANDLE_VALUE)
        handle = 100 + len(self._paths)
        self._paths[handle] = path
        return handle

    def CloseHandle(self, handle: int) -> int:  # noqa: N802 - the Win32 entry point's own name
        self.closed.append(handle)
        return 1

    def DeviceIoControl(  # noqa: N802 - the Win32 entry point's own name
        self,
        handle: int,
        code: int,
        in_buffer: ctypes.Array[ctypes.c_char],
        in_size: int,
        out_buffer: ctypes.Array[ctypes.c_char],
        out_size: int,
        returned: object,
        overlapped: object,
    ) -> int:
        del overlapped
        request = in_buffer.raw[:in_size]
        port = int.from_bytes(request[:4], "little") if in_size else 0
        path = self._paths[handle]
        self.calls.append((path, code, in_size, port))
        answer = self.answers[path].get(code, {}).get(port)
        if answer is not None and code == api.IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION:
            answer = request[:12] + answer[: int.from_bytes(request[10:12], "little")]
        if answer is None or len(answer) > out_size:
            return 0
        ctypes.memmove(out_buffer, answer, len(answer))
        getattr(returned, "_obj").value = len(answer)  # noqa: B009 - a CArgObject's referent
        return 1


def _disk(*ancestors: str) -> dict[str, Any]:
    """A disk's reading as ``read_disk`` leaves it, holding only what the USB pass reads."""
    return {"ancestors": list(ancestors)}


def _decoded(entry: dict[str, Any], field: str) -> bytes | None:
    """One recorded buffer, back from base64."""
    value = entry.get(field)
    return None if value is None else base64.b64decode(value)


def _read(kernel: FakeHubKernel, disks: dict[str, dict[str, Any]]) -> reader.UsbReading:
    return reader.read_usb_ports(cast("api.WinLibrary", kernel), disks, DEVICES, HUBS)


@pytest.mark.os_agnostic
def test_a_usb_disk_s_port_records_every_answer_its_hub_gave() -> None:
    kernel = FakeHubKernel(_answers())

    reading = _read(kernel, {"disk0": _disk("SCSI\\DISK&VEN_SANDISK\\7&1", SSD, USB2_HUB, ROOT_HUB)})

    port = reading.ports[SSD]
    assert (port["hub"], port["port"], port["service"]) == (USB2_HUB, 3, "UASPStor")
    assert _decoded(port, "connection") == SSD_CONNECTION
    assert _decoded(port, "connection_v2") == SSD_CONNECTION_V2
    assert _decoded(port, "connector") == SSD_CONNECTOR
    assert _decoded(port, "bos") == SSD_BOS, "the BOS is recorded without the hub's request header"
    assert usb.decode_bos(SSD_BOS).fastest is not None, "the vector is not a BOS that declares a speed"
    # Refused because the port is not running SuperSpeedPlus: an answer, not a failure.
    assert port["superspeedplus"] is None
    assert port["error"] is None
    assert reading.disk_errors == {}
    # The capture model takes exactly what the reader wrote.
    assert UsbPortEntry.model_validate(port).port == 3


@pytest.mark.os_agnostic
def test_every_hub_between_the_disk_and_the_root_is_asked_about_its_own_port() -> None:
    kernel = FakeHubKernel(_answers())

    reading = _read(kernel, {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB, HOST_CONTROLLER)})

    assert set(reading.ports) == {SSD, USB2_HUB}, "the root hub sits in no port, so it has no port record"
    hub_port = reading.ports[USB2_HUB]
    assert (hub_port["hub"], hub_port["port"]) == (ROOT_HUB, 13)
    assert _decoded(hub_port, "connection_v2") == HUB_CONNECTION_V2
    # A USB 2 hub STALLs GET_DESCRIPTOR(BOS): it has none, which is an answer.
    assert hub_port["bos"] is None
    assert hub_port["error"] is None
    assert {hub: _decoded(entry, "information") for hub, entry in reading.hubs.items()} == {
        USB2_HUB: USB2_HUB_INFORMATION,
        ROOT_HUB: ROOT_HUB_INFORMATION,
    }
    assert all(UsbHubEntry.model_validate(entry).error is None for entry in reading.hubs.values())


@pytest.mark.os_agnostic
def test_each_request_is_the_size_its_ioctl_accepts() -> None:
    """The sizes are measured: one byte short and the hub answers ERROR_INVALID_PARAMETER."""
    kernel = FakeHubKernel(_answers())

    _read(kernel, {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)})

    sizes = {(code, in_size) for path, code, in_size, port in kernel.calls if (path, port) in {(USB2_HUB_PATH, 3)}}
    assert sizes == {
        (api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX, len(usb.connection_request(3))),
        (api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2, len(usb.connection_v2_request(3))),
        (api.IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES, len(usb.connector_request(3))),
        (api.IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION, len(usb.superspeedplus_request(3))),
        (api.IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION, len(usb.bos_request(3, 5))),
    }
    assert (len(usb.connection_v2_request(3)), len(usb.connector_request(3))) == (16, 18)
    assert len(usb.superspeedplus_request(3)) == 24
    hub_information = [call for call in kernel.calls if call[1] == api.IOCTL_USB_GET_HUB_INFORMATION_EX]
    assert {in_size for _path, _code, in_size, _port in hub_information} == {0}


@pytest.mark.os_agnostic
def test_a_hub_is_opened_the_way_its_ioctls_require() -> None:
    kernel = FakeHubKernel(_answers())

    _read(kernel, {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)})

    assert kernel.opened
    assert {(access, share, disposition) for _path, access, share, disposition in kernel.opened} == {
        (api.GENERIC_WRITE, api.FILE_SHARE_WRITE, api.OPEN_EXISTING)
    }
    assert sorted(kernel.closed) == [100, 101], "every hub opened is closed again"


@pytest.mark.os_agnostic
def test_a_hub_shared_by_two_disks_is_opened_and_asked_once() -> None:
    kernel = FakeHubKernel(_answers())

    reading = _read(
        kernel,
        {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB), "disk1": _disk(SECOND_SSD, USB2_HUB, ROOT_HUB)},
    )

    assert [path for path, *_ in kernel.opened] == [USB2_HUB_PATH, ROOT_HUB_PATH]
    asked = [
        (path, port)
        for path, code, _size, port in kernel.calls
        if code == api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX
    ]
    assert sorted(asked) == sorted([(USB2_HUB_PATH, 3), (USB2_HUB_PATH, 4), (ROOT_HUB_PATH, 13)])
    assert reading.ports[SECOND_SSD]["service"] == "USBSTOR"


@pytest.mark.os_agnostic
def test_a_hub_that_will_not_open_marks_the_disk_s_link_unread() -> None:
    kernel = FakeHubKernel(_answers(), unopenable=frozenset({USB2_HUB_PATH}))

    reading = _read(kernel, {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)})

    assert reading.disk_errors["disk0"].startswith("Win32 error"), reading.disk_errors
    assert reading.ports[SSD]["error"] == reading.disk_errors["disk0"]
    assert reading.ports[SSD]["connection"] is None
    assert reading.hubs[USB2_HUB]["error"] == reading.disk_errors["disk0"]
    assert reading.hubs[USB2_HUB]["information"] is None
    # The hub above it opened fine, so ITS port was still read.
    assert reading.ports[USB2_HUB]["error"] is None


@pytest.mark.os_agnostic
def test_a_port_whose_connection_query_fails_marks_the_disk_s_link_unread() -> None:
    answers = _answers()
    answers[USB2_HUB_PATH][api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX][3] = None
    kernel = FakeHubKernel(answers)

    reading = _read(kernel, {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)})

    assert reading.disk_errors["disk0"].startswith("Win32 error"), reading.disk_errors
    assert reading.ports[SSD]["error"] == reading.disk_errors["disk0"]
    asked_after = [code for path, code, _size, port in kernel.calls if (path, port) == (USB2_HUB_PATH, 3)]
    assert asked_after == [api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX], (
        "a port that did not answer is asked nothing more"
    )


@pytest.mark.os_agnostic
def test_a_failure_above_the_disk_s_own_port_is_not_the_disk_s_refusal() -> None:
    """Only the disk's OWN port decides whether its link was read; a hub above it is context."""
    answers = _answers()
    answers[ROOT_HUB_PATH][api.IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX][13] = None
    kernel = FakeHubKernel(answers)

    reading = _read(kernel, {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)})

    assert reading.ports[USB2_HUB]["error"] is not None
    assert reading.disk_errors == {}


@pytest.mark.os_agnostic
def test_a_disk_with_no_usb_device_above_it_asks_no_hub() -> None:
    kernel = FakeHubKernel(_answers())

    reading = _read(kernel, {"disk0": _disk("PCI\\VEN_8086&DEV_7AE2\\3&1", HOST_CONTROLLER)})

    assert (reading.ports, reading.hubs, reading.disk_errors) == ({}, {}, {})
    assert kernel.opened == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        pytest.param(reader.UsbDeviceFacts(parent=USB2_HUB, port=None, service=None), "no port number", id="unread"),
        pytest.param(reader.UsbDeviceFacts(parent=USB2_HUB, port=0, service=None), "port number 0", id="zero"),
        pytest.param(reader.UsbDeviceFacts(parent=USB2_HUB, port=256, service=None), "port number 256", id="too high"),
    ],
)
def test_a_port_number_no_hub_can_have_is_not_asked(facts: reader.UsbDeviceFacts, reason: str) -> None:
    """A port outside 1-255 cannot be asked for and cannot be stored, so it is recorded as unread."""
    kernel = FakeHubKernel(_answers())
    devices = {**DEVICES, SSD: facts}

    reading = reader.read_usb_ports(
        cast("api.WinLibrary", kernel), {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)}, devices, HUBS
    )

    assert reason in reading.disk_errors["disk0"], reading.disk_errors
    assert SSD not in reading.ports, "a port record must carry a port number the capture model accepts"
    assert USB2_HUB_PATH not in {path for path, *_ in kernel.opened}, "the hub was opened to ask a port it cannot have"


@pytest.mark.os_agnostic
def test_a_usb_disk_whose_hub_publishes_no_interface_says_its_link_was_not_read() -> None:
    kernel = FakeHubKernel(_answers())

    reading = reader.read_usb_ports(
        cast("api.WinLibrary", kernel), {"disk0": _disk(SSD, USB2_HUB, ROOT_HUB)}, DEVICES, {ROOT_HUB: ROOT_HUB_PATH}
    )

    assert "no hub interface" in reading.disk_errors["disk0"], reading.disk_errors
    assert SSD not in reading.ports


@pytest.mark.os_agnostic
def test_a_composite_device_s_interface_is_not_taken_for_the_port() -> None:
    """One interface of a composite device hangs off the device, which is what sits in the port."""
    interface = "USB\\VID_0781&PID_558C&MI_00\\8&2A1B3C4D&0&0000"
    devices = {**DEVICES, interface: reader.UsbDeviceFacts(parent=SSD, port=None, service="UASPStor")}
    kernel = FakeHubKernel(_answers())

    reading = reader.read_usb_ports(
        cast("api.WinLibrary", kernel), {"disk0": _disk(interface, SSD, USB2_HUB, ROOT_HUB)}, devices, HUBS
    )

    assert reading.disk_errors == {}
    assert set(reading.ports) == {SSD, USB2_HUB}


@pytest.mark.os_agnostic
def test_a_root_hub_is_never_taken_for_a_port() -> None:
    """A root hub hangs off its PCI controller, so it sits in no port and nothing about it is unread."""
    kernel = FakeHubKernel(_answers())

    reading = _read(kernel, {"disk0": _disk("SCSI\\DISK&VEN_X\\7&1", ROOT_HUB, HOST_CONTROLLER)})

    assert (reading.ports, reading.disk_errors) == ({}, {})
    assert kernel.opened == []
