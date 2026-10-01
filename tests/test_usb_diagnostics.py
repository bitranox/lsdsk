"""A USB disk's link is graded like every other link: both ends, and whether the drive would notice."""

from __future__ import annotations

import pytest

from lsdsk.domain.diagnostics import diagnose, diagnose_usb_link
from lsdsk.domain.enums import BusType, Environment, Severity, UsbLaneRate
from lsdsk.domain.models import Disk, InterfaceLink, Inventory, UsbLink, UsbSpeed

HIGH = UsbSpeed(lane_rate=UsbLaneRate.HIGH)
GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
GEN2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)


def _disk(usb: UsbLink, *, sata_gbps: float | None = 6.0) -> Disk:
    return Disk(
        node="sdb",
        path="/dev/sdb",
        model="Portable SSD",
        bus=BusType.USB,
        link=InterfaceLink(negotiated_gbps=sata_gbps, drive_max_gbps=sata_gbps),
        usb=usb,
    )


@pytest.mark.os_agnostic
def test_a_superspeed_disk_on_the_usb2_half_of_a_usb3_port_is_a_warning_naming_the_cable() -> None:
    [finding] = diagnose_usb_link(_disk(UsbLink(running=HIGH, device_max=GEN2, port_max=GEN2, on_usb2_twin=True)))
    assert finding.severity is Severity.WARNING
    assert "USB480M" in finding.title and "USB 3" in finding.title
    assert finding.action is not None and "cable" in finding.action


@pytest.mark.os_agnostic
def test_a_link_below_both_read_ends_is_a_warning_and_says_both_ends() -> None:
    [finding] = diagnose_usb_link(_disk(UsbLink(running=GEN1, device_max=GEN2, port_max=GEN2, on_usb2_twin=False)))
    assert finding.severity is Severity.WARNING
    assert "both ends support USB10G" in finding.title


@pytest.mark.os_agnostic
def test_a_slower_port_is_a_warning_when_the_drive_behind_the_bridge_would_notice() -> None:
    # A 6 Gb/s SATA SSD pulls 0.6 GB/s; a USB 2 port carries 0.06.
    [finding] = diagnose_usb_link(_disk(UsbLink(running=HIGH, device_max=GEN2, port_max=HIGH, on_usb2_twin=False)))
    assert finding.severity is Severity.WARNING
    assert "its port only offers USB480M" in finding.title


@pytest.mark.os_agnostic
def test_a_slower_port_is_only_a_hint_when_the_drive_would_not_notice() -> None:
    # A 1.5 Gb/s drive pulls 0.15 GB/s, which a 5 Gb/s port (0.5 GB/s) already carries.
    usb = UsbLink(running=GEN1, device_max=GEN2, port_max=GEN1, on_usb2_twin=False)
    [finding] = diagnose_usb_link(_disk(usb, sata_gbps=1.5))
    assert finding.severity is Severity.HINT
    assert "costs nothing today" in (finding.detail or "")


@pytest.mark.os_agnostic
def test_an_unread_drive_behind_the_bridge_counts_as_one_that_would_notice() -> None:
    usb = UsbLink(running=GEN1, device_max=GEN2, port_max=GEN1, on_usb2_twin=False)
    [finding] = diagnose_usb_link(_disk(usb, sata_gbps=None))
    assert finding.severity is Severity.WARNING
    assert "was not read" in (finding.detail or "")


@pytest.mark.os_agnostic
def test_a_hub_slower_than_the_disk_and_its_port_is_named_as_the_ceiling() -> None:
    usb = UsbLink(running=GEN1, device_max=GEN2, port_max=GEN2, behind_hub=True, upstream=GEN1)
    [finding] = diagnose_usb_link(_disk(usb))
    assert "a hub between it and the machine runs at USB5G" in finding.title


@pytest.mark.os_agnostic
def test_an_unread_port_claims_no_fault() -> None:
    [finding] = diagnose_usb_link(_disk(UsbLink(running=HIGH, device_max=GEN2, port_max=None)))
    assert finding.severity is Severity.WARNING
    assert "both ends" not in finding.title
    assert finding.action is not None and "cable" not in finding.action


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "usb",
    [
        UsbLink(running=GEN2, device_max=GEN2, port_max=GEN2, on_usb2_twin=False),
        UsbLink(running=None, device_max=GEN2, port_max=GEN2),
        UsbLink(running=HIGH, device_max=None, port_max=None),
    ],
    ids=["at-capability", "running-unread", "device-unread"],
)
def test_a_link_at_its_capability_or_unread_raises_nothing(usb: UsbLink) -> None:
    assert diagnose_usb_link(_disk(usb)) == []


@pytest.mark.os_agnostic
def test_a_disk_with_no_usb_link_is_not_judged_here() -> None:
    assert diagnose_usb_link(Disk(node="sda", path="/dev/sda", model="m")) == []


@pytest.mark.os_agnostic
def test_diagnose_runs_the_usb_rule_for_every_physical_disk() -> None:
    disk = _disk(UsbLink(running=HIGH, device_max=GEN2, port_max=GEN2, on_usb2_twin=True))
    physical = diagnose(Inventory(hostname="h", disks=(disk,)))
    assert any("USB 3" in finding.title for finding in physical)
    # control: a hypervisor invents link speeds, so the rule stays silent there
    virtual = diagnose(Inventory(hostname="h", environment=Environment.VIRTUAL_MACHINE, disks=(disk,)))
    assert not any("USB 3" in finding.title for finding in virtual)
