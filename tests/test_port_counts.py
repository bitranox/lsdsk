"""Which source a controller's port count is read from, and that only one is.

CLAUDE.md rule 4: an AHCI port count comes from the ports-implemented bitmap or
not at all, because libata creates one ``ata_port`` per DECLARED port and a
chipset commonly declares six while wiring two. That rule was keyed on the PCI
class alone, so a controller the ``ahci`` driver owns in RAID mode (class
0x0104, the firmware's RAID setting on most Intel boards) had no registers read
and was counted from its declared ports. And the two sources were SUMMED, so a
libsas HBA with eight phys and eight SATA drives behind them reported sixteen.

Now one source answers per controller: the bitmap for anything the ``ahci``
driver owns or the class names AHCI, the host phys for a SAS controller, and
the ``ata_port`` entries only where neither exists.
"""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.snapshot import build_from

_ADDRESS = "0000:00:17.0"
_PATH = f"/sys/devices/pci0000:00/{_ADDRESS}"


def _capture(pci: dict[str, Any], classes: dict[str, Any]) -> dict[str, Any]:
    """A minimal Linux reading holding one controller."""
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "ports",
        "kernel": "6.8.0",
        "pci": {_ADDRESS: {"path": _PATH, **pci}},
        "classes": classes,
    }


def _ata_ports(count: int, *, under: str = _PATH) -> dict[str, dict[str, str]]:
    """``count`` libata ports below a path, as libata names them."""
    return {f"ata{n}": {"path": f"{under}/ata{n}/ata_port/ata{n}"} for n in range(1, count + 1)}


def _port_count(pci: dict[str, Any], classes: dict[str, Any]) -> int | None:
    """The port count the builder gives the one controller in a capture."""
    (controller,) = build_from(_capture(pci, classes)).controllers
    return controller.port_count


@pytest.mark.os_agnostic
def test_an_ahci_driven_controller_in_raid_mode_is_counted_from_its_bitmap() -> None:
    """Six declared ports, two implemented: the bitmap is the answer."""
    pci = {"class": "0x010400", "driver": "ahci", "ahci": {"capability": 0xE7234F05, "ports_implemented": 0b11}}

    assert _port_count(pci, {"ata_port": _ata_ports(6)}) == 2


@pytest.mark.os_agnostic
def test_an_ahci_driven_controller_in_raid_mode_with_no_bitmap_has_no_port_count() -> None:
    """Rule 4: bitmap or nothing, never the declared ports."""
    assert _port_count({"class": "0x010400", "driver": "ahci"}, {"ata_port": _ata_ports(6)}) is None


@pytest.mark.os_agnostic
def test_a_libsas_controller_is_counted_from_its_phys_never_phys_plus_ata_ports() -> None:
    """Eight phys with a SATA drive on each: eight ports, not sixteen."""
    host = f"{_PATH}/host0"
    phys = {
        f"phy-0:{n}": {
            "path": f"{host}/phy-0:{n}/sas_phy/phy-0:{n}",
            "negotiated_linkrate": "6.0 Gbit",
            "maximum_linkrate_hw": "12.0 Gbit",
        }
        for n in range(8)
    }
    ports = {
        f"ata{n + 1}": {"path": f"{host}/port-0:{n}/end_device-0:{n}/ata{n + 1}/ata_port/ata{n + 1}"} for n in range(8)
    }

    assert _port_count({"class": "0x010700", "driver": "hisi_sas_v3_hw"}, {"sas_phy": phys, "ata_port": ports}) == 8


@pytest.mark.os_agnostic
def test_a_controller_with_neither_registers_nor_phys_is_counted_from_its_ata_ports() -> None:
    """The control: libata's ports are still the answer where nothing better exists."""
    assert _port_count({"class": "0x01018a", "driver": "ata_piix"}, {"ata_port": _ata_ports(2)}) == 2


@pytest.mark.parametrize(
    ("capability", "bitmap"),
    [
        pytest.param(0xFFFFFFFF, 0xFFFFFFFF, id="master-aborted-read"),
        pytest.param(0xE7234F05, 0xFFFFFFFF, id="bitmap-all-ones"),
        pytest.param(0xFFFFFFFF, 0b11, id="capability-all-ones"),
        pytest.param(0xE7234F05, 0b1111111, id="more-implemented-than-declared"),
    ],
)
def test_an_impossible_ahci_register_reading_drops_the_port_count(capability: int, bitmap: int) -> None:
    """Rule 4: an unbelievable bitmap is no bitmap, so there is no count to guess from."""
    pci = {"class": "0x010601", "driver": "ahci", "ahci": {"capability": capability, "ports_implemented": bitmap}}

    assert _port_count(pci, {"ata_port": _ata_ports(6)}) is None


def test_a_bitmap_naming_exactly_the_declared_ports_is_still_counted() -> None:
    """The control: six declared, six implemented is a real controller."""
    pci = {"class": "0x010601", "driver": "ahci", "ahci": {"capability": 0xE7234F05, "ports_implemented": 0b111111}}

    assert _port_count(pci, {}) == 6


def test_an_ahci_class_controller_with_no_driver_is_counted_from_its_bitmap() -> None:
    """The class alone says AHCI: the declared ata_ports must not be the answer."""
    pci = {"class": "0x010601", "ahci": {"capability": 0xE7234F05, "ports_implemented": 0b11}}

    assert _port_count(pci, {"ata_port": _ata_ports(6)}) == 2


def test_a_sas_controller_whose_host_phys_publish_no_rate_never_falls_back_to_ata_ports() -> None:
    """Rule 4's second source is for controllers with no phys at all."""
    host = f"{_PATH}/host0"
    phys = {
        f"phy-0:{n}": {
            "path": f"{host}/phy-0:{n}/sas_phy/phy-0:{n}",
            "negotiated_linkrate": "Unknown",
            "maximum_linkrate_hw": "Unknown",
        }
        for n in range(4)
    }

    assert (
        _port_count({"class": "0x010700", "driver": "hisi_sas_v3_hw"}, {"sas_phy": phys, "ata_port": _ata_ports(4)})
        is None
    )
