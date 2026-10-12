"""Digits in sysfs text are ASCII, and a phy that reports a rate of zero reports none.

``int()`` and the regex digit class both read other scripts' digits as numbers, so a hostile
or mangled capture could name a port or a phy in Arabic-Indic digits and be
believed; and ``0.0 Gbit`` is what a virtual or idle phy prints, which is no
link rate to count a port from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.hw.decode import usb
from lsdsk.adapters.hw.decode.captured import parse_int
from lsdsk.adapters.hw.linux.builder import parse_link_rate
from lsdsk.adapters.hw.linux.reader import read_pci, read_usb
from lsdsk.adapters.hw.snapshot import build_from

if TYPE_CHECKING:
    from pathlib import Path

_ARABIC_THREE = chr(0x663)
_ADDRESS = "0000:03:00.0"
_HBA = f"/sys/devices/pci0000:00/0000:00:03.0/{_ADDRESS}"


def _hba_with_phys(phys: dict[str, dict[str, str]]) -> dict[str, Any]:
    """A capture whose one SAS controller carries the given ``sas_phy`` entries."""
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "digits",
        "kernel": "6.8.0",
        "pci": {_ADDRESS: {"class": "0x010700", "driver": "mpt3sas", "path": _HBA}},
        "classes": {"sas_phy": phys},
    }


def _phy(name: str, rate: str) -> dict[str, str]:
    """One phy entry under the HBA, naming itself ``name``."""
    return {"path": f"{_HBA}/host6/{name}/sas_phy/{name}", "negotiated_linkrate": rate, "maximum_linkrate_hw": rate}


def test_parse_int_refuses_another_scripts_digits_and_keeps_ascii() -> None:
    assert parse_int(_ARABIC_THREE) is None
    assert parse_int(f"1{_ARABIC_THREE}") is None
    assert parse_int("13") == 13


def test_a_phy_named_in_other_digits_is_not_one_of_the_controllers_ports() -> None:
    phys = {
        f"phy-6:{_ARABIC_THREE}": _phy(f"phy-6:{_ARABIC_THREE}", "6.0 Gbit"),
        "phy-6:0": _phy("phy-6:0", "6.0 Gbit"),
    }

    (controller,) = build_from(_hba_with_phys(phys)).controllers

    assert controller.port_count == 1


def test_a_link_rate_in_other_digits_is_unread() -> None:
    assert parse_link_rate(f"{_ARABIC_THREE}.0 Gbit") is None
    assert parse_link_rate("3.0 Gbit") == 3.0


def test_a_phy_rate_of_zero_is_unread_and_the_phy_is_not_a_port() -> None:
    assert parse_link_rate("0.0 Gbit") is None
    phys = {"phy-6:0": _phy("phy-6:0", "0.0 Gbit"), "phy-6:1": _phy("phy-6:1", "6.0 Gbit")}

    (controller,) = build_from(_hba_with_phys(phys)).controllers

    assert controller.port_count == 1


def test_a_drive_below_a_port_named_in_other_digits_is_on_no_sas_port() -> None:
    port = f"port-6:{_ARABIC_THREE}"
    reading = _hba_with_phys({"phy-6:0": {**_phy("phy-6:0", "6.0 Gbit"), "port": port}})
    reading["block"] = {
        "sdzz": {
            "size": "1",
            "device_path": f"{_HBA}/host6/{port}/end_device-6:0/target6:0:0/6:0:0:0",
            "device": {"model": "X", "vendor": "SEAGATE"},
            "vpd": {},
        }
    }

    (disk,) = build_from(reading).disks

    assert disk.link.negotiated_gbps is None


@pytest.mark.parametrize("text", ["1_6", "+8", "0x_10", "1 6", "", "0x", "-", "8\n9"])
def test_parse_int_refuses_what_int_accepts_beyond_plain_ascii_digits(text: str) -> None:
    assert parse_int(text) is None
    assert parse_int(text, 16) is None


def test_parse_int_keeps_the_forms_a_capture_really_holds() -> None:
    assert (parse_int(" 12\n"), parse_int("-5000"), parse_int("0x1AF", 16), parse_int("1af", 16)) == (
        12,
        -5000,
        431,
        431,
    )


def test_a_lane_count_in_other_digits_is_unread_and_counts_as_one_lane() -> None:
    fullwidth_two = chr(0xFF12)

    other = usb.speed_from_sysfs("10000", fullwidth_two, fullwidth_two)
    ascii_two = usb.speed_from_sysfs("10000", "2", "2")

    assert other is not None and ascii_two is not None
    assert (other.lanes, ascii_two.lanes) == (1, 2)


def _usb_disk_under_a_device_of_version(tmp_path: Path, version: str) -> dict[str, dict[str, Any]]:
    """Read the one USB device above a disk whose ``version`` attribute reads ``version``."""
    device = tmp_path / "usb1" / "1-1"
    device.mkdir(parents=True)
    (device / "idVendor").write_text("0781\n", encoding="utf-8")
    (device / "version").write_text(f" {version}\n", encoding="utf-8")
    return read_usb({"sdz": {"device_path": str(device / "1-1:1.0" / "host0")}})


def test_a_usb_version_in_other_digits_is_asked_about_rather_than_believed_to_have_no_bos(tmp_path: Path) -> None:
    fullwidth = f"{chr(0xFF11)}.{chr(0xFF10)}"

    (device,) = _usb_disk_under_a_device_of_version(tmp_path, fullwidth).values()

    assert "bos_none" not in device


def test_a_usb_version_below_2_1_declares_no_bos(tmp_path: Path) -> None:
    (device,) = _usb_disk_under_a_device_of_version(tmp_path, "1.0").values()

    assert device["bos_none"] is True


def _pci_device_of_class(tmp_path: Path, class_text: str) -> dict[str, Any]:
    """Read one PCI device whose ``class`` attribute reads ``class_text`` and which exposes a BAR5."""
    device = tmp_path / "devices" / "0000_03_00.0"
    device.mkdir(parents=True)
    (device / "class").write_text(f"{class_text}\n", encoding="utf-8")
    (device / "resource5").write_bytes(bytes(4096))
    return read_pci(tmp_path / "devices")["0000_03_00.0"]


def test_an_ahci_class_written_in_other_digits_is_not_an_ahci_controller(tmp_path: Path) -> None:
    fullwidth = "0x" + "".join(chr(ord(c) + 0xFEE0) for c in "010601")

    assert "ahci" not in _pci_device_of_class(tmp_path, fullwidth)


def test_an_ahci_class_in_ascii_is_read_for_its_registers(tmp_path: Path) -> None:
    assert "ahci" in _pci_device_of_class(tmp_path, "0x010601")
