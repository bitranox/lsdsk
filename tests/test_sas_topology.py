"""A SAS controller's ports, and which phy a disk is attached through.

Two things went wrong with SAS topology, both on the committed ``linux-sas-hba``
capture. Every ``sas_phy`` entry was counted as a port, so the HBA 9500-16i
reported 21 ports where it has 16: five of its phys are virtual (no hardware
link rate at all), and behind an expander every expander phy would have been
counted too. And a disk on ``port-6:0`` was given ``phy-6:0`` by NUMBER, while
mpt3sas numbers its ports in discovery order: the drive on ``port-6:0`` runs
6 Gb/s while ``phy-6:0`` reads ``Unknown``. A native SAS drive took a
neighbour's negotiated rate and port maximum that way.

The phy a disk is attached through is now PROVEN or left unread. The kernel
links each phy to the port it belongs to (``sas_port_add_phy`` creates the
``port`` link on the phy and the phy's link on the port in the same call), the
reader records that link as the phy's ``port``, and the builder joins the port
directly above the disk's ``end_device`` to the one phy that names it. A capture
that carries no such link - every committed one - leaves both ends of that link
unread rather than borrowing one, because an unread end is never a capable end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.linux.reader import read_classes
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.enums import BusType

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

_HBA_ADDRESS = "0000:03:00.0"
_HBA = f"/sys/devices/pci0000:00/0000:00:03.0/{_HBA_ADDRESS}"


def _load(name: str) -> dict[str, Any]:
    """One committed capture, decoded."""
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _phy(path: str, *, negotiated: str, maximum: str, port: str | None) -> dict[str, str]:
    """One ``sas_phy`` class entry as the reader records it."""
    entry = {"path": path, "negotiated_linkrate": negotiated, "maximum_linkrate_hw": maximum}
    if port is not None:
        entry["port"] = port
    return entry


def _native_sas_disk(device_path: str) -> dict[str, Any]:
    """A block entry for a SAS drive, which answers no ATA IDENTIFY."""
    return {
        "size": "7814037168",
        "device_path": device_path,
        "device": {"model": "SAS4000", "rev": "E002"},
        "queue": {"rotational": "1"},
        "vpd": {},
    }


def _expander_capture(*, with_ports: bool) -> dict[str, Any]:
    """An HBA whose four host phys form one wide port to an expander, and a SAS drive behind it.

    The drive hangs off expander phy ``phy-6:0:5``, whose rates (3.0 of 6.0) are
    chosen to differ from host phy ``phy-6:0`` (6.0 of 12.0), the one a by-number
    lookup of the first ``port-6:0`` in the path borrowed. ``phy-6:16`` is a
    virtual phy, publishing no hardware rate. Eight expander phys are present so
    a count that took every ``sas_phy`` entry is visibly wrong.
    """
    phys: dict[str, dict[str, str]] = {}
    for number in range(4):
        phys[f"phy-6:{number}"] = _phy(
            f"{_HBA}/host6/phy-6:{number}/sas_phy/phy-6:{number}",
            negotiated="6.0 Gbit" if number == 0 else "12.0 Gbit",
            maximum="12.0 Gbit",
            port="port-6:0" if with_ports else None,
        )
    phys["phy-6:16"] = _phy(
        f"{_HBA}/host6/phy-6:16/sas_phy/phy-6:16", negotiated="3.0 Gbit", maximum="Unknown", port=None
    )
    expander = f"{_HBA}/host6/port-6:0/expander-6:0"
    for number in range(8):
        attached = number == 5
        phys[f"phy-6:0:{number}"] = _phy(
            f"{expander}/phy-6:0:{number}/sas_phy/phy-6:0:{number}",
            negotiated="3.0 Gbit" if attached else "Unknown",
            maximum="6.0 Gbit",
            port="port-6:0:5" if attached and with_ports else None,
        )
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "sas-expander",
        "kernel": "6.8.0",
        "pci": {_HBA_ADDRESS: {"class": "0x010700", "driver": "mpt3sas", "path": _HBA}},
        "classes": {"sas_phy": phys},
        "block": {"sdzz": _native_sas_disk(f"{expander}/port-6:0:5/end_device-6:0:5/target6:0:5/6:0:5:0")},
    }


def _discovery_order_capture() -> dict[str, Any]:
    """A drive on ``port-6:1`` that is really attached through ``phy-6:7``.

    mpt3sas numbers its ports in the order it discovers them, so the port's
    number names no phy. ``phy-6:1`` here has trained no link at all.
    """
    phys = {
        "phy-6:1": _phy(f"{_HBA}/host6/phy-6:1/sas_phy/phy-6:1", negotiated="Unknown", maximum="12.0 Gbit", port=None),
        "phy-6:7": _phy(
            f"{_HBA}/host6/phy-6:7/sas_phy/phy-6:7", negotiated="12.0 Gbit", maximum="12.0 Gbit", port="port-6:1"
        ),
    }
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "sas-direct",
        "kernel": "6.8.0",
        "pci": {_HBA_ADDRESS: {"class": "0x010700", "driver": "mpt3sas", "path": _HBA}},
        "classes": {"sas_phy": phys},
        "block": {"sdzy": _native_sas_disk(f"{_HBA}/host6/port-6:1/end_device-6:1/target6:0:1/6:0:1:0")},
    }


@pytest.mark.os_agnostic
def test_a_sas_hba_counts_only_its_host_phys_that_publish_a_hardware_rate() -> None:
    """The 9500-16i has sixteen phys; the other five entries under it are virtual."""
    controllers = {controller.address: controller for controller in build_from(_load("linux-sas-hba")).controllers}

    assert controllers["0000:03:00.0"].port_count == 16
    assert controllers["0000:03:00.0"].ports_free == 6
    assert controllers["0000:04:00.0"].port_count == 8
    assert controllers["0000:04:00.0"].ports_free == 0


@pytest.mark.os_agnostic
def test_an_expanders_phys_are_never_counted_as_the_hbas_ports() -> None:
    """Four host phys with a hardware rate, whatever hangs behind them."""
    (controller,) = build_from(_expander_capture(with_ports=True)).controllers

    assert controller.port_count == 4


@pytest.mark.os_agnostic
def test_a_disk_behind_an_expander_reads_the_expander_phy_it_is_attached_through() -> None:
    (disk,) = build_from(_expander_capture(with_ports=True)).disks

    assert disk.bus is BusType.SAS
    assert disk.link.negotiated_gbps == 3.0
    assert disk.link.port_max_gbps == 6.0


@pytest.mark.os_agnostic
def test_a_port_whose_phy_is_not_recorded_leaves_both_ends_of_the_link_unread() -> None:
    """Every committed capture is this case: nothing says which phy a port holds."""
    (disk,) = build_from(_expander_capture(with_ports=False)).disks

    assert disk.bus is BusType.SAS
    assert disk.link.negotiated_gbps is None
    assert disk.link.port_max_gbps is None


@pytest.mark.os_agnostic
def test_a_port_number_never_names_the_phy_behind_it() -> None:
    """``port-6:1`` holds ``phy-6:7``, and the drive's link is that phy's."""
    (disk,) = build_from(_discovery_order_capture()).disks

    assert disk.link.negotiated_gbps == 12.0
    assert disk.link.port_max_gbps == 12.0


@pytest.mark.os_agnostic
def test_no_drive_on_a_committed_sas_capture_borrows_a_phy_it_was_not_proved_to_use() -> None:
    """The captures record no phy-to-port link, so no SAS drive's port end was read."""
    for name in ("linux-sas-hba", "linux-sas-hba-later"):
        inventory = build_from(_load(name))
        sas_disks = [disk for disk in inventory.disks if disk.controller_address in {"0000:03:00.0", "0000:04:00.0"}]
        assert len(sas_disks) == 18, name
        assert all(disk.link.port_max_gbps is None for disk in sas_disks), name


@pytest.mark.os_posix
def test_the_reader_records_the_port_each_phy_belongs_to(tmp_path: Path) -> None:
    """The phy's own ``port`` link, read through its class entry, reaches the builder.

    Built as the kernel lays it out: ``/sys/class/sas_phy/<phy>`` resolves to
    ``<host>/<phy>/sas_phy/<phy>``, whose ``device`` link names ``<host>/<phy>``,
    which carries a ``port`` link to the port it belongs to.
    """
    host = tmp_path / "devices" / "pci0000:00" / "0000:03:00.0" / "host6"
    port = host / "port-6:1"
    port.mkdir(parents=True)
    classes = tmp_path / "class" / "sas_phy"
    classes.mkdir(parents=True)
    for number, linked in ((1, False), (7, True)):
        device = host / f"phy-6:{number}"
        member = device / "sas_phy" / f"phy-6:{number}"
        member.mkdir(parents=True)
        (member / "maximum_linkrate_hw").write_text("12.0 Gbit\n", encoding="utf-8")
        (member / "negotiated_linkrate").write_text("12.0 Gbit\n" if linked else "Unknown\n", encoding="utf-8")
        (member / "device").symlink_to(device)
        if linked:
            (device / "port").symlink_to(port)
        (classes / f"phy-6:{number}").symlink_to(member)

    phys = read_classes(tmp_path / "class")["sas_phy"]

    assert phys["phy-6:7"]["port"] == "port-6:1"
    assert "port" not in phys["phy-6:1"]
    assert phys["phy-6:7"]["path"] == os.path.realpath(classes / "phy-6:7")
