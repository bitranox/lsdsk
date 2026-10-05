"""A USB disk shows its socket, its own capability and its running link, each under its own heading."""

from __future__ import annotations

import pytest

from lsdsk.adapters.render import detail, report, theme
from lsdsk.domain.enums import BusType, UsbLaneRate, UsbTransport
from lsdsk.domain.models import Disk, InterfaceLink, Inventory, UsbLink, UsbSpeed
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

HIGH = UsbSpeed(lane_rate=UsbLaneRate.HIGH)
GEN1 = UsbSpeed(lane_rate=UsbLaneRate.GEN1)
GEN2 = UsbSpeed(lane_rate=UsbLaneRate.GEN2)


def _usb_disk(usb: UsbLink) -> Disk:
    return Disk(
        node="sdb",
        path="/dev/sdb",
        model="Portable SSD",
        bus=BusType.USB,
        link=InterfaceLink(negotiated_gbps=6.0, drive_max_gbps=6.0),
        usb=usb,
    )


def _link_values(disk: Disk) -> dict[str, tuple[str, str]]:
    record = detail.disk_detail(disk, Inventory(hostname="h", disks=(disk,)), thresholds=DEFAULT_THRESHOLDS)
    group = next(group for group in record.groups if group.label == detail.LINK)
    return dict(group.values)


@pytest.mark.os_agnostic
def test_a_usb_row_puts_each_figure_under_its_own_heading() -> None:
    cells = report.disk_cells(
        _usb_disk(UsbLink(running=HIGH, device_max=GEN2, port_max=HIGH)), bandwidth=True, thresholds=DEFAULT_THRESHOLDS
    )
    assert cells["port"] == "USB480M (0.06 GB/s)"
    assert cells["disk"] == "USB10G (1.21 GB/s)"
    assert cells["link"] == "USB480M (0.06 GB/s)"
    assert cells["bus"] == theme.format_bus(BusType.USB)


@pytest.mark.os_agnostic
def test_a_usb_row_without_bandwidth_carries_the_bare_figure() -> None:
    cells = report.disk_cells(
        _usb_disk(UsbLink(running=GEN1, device_max=GEN2, port_max=GEN2)), thresholds=DEFAULT_THRESHOLDS
    )
    assert (cells["port"], cells["disk"], cells["link"]) == ("USB10G", "USB10G", "USB5G")


@pytest.mark.os_agnostic
def test_an_unread_usb_end_is_the_unread_marker_and_carries_no_bandwidth() -> None:
    cells = report.disk_cells(
        _usb_disk(UsbLink(running=HIGH, device_max=None, port_max=None)), bandwidth=True, thresholds=DEFAULT_THRESHOLDS
    )
    assert cells["port"] == theme.NOT_READ
    assert cells["disk"] == theme.NOT_READ


@pytest.mark.os_agnostic
def test_a_usb_row_is_coloured_by_the_same_three_number_rule() -> None:
    styles = report.disk_cell_styles(
        _usb_disk(UsbLink(running=HIGH, device_max=GEN2, port_max=GEN2)), thresholds=DEFAULT_THRESHOLDS
    )
    assert styles["link"] == theme.STYLE_FAILING  # both ends read, running below both
    capped = report.disk_cell_styles(
        _usb_disk(UsbLink(running=HIGH, device_max=GEN2, port_max=HIGH)), thresholds=DEFAULT_THRESHOLDS
    )
    assert capped["port"] == theme.STYLE_BELOW_CAPABILITY
    assert capped["link"] == theme.STYLE_AT_CAPABILITY  # running at the port's ceiling is no fault


@pytest.mark.os_agnostic
def test_a_link_held_down_by_a_slower_hub_is_not_coloured_as_a_fault() -> None:
    # The finding names the hub as the ceiling, so the cell may not call the link failing.
    usb = UsbLink(running=GEN1, device_max=GEN2, port_max=GEN2, behind_hub=True, upstream=GEN1)
    assert report.disk_cell_styles(_usb_disk(usb), thresholds=DEFAULT_THRESHOLDS)["link"] == theme.STYLE_AT_CAPABILITY
    # control: the same link with no hub in the way is a fault
    alone = UsbLink(running=GEN1, device_max=GEN2, port_max=GEN2, behind_hub=False)
    assert report.disk_cell_styles(_usb_disk(alone), thresholds=DEFAULT_THRESHOLDS)["link"] == theme.STYLE_FAILING


@pytest.mark.os_agnostic
def test_the_detail_panel_names_the_usb_side_and_the_drive_behind_the_bridge() -> None:
    usb = UsbLink(
        running=HIGH, device_max=GEN2, port_max=HIGH, behind_hub=False, on_usb2_twin=False, transport=UsbTransport.UAS
    )
    values = _link_values(_usb_disk(usb))
    assert values["negotiated"][0] == "USB480M (0.06 GB/s)"
    assert values["achievable"][0] == "USB480M (0.06 GB/s)"
    assert values["hub above"][0] == theme.NOT_APPLICABLE
    assert values["usb2 side"][0] == "no"
    assert values["transport"][0] == "UAS"
    assert values["drive link"][0].startswith("6G")
    assert values["drive can do"][0].startswith("6G")


@pytest.mark.os_agnostic
def test_the_detail_panel_shows_the_hub_above_and_marks_what_was_not_read() -> None:
    usb = UsbLink(running=GEN1, device_max=GEN2, port_max=None, behind_hub=True, upstream=GEN1)
    values = _link_values(_usb_disk(usb))
    assert values["hub above"][0] == "USB5G (0.50 GB/s)"
    assert values["achievable"][0] == theme.NOT_READ
    assert values["usb2 side"][0] == theme.NOT_READ
    assert values["transport"][0] == theme.NOT_READ


@pytest.mark.os_agnostic
def test_a_disk_off_usb_draws_no_usb_rows() -> None:
    plain = Disk(node="sda", path="/dev/sda", model="m", bus=BusType.SATA, link=InterfaceLink(negotiated_gbps=6.0))
    assert "transport" not in _link_values(plain)
