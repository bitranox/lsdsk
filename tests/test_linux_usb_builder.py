"""The Linux builder turns a USB disk's captured chain into its USB link, and calls its bus usb."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from lsdsk.adapters.hw.linux.builder import build_disks
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.domain.diagnostics import diagnose_usb_link
from lsdsk.domain.enums import BusType, Platform, Severity, UsbLaneRate, UsbTransport
from lsdsk.domain.models import RefusedReading, UsbSpeed

# The BOS of a real 10 Gb/s UAS SSD: SuperSpeed and SuperSpeedPlus Gen 2.
SANDISK_BOS = bytes.fromhex("050f2a00030710021ef400000a1003000e00010aff0714100a00010000000011000030400a00b0400a00")

ROOT = "/sys/devices/pci0000:00/0000:00:1d.0/usb1"
HUB = f"{ROOT}/1-1"
DISK = f"{HUB}/1-1.2"
TWIN_ROOT = "/sys/devices/pci0000:00/0000:00:14.0/usb2"
DEVICE_PATH = f"{DISK}/1-1.2:1.0/host14/target14:0:0/14:0:0:0"

HIGH = UsbSpeed(lane_rate=UsbLaneRate.HIGH)
GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
GEN2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)


def _usb2_path() -> dict[str, dict[str, Any]]:
    """An EHCI root, an Intel rate-matching hub with no BOS, and the 10G disk running at 480."""
    return {
        ROOT: {"name": "usb1", "speed": "480", "version": " 2.00", "bDeviceClass": "09", "bos_none": True},
        HUB: {"name": "1-1", "speed": "480", "version": " 2.00", "bDeviceClass": "09", "bos_none": True},
        DISK: {
            "name": "1-1.2",
            "speed": "480",
            "version": " 2.10",
            "bDeviceClass": "00",
            "interface_drivers": ["uas"],
            "bos": base64.b64encode(SANDISK_BOS).decode("ascii"),
        },
    }


def _disk(usb: dict[str, dict[str, Any]], *, device_path: str = DEVICE_PATH) -> Any:
    capture = LinuxCapture.model_validate(
        {
            "schema": 2,
            "platform": Platform.LINUX,
            "hostname": "crafted",
            "kernel": "6.8.0",
            "pci": {},
            "block": {"sdb": {"device_path": device_path, "size": "1953525168"}},
            "usb": usb,
        }
    )
    [disk] = build_disks(capture)
    return disk


@pytest.mark.os_agnostic
def test_a_10g_disk_behind_a_usb2_hub_reads_every_end_of_its_link() -> None:
    disk = _disk(_usb2_path())
    assert disk.bus is BusType.USB
    usb = disk.usb
    assert usb is not None
    assert (usb.running, usb.device_max, usb.port_max) == (HIGH, GEN2, HIGH)
    assert (usb.behind_hub, usb.upstream, usb.on_usb2_twin) == (True, HIGH, False)
    assert usb.transport is UsbTransport.UAS


@pytest.mark.os_agnostic
def test_the_usb2_path_raises_the_port_ceiling_finding() -> None:
    [finding] = diagnose_usb_link(_disk(_usb2_path()))
    assert finding.severity is Severity.WARNING
    assert "its port only offers USB480M" in finding.title


@pytest.mark.os_agnostic
def test_a_socket_with_a_usb3_twin_can_do_what_the_twin_does_and_the_disk_fell_back() -> None:
    usb = _usb2_path()
    usb[DISK]["peer_hub"] = TWIN_ROOT
    usb[TWIN_ROOT] = {"name": "usb2", "speed": "5000", "version": " 3.00", "bDeviceClass": "09"}
    link = _disk(usb).usb
    assert link is not None
    assert link.port_max == GEN1
    assert link.on_usb2_twin is True
    assert link.fell_back_to_usb2


@pytest.mark.os_agnostic
def test_a_disk_on_a_root_port_has_no_hub_above_it() -> None:
    usb = _usb2_path()
    del usb[HUB]
    link = _disk(usb).usb
    assert link is not None
    assert link.behind_hub is False
    assert link.upstream is None
    assert link.port_max == HIGH  # the root hub's own speed is the bus's capability


@pytest.mark.os_agnostic
def test_a_superspeedplus_root_reads_its_lanes() -> None:
    usb = {
        "/sys/devices/pci0000:00/0000:00:14.0/usb4": {
            "name": "usb4",
            "speed": "20000",
            "rx_lanes": "2",
            "tx_lanes": "2",
            "version": " 3.20",
        },
        "/sys/devices/pci0000:00/0000:00:14.0/usb4/4-1": {
            "name": "4-1",
            "speed": "10000",
            "rx_lanes": "1",
            "tx_lanes": "1",
            "version": " 3.20",
            "interface_drivers": ["usb-storage"],
            "bos": base64.b64encode(SANDISK_BOS).decode("ascii"),
        },
    }
    link = _disk(usb, device_path="/sys/devices/pci0000:00/0000:00:14.0/usb4/4-1/4-1:1.0/host3/target3:0:0/3:0:0:0").usb
    assert link is not None
    assert link.port_max == UsbSpeed(lane_rate=UsbLaneRate.GEN2, lanes=2)
    assert (link.running, link.device_max) == (GEN2, GEN2)
    assert link.transport is UsbTransport.BOT


@pytest.mark.os_agnostic
def test_a_device_with_no_bos_is_as_capable_as_it_runs() -> None:
    usb = _usb2_path()
    usb[DISK] = {**usb[DISK], "bos": None, "bos_none": True}
    link = _disk(usb).usb
    assert link is not None
    assert link.device_max == HIGH


@pytest.mark.os_agnostic
def test_an_unreadable_bos_leaves_the_device_unread_and_is_reported_as_refused() -> None:
    usb = _usb2_path()
    usb[DISK] = {**usb[DISK], "bos": None, "bos_error": "[Errno 13] Permission denied"}
    disk = _disk(usb)
    assert disk.usb is not None
    assert disk.usb.device_max is None
    assert RefusedReading(reading="usb-link", reason="[Errno 13] Permission denied") in disk.readings_refused


@pytest.mark.os_agnostic
def test_a_bos_that_does_not_decode_is_unread_rather_than_a_crash() -> None:
    usb = _usb2_path()
    usb[DISK] = {**usb[DISK], "bos": base64.b64encode(b"\x05\x0e\x05\x00\x00").decode("ascii")}
    link = _disk(usb).usb
    assert link is not None
    assert link.device_max is None


@pytest.mark.os_agnostic
def test_a_disk_off_usb_carries_no_usb_link() -> None:
    disk = _disk({})
    assert disk.usb is None
    assert disk.bus is not BusType.USB
    assert not any(refusal.reading == "usb-link" for refusal in disk.readings_refused)
