"""The USB link model: one spelling, bandwidth by line coding, and both ends required."""

from __future__ import annotations

import pytest

from lsdsk.domain.enums import UsbLaneRate, UsbTransport
from lsdsk.domain.models import Disk, UsbLink, UsbSpeed

GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
GEN2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)
GEN2X2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2, lanes=2)
GEN1X2 = UsbSpeed(lane_rate=UsbLaneRate.GEN1, lanes=2)
HIGH = UsbSpeed(lane_rate=UsbLaneRate.HIGH)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("speed", "figure", "gbps"),
    [
        (UsbSpeed(lane_rate=UsbLaneRate.LOW), "USB1.5M", 0.000188),
        (UsbSpeed(lane_rate=UsbLaneRate.FULL), "USB12M", 0.0015),
        (HIGH, "USB480M", 0.06),
        (GEN1, "USB5G", 0.5),
        (GEN2, "USB10G", 1.212121),
        (GEN1X2, "USB10G", 1.0),
        (GEN2X2, "USB20G", 2.424242),
    ],
)
def test_a_usb_speed_is_spelled_by_its_total_rate_and_priced_by_its_line_coding(
    speed: UsbSpeed, figure: str, gbps: float
) -> None:
    assert speed.figure == figure
    assert speed.bandwidth_gbps == gbps


@pytest.mark.os_agnostic
def test_a_usb_link_with_an_unread_end_has_nothing_achievable() -> None:
    assert UsbLink(running=HIGH, device_max=GEN2, port_max=None).achievable is None
    assert UsbLink(running=HIGH, device_max=None, port_max=GEN2).achievable is None
    # control: both ends read
    assert UsbLink(running=HIGH, device_max=GEN2, port_max=GEN1).achievable == GEN1


@pytest.mark.os_agnostic
def test_a_hub_slower_than_both_ends_is_what_the_link_can_achieve() -> None:
    link = UsbLink(running=HIGH, device_max=GEN2, port_max=GEN2, behind_hub=True, upstream=GEN1)
    assert link.achievable == GEN1


@pytest.mark.os_agnostic
def test_a_link_below_both_read_ends_is_underperforming_and_one_at_them_is_not() -> None:
    assert UsbLink(running=GEN1, device_max=GEN2, port_max=GEN2).is_underperforming
    assert not UsbLink(running=GEN2, device_max=GEN2, port_max=GEN2).is_underperforming
    assert not UsbLink(running=GEN1, device_max=GEN2, port_max=None).is_underperforming


@pytest.mark.os_agnostic
def test_a_superspeed_disk_on_the_usb2_twin_of_its_port_fell_back() -> None:
    assert UsbLink(running=HIGH, device_max=GEN2, on_usb2_twin=True).fell_back_to_usb2
    # controls: a USB 2 disk there did not fall back, and neither did one on a port with no twin
    assert not UsbLink(running=HIGH, device_max=HIGH, on_usb2_twin=True).fell_back_to_usb2
    assert not UsbLink(running=HIGH, device_max=GEN2, on_usb2_twin=False).fell_back_to_usb2
    assert not UsbLink(running=HIGH, device_max=GEN2, on_usb2_twin=None).fell_back_to_usb2


@pytest.mark.os_agnostic
def test_a_disk_carries_no_usb_link_unless_given_one() -> None:
    plain = Disk(node="sda", path="/dev/sda", model="m")
    assert plain.usb is None
    attached = plain.with_changes(usb=UsbLink(running=GEN1, transport=UsbTransport.UAS))
    assert attached.usb is not None and attached.usb.transport is UsbTransport.UAS


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rate", [UsbLaneRate.LOW, UsbLaneRate.FULL, UsbLaneRate.HIGH])
def test_two_lanes_below_superspeed_are_refused(rate: UsbLaneRate) -> None:
    """Dual-lane operation exists only for SuperSpeed, so USB960M is no speed at all."""
    with pytest.raises(ValueError, match="two lanes exist only at 5G and 10G"):
        UsbSpeed(lane_rate=rate, lanes=2)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rate", [UsbLaneRate.GEN1, UsbLaneRate.GEN2])
def test_two_superspeed_lanes_are_a_speed(rate: UsbLaneRate) -> None:
    """The control: the refusal is about the rate, not about two lanes."""
    assert UsbSpeed(lane_rate=rate, lanes=2).lanes == 2
