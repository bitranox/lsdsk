"""A Windows USB disk's link is built from what its hubs answered.

Each test takes the committed ``windows-ahci`` capture, turns its one disk into a
USB disk by giving it the device chain a real one has, and records the answers
real hubs gave for that chain under ``usb_ports`` / ``usb_hubs``. Decoding
happens in the builder, so these hold the Windows mapping on every runner.

Two placements were measured on one Windows 11 machine with one 10 Gb/s UAS
SSD (SanDisk 0781:558c): behind a USB 2 hub, and directly in a USB-C port of
the xHCI root hub, where it runs SuperSpeedPlus.
"""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose_usb_link
from lsdsk.domain.enums import BusType, UsbLaneRate, UsbTransport
from lsdsk.domain.models import Disk, UsbSpeed

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"

# The SSD's own instance ID ends in its serial, so a made-up one of the same width stands in.
SSD = "USB\\VID_0781&PID_558C\\TESTSERIAL0123456789"
MASS_STORAGE = "SCSI\\DISK&VEN_SANDISK&PROD_EXTREME_SSD\\7&2A6D0F4B&0&000000"
USB2_HUB = "USB\\VID_05E3&PID_0608\\5&CC3F949&0&13"
ROOT_HUB = "USB\\ROOT_HUB30\\4&2FD48294&0&0"
HOST_CONTROLLER = "PCI\\VEN_8086&DEV_7AE0&SUBSYS_86941043&REV_11\\3&11583659&0&A0"

# Behind the USB 2 hub: port 3 of that hub, which sits in port 13 of the root hub.
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

# Directly in port 17 of the root hub, a USB-C socket whose USB 2 half is port 1.
_ROOT_CONNECTOR_NAME = (
    "550053004200230052004f004f0054005f004800550042003300300023003400260032006600640034003800320039003400"
    "260030002600300023007b00660031003800610030006500380038002d0063003300300063002d0031003100640030002d00"
    "38003800310035002d003000300061003000630039003000360062006500640038007d00"
)
DIRECT_CONNECTION = bytes.fromhex(
    "11000000120110030000000981078c551210020301010102000b0004000000010000000705810200040000000000"
    "070502020004000000000007058302000400000000000705040200040000000000"
)
DIRECT_CONNECTION_V2 = bytes.fromhex("1100000010000000040000000f000000")
DIRECT_CONNECTOR = bytes.fromhex("110000009a0000000b00000000000100" + _ROOT_CONNECTOR_NAME).ljust(154, b"\0")
DIRECT_SUPERSPEEDPLUS = bytes.fromhex("110000001800000035400a0000000000b5400a0000000000")

# The USB 2 half of that USB-C socket, which names port 17 as its companion.
TWIN_CONNECTION = bytes([1]) + SSD_CONNECTION[1:]
TWIN_CONNECTION_V2 = bytes.fromhex("0100000010000000030000000a000000")
TWIN_CONNECTOR = bytes.fromhex("010000009a0000000900000000001100" + _ROOT_CONNECTOR_NAME).ljust(154, b"\0")

# A 5 Gb/s USB 3 hub (05e3:0626) in root port 18, with the SSD in its port 1. The
# hub's answers are real; its BOS Container ID, unique to the unit, is made up.
USB3_HUB = "USB\\VID_05E3&PID_0626\\5&CC3F949&0&18"
USB3_HUB_CONNECTION = bytes.fromhex(
    "120000001201200309000309e3052606560601020001010201040001000000010000000705811302000800000000"
)
USB3_HUB_CONNECTION_V2 = bytes.fromhex("12000000100000000400000003000000")
USB3_HUB_BOS = bytes.fromhex(
    "050f2a0003071002060000000a1003000e000108be00141004" + "00" + "00112233445566778899aabbccddeeff"
)
USB3_HUB_INFORMATION = bytes.fromhex("0300000004000c2a0400000090045e01") + bytes(60)
SSD_IN_USB3_HUB_CONNECTION = bytes([1]) + SSD_CONNECTION[1:]
SSD_IN_USB3_HUB_CONNECTION_V2 = bytes.fromhex("0100000010000000040000000b000000")

USB2 = UsbSpeed(lane_rate=UsbLaneRate.HIGH)
GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
GEN2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _port(hub: str, port: int, service: str, **answers: bytes | None) -> dict[str, Any]:
    """One ``usb_ports`` entry, every buffer base64 as the reader records it."""
    entry: dict[str, Any] = {"hub": hub, "port": port, "service": service}
    entry.update({name: _b64(blob) for name, blob in answers.items() if blob is not None})
    return entry


def _usb_capture(ancestors: list[str], ports: dict[str, Any], hubs: dict[str, bytes]) -> dict[str, Any]:
    """The committed Windows capture with its one disk moved behind a USB device chain."""
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pci"][HOST_CONTROLLER] = {"class": "0x0c0330", "vendor": "0x8086", "device": "0x7ae0"}
    disk = next(iter(payload["disks"].values()))
    disk["device"]["bus_type"] = "usb"
    # What the storage descriptor says, set apart from what IDENTIFY says, so
    # the tests can tell which of the two a figure came from.
    disk["device"]["model"] = "SanDisk Extreme SSD"
    disk["device"]["rev"] = "1012"
    disk["parent"] = MASS_STORAGE
    disk["ancestors"] = [MASS_STORAGE, *ancestors, HOST_CONTROLLER]
    payload["usb_ports"] = ports
    payload["usb_hubs"] = {hub: {"information": _b64(info)} for hub, info in hubs.items()}
    return payload


def _behind_usb2_hub(**overrides: bytes | None) -> dict[str, Any]:
    """The SSD in port 3 of a USB 2 hub, as the step-0 probe found it."""
    ssd: dict[str, bytes | None] = {
        "connection": SSD_CONNECTION,
        "connection_v2": SSD_CONNECTION_V2,
        "connector": SSD_CONNECTOR,
        "bos": SSD_BOS,
    }
    ssd.update(overrides)
    ports = {
        SSD: _port(USB2_HUB, 3, "UASPStor", **ssd),
        USB2_HUB: _port(
            ROOT_HUB, 13, "USBHUB3", connection=HUB_CONNECTION, connection_v2=HUB_CONNECTION_V2, connector=HUB_CONNECTOR
        ),
    }
    hubs = {USB2_HUB: USB2_HUB_INFORMATION, ROOT_HUB: ROOT_HUB_INFORMATION}
    return _usb_capture([SSD, USB2_HUB, ROOT_HUB], ports, hubs)


def _in_root_port(port: int, answers: dict[str, bytes | None]) -> dict[str, Any]:
    """The SSD directly in a port of the xHCI root hub."""
    ports = {SSD: _port(ROOT_HUB, port, "UASPStor", **answers)}
    return _usb_capture([SSD, ROOT_HUB], ports, {ROOT_HUB: ROOT_HUB_INFORMATION})


def _direct(**overrides: bytes | None) -> dict[str, Any]:
    """The SSD in the USB-C root port 17, running SuperSpeedPlus, as measured."""
    answers: dict[str, bytes | None] = {
        "connection": DIRECT_CONNECTION,
        "connection_v2": DIRECT_CONNECTION_V2,
        "connector": DIRECT_CONNECTOR,
        "superspeedplus": DIRECT_SUPERSPEEDPLUS,
        "bos": SSD_BOS,
    }
    answers.update(overrides)
    return _in_root_port(17, answers)


def _disk(payload: dict[str, Any]) -> Disk:
    return build_from(copy.deepcopy(payload)).disks[0]


@pytest.mark.os_agnostic
def test_the_real_buffers_are_the_shapes_the_decoders_take() -> None:
    """The hand-assembled connector buffers must be as long as the hub said they were."""
    assert len(DIRECT_CONNECTOR) == DIRECT_CONNECTOR[4] == 154
    assert len(TWIN_CONNECTOR) == TWIN_CONNECTOR[4] == 154


@pytest.mark.os_agnostic
def test_a_disk_behind_a_usb2_hub_is_held_to_that_hub() -> None:
    disk = _disk(_behind_usb2_hub())
    link = disk.usb

    assert disk.bus is BusType.USB
    assert link is not None
    assert link.running == USB2
    assert link.device_max == GEN2
    assert link.port_max == USB2, "a port that speaks no USB 3 can do 480 Mb/s, which is a reading"
    assert link.on_usb2_twin is False
    assert link.behind_hub is True
    assert link.upstream == USB2
    assert link.transport is UsbTransport.UAS


@pytest.mark.os_agnostic
def test_a_disk_in_a_root_usb3_port_runs_the_superspeedplus_rate_the_hub_reports() -> None:
    link = _disk(_direct()).usb

    assert link is not None
    assert link.running == UsbSpeed(lane_rate=UsbLaneRate.GEN2, lanes=1)
    assert link.device_max == GEN2
    assert link.port_max is None, "a root port says only USB 3, never 5, 10 or 20 Gb/s"
    assert link.on_usb2_twin is False
    assert link.behind_hub is False, "the hub it is plugged into is the root hub"
    assert link.upstream is None


@pytest.mark.os_agnostic
def test_superspeedplus_without_its_rate_leaves_the_running_speed_unread() -> None:
    """V2 says SuperSpeedPlus but no rate came back: a guess would be 10 Gb/s, which reads a 20 Gb/s link as slow."""
    link = _disk(_direct(superspeedplus=None)).usb

    assert link is not None
    assert link.running is None
    assert link.device_max == GEN2


@pytest.mark.os_agnostic
def test_superspeed_without_plus_runs_at_5g() -> None:
    operating_superspeed_only = bytes.fromhex("11000000100000000400000003000000")
    link = _disk(_direct(connection_v2=operating_superspeed_only, superspeedplus=None)).usb

    assert link is not None
    assert link.running == UsbSpeed(lane_rate=UsbLaneRate.GEN1)


@pytest.mark.os_agnostic
def test_without_the_v2_answer_a_high_speed_report_is_not_believed() -> None:
    """``..._EX`` says high speed for a SuperSpeed device, so only V2 can say a link runs at 480."""
    link = _disk(_behind_usb2_hub(connection_v2=None)).usb

    assert link is not None
    assert link.running is None
    assert link.port_max is None


@pytest.mark.os_agnostic
def test_a_superspeed_disk_on_the_usb2_half_of_a_usb_c_socket_fell_back() -> None:
    link = _disk(
        _in_root_port(
            1,
            {
                "connection": TWIN_CONNECTION,
                "connection_v2": TWIN_CONNECTION_V2,
                "connector": TWIN_CONNECTOR,
                "bos": SSD_BOS,
            },
        )
    ).usb

    assert link is not None
    assert link.running == USB2
    assert link.on_usb2_twin is True
    assert link.port_max is None, "the socket's USB 3 half is a different port, which nobody asked"
    assert link.fell_back_to_usb2


@pytest.mark.os_agnostic
def test_a_usb2_port_without_a_usb3_companion_is_not_a_twin() -> None:
    link = _disk(_behind_usb2_hub()).usb

    assert link is not None
    assert link.on_usb2_twin is False


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("service", "transport"), [("UASPStor", UsbTransport.UAS), ("USBSTOR", UsbTransport.BOT)])
def test_the_bound_driver_names_the_transport(service: str, transport: UsbTransport) -> None:
    payload = _behind_usb2_hub()
    payload["usb_ports"][SSD]["service"] = service

    link = _disk(payload).usb

    assert link is not None
    assert link.transport is transport


@pytest.mark.os_agnostic
def test_a_port_that_could_not_be_asked_leaves_the_link_unread_and_says_why() -> None:
    payload = _in_root_port(17, {})
    payload["usb_ports"][SSD]["error"] = "Win32 error 5"
    next(iter(payload["disks"].values()))["usb_link_error"] = "Win32 error 5"

    disk = _disk(payload)

    assert disk.bus is BusType.USB
    assert disk.usb is not None
    assert (disk.usb.running, disk.usb.device_max, disk.usb.port_max) == (None, None, None)
    assert "usb-link" in {refusal.reading for refusal in disk.readings_refused}


@pytest.mark.os_agnostic
def test_a_usb_disk_keeps_what_its_identify_says() -> None:
    """The SAT passthrough read IDENTIFY through the bridge, and the drive's own answers outrank the bridge's."""
    disk = _disk(_behind_usb2_hub())

    assert (disk.model, disk.firmware) == ("QEMU HARDDISK", "2.5+")


@pytest.mark.os_agnostic
def test_a_disk_with_no_usb_device_above_it_has_no_usb_link() -> None:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))

    disk = _disk(payload)

    assert disk.usb is None
    assert disk.bus is BusType.SATA


@pytest.mark.os_agnostic
def test_a_socket_on_an_external_usb3_hub_is_as_fast_as_that_hub_declares() -> None:
    ports = {
        SSD: _port(
            USB3_HUB,
            1,
            "UASPStor",
            connection=SSD_IN_USB3_HUB_CONNECTION,
            connection_v2=SSD_IN_USB3_HUB_CONNECTION_V2,
            bos=SSD_BOS,
        ),
        USB3_HUB: _port(
            ROOT_HUB,
            18,
            "USBHUB3",
            connection=USB3_HUB_CONNECTION,
            connection_v2=USB3_HUB_CONNECTION_V2,
            bos=USB3_HUB_BOS,
        ),
    }
    hubs = {USB3_HUB: USB3_HUB_INFORMATION, ROOT_HUB: ROOT_HUB_INFORMATION}
    link = _disk(_usb_capture([SSD, USB3_HUB, ROOT_HUB], ports, hubs)).usb

    assert link is not None
    assert link.running == GEN1
    assert link.device_max == GEN2
    assert link.port_max == GEN1, "the hub's own BOS says 5 Gb/s, so its ports can do no more"
    assert link.behind_hub is True
    assert link.upstream == GEN1


@pytest.mark.os_agnostic
def test_without_a_bos_the_capability_flags_still_say_what_the_device_can_do() -> None:
    """A BOS that was not recorded leaves V2's capable-of flags, which set a floor under the device."""
    link = _disk(_behind_usb2_hub(bos=None)).usb

    assert link is not None
    assert link.running == USB2
    assert link.device_max == GEN2


@pytest.mark.os_agnostic
def test_a_disk_under_a_usb_device_is_a_usb_disk_whatever_storage_bus_windows_names() -> None:
    """USB-ness comes from the device chain, not from the storage descriptor's bus, which a bridge driver chooses."""
    payload = _behind_usb2_hub()
    next(iter(payload["disks"].values()))["device"]["bus_type"] = "unknown"

    disk = _disk(payload)

    assert disk.bus is BusType.USB
    assert disk.usb is not None
    assert (disk.model, disk.firmware) == ("QEMU HARDDISK", "2.5+"), "IDENTIFY through the bridge is still read"


def _twin_port_without_its_connector_answer() -> dict[str, Any]:
    """The SSD on the USB 2 half of the USB-C socket, with the hub's connector answer missing."""
    return _in_root_port(
        1,
        {
            "connection": TWIN_CONNECTION,
            "connection_v2": TWIN_CONNECTION_V2,
            "connector": None,
            "bos": SSD_BOS,
        },
    )


@pytest.mark.os_agnostic
def test_a_usb2_port_whose_connector_was_not_read_is_not_a_480m_socket() -> None:
    """Only the connector answer says whether a USB 3 half exists, so without it the socket is unread.

    The port speaks no USB 3 itself, which is exactly what the USB 2 half of a
    USB-C socket looks like. Calling that a 480 Mb/s socket would send the
    reader to another port for a disk whose own socket may do 10 Gb/s.
    """
    link = _disk(_twin_port_without_its_connector_answer()).usb

    assert link is not None
    assert link.running == USB2
    assert link.on_usb2_twin is None, "nobody said whether the socket has a USB 3 half"
    assert link.port_max is None, "an unread twin is not an absent one"


@pytest.mark.os_agnostic
def test_a_usb2_port_whose_connector_was_not_read_raises_no_finding_blaming_the_socket() -> None:
    findings = diagnose_usb_link(_disk(_twin_port_without_its_connector_answer()))

    assert len(findings) == 1, "the shortfall below the drive's own rate is still shown"
    finding = findings[0]
    assert "below its own" in finding.title, f"expected the unattributed shortfall, got: {finding.title}"
    assert "port only offers" not in finding.title, finding.title


@pytest.mark.os_agnostic
def test_a_usb2_port_whose_connector_names_no_companion_is_still_a_480m_socket() -> None:
    """The control: a connector answer that rules out a USB 3 half keeps the 480 Mb/s reading."""
    link = _disk(_behind_usb2_hub()).usb

    assert link is not None
    assert link.on_usb2_twin is False
    assert link.port_max == USB2
