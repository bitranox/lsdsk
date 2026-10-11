"""Digits in sysfs text are ASCII, and a phy that reports a rate of zero reports none.

``int()`` and the regex digit class both read other scripts' digits as numbers, so a hostile
or mangled capture could name a port or a phy in Arabic-Indic digits and be
believed; and ``0.0 Gbit`` is what a virtual or idle phy prints, which is no
link rate to count a port from.
"""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.decode.captured import parse_int
from lsdsk.adapters.hw.linux.builder import parse_link_rate
from lsdsk.adapters.hw.snapshot import build_from

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


@pytest.mark.os_agnostic
def test_parse_int_refuses_another_scripts_digits_and_keeps_ascii() -> None:
    assert parse_int(_ARABIC_THREE) is None
    assert parse_int(f"1{_ARABIC_THREE}") is None
    assert parse_int("13") == 13


@pytest.mark.os_agnostic
def test_a_phy_named_in_other_digits_is_not_one_of_the_controllers_ports() -> None:
    phys = {
        f"phy-6:{_ARABIC_THREE}": _phy(f"phy-6:{_ARABIC_THREE}", "6.0 Gbit"),
        "phy-6:0": _phy("phy-6:0", "6.0 Gbit"),
    }

    (controller,) = build_from(_hba_with_phys(phys)).controllers

    assert controller.port_count == 1


@pytest.mark.os_agnostic
def test_a_link_rate_in_other_digits_is_unread() -> None:
    assert parse_link_rate(f"{_ARABIC_THREE}.0 Gbit") is None
    assert parse_link_rate("3.0 Gbit") == 3.0


@pytest.mark.os_agnostic
def test_a_phy_rate_of_zero_is_unread_and_the_phy_is_not_a_port() -> None:
    assert parse_link_rate("0.0 Gbit") is None
    phys = {"phy-6:0": _phy("phy-6:0", "0.0 Gbit"), "phy-6:1": _phy("phy-6:1", "6.0 Gbit")}

    (controller,) = build_from(_hba_with_phys(phys)).controllers

    assert controller.port_count == 1


@pytest.mark.os_agnostic
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
