"""Mapping tests over captures taken from real machines.

These exercise the production path end to end short of the ioctls themselves:
a capture recorded by the real reader goes through the real builder and the real
diagnostics. Because the captures are files, the Linux mapping is tested on
Windows and the Windows mapping on Linux, which is the point of splitting the
readers from the builders.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.linux.builder import controller_address_of, controller_kind_of, parse_link_rate, parse_pcie_speed
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import BusType, ControllerKind, DiskKind, Severity

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"
LINUX_HOSTS = ("linux-sas-hba", "linux-minimal", "linux-nvme-board")
WINDOWS_HOST = "windows-ahci"


def load(host: str) -> dict[str, Any]:
    """Load one captured machine."""
    with (FIXTURE_DIR / f"{host}.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", LINUX_HOSTS)
def test_when_a_linux_capture_is_built_it_finds_controllers_and_disks(host: str) -> None:
    """Verify a real Linux capture maps to a complete inventory."""
    inventory = build_from(load(host))

    assert inventory.hostname == host
    assert inventory.controllers, "no controllers were found"
    assert inventory.disks, "no disks were found"
    assert all(disk.model for disk in inventory.disks)
    assert all(disk.size_bytes for disk in inventory.disks)
    assert any(disk.controller_address for disk in inventory.disks)


@pytest.mark.os_agnostic
def test_when_disks_hang_off_a_sas_hba_they_map_to_it() -> None:
    """Verify the disk-to-controller mapping on a real two-HBA machine."""
    inventory = build_from(load("linux-sas-hba"))
    by_address = {controller.address: controller for controller in inventory.controllers}

    assert by_address["0000:03:00.0"].name == "HBA 9500-16i"
    assert by_address["0000:03:00.0"].firmware == "23.00.00.00"
    assert by_address["0000:04:00.0"].name == "LSI SAS3008"
    assert len(inventory.disks_on("0000:03:00.0")) == 10
    assert len(inventory.disks_on("0000:04:00.0")) == 8


@pytest.mark.os_agnostic
def test_when_a_drive_sits_behind_a_sas_hba_its_sata_speed_is_still_known() -> None:
    """Verify the speed the ata_link class cannot report is recovered anyway.

    Every disk on this machine is behind a SAS HBA, where ``sata_spd`` reads
    ``<unknown>``. The speeds below come from the IDENTIFY page and the SAS phy
    instead, which is the whole reason those sources are consulted.
    """
    inventory = build_from(load("linux-sas-hba"))
    by_node = {disk.node: disk for disk in inventory.disks}

    ssd = by_node["sda"]
    assert ssd.link.negotiated_gbps == 6.0
    assert ssd.link.drive_max_gbps == 6.0
    assert ssd.link.port_max_gbps == 12.0
    assert not ssd.link.is_underperforming

    # A SATA-II drive on the same 12 Gb/s backplane: slower, but not a fault,
    # because 3 Gb/s is all the drive itself can do.
    old = by_node["sdr"]
    assert old.link.negotiated_gbps == 3.0
    assert old.link.drive_max_gbps == 3.0
    assert not old.link.is_underperforming


@pytest.mark.os_agnostic
def test_when_a_legacy_bridge_reports_no_capability_it_is_not_a_slot() -> None:
    """Verify a bridge with no PCIe capability is never a move target.

    Treating its unknown capability as unlimited once made it look like the
    fastest slot in the machine, which produced a confident recommendation to
    move a card somewhere slower than where it already was.
    """
    inventory = build_from(load("linux-sas-hba"))
    by_address = {slot.address: slot for slot in inventory.slots}

    legacy = by_address["0000:00:1e.0"]
    assert legacy.connector_present is not True
    assert not legacy.is_move_target
    assert legacy.link.max_speed_gtps is None

    internal = by_address["0000:00:11.0"]
    assert internal.connector_present is False
    assert not internal.is_move_target

    real_slot = by_address["0000:00:01.1"]
    assert real_slot.connector_present is True
    assert real_slot.is_move_target


@pytest.mark.os_agnostic
def test_when_a_slot_holds_a_display_controller_it_is_not_offered_for_swapping() -> None:
    """Verify the graphics card is never proposed as somewhere to put an HBA."""
    inventory = build_from(load("linux-sas-hba"))
    graphics = [slot for slot in inventory.slots if slot.occupant_class == 0x030000]

    assert graphics, "this machine has a graphics card, so the case is real"
    assert all(not slot.is_swap_candidate for slot in graphics)


@pytest.mark.os_agnostic
def test_when_a_network_card_wastes_a_wide_slot_it_is_a_swap_candidate() -> None:
    """Verify a card that cannot use its slot is identified as swappable."""
    inventory = build_from(load("linux-sas-hba"))
    swappable = [slot for slot in inventory.slots if slot.is_swap_candidate]

    assert swappable, "this machine has a Gen2 network card in a Gen3 slot"
    for slot in swappable:
        assert slot.occupant_need_gbps is not None
        assert slot.capability_gbps is not None
        assert slot.occupant_need_gbps < slot.capability_gbps


@pytest.mark.os_agnostic
def test_when_a_gen4_card_sits_in_a_gen3_board_it_is_a_hint_not_a_warning() -> None:
    """Verify a platform ceiling is graded down when nothing can be done.

    The HBA in this machine is PCIe 4.0 capable in a board whose every root port
    tops out at 3.0, so there is nothing to fix and nowhere better to put it.
    """
    inventory = build_from(load("linux-sas-hba"))
    findings = diagnose(inventory)
    about_hba = [finding for finding in findings if finding.subject == "0000:03:00.0"]

    assert len(about_hba) == 1
    assert about_hba[0].severity is Severity.HINT
    assert "capped by the mainboard" in about_hba[0].title
    assert about_hba[0].action is not None
    assert "PCIe 4.0 board" in about_hba[0].action


@pytest.mark.os_agnostic
def test_when_drives_report_errors_they_become_findings() -> None:
    """Verify real error counters on real drives are surfaced."""
    inventory = build_from(load("linux-sas-hba"))
    findings = diagnose(inventory)
    titles = {finding.subject: finding.title for finding in findings}

    assert "reallocated sectors" in titles["/dev/sdp"]
    assert "media errors" in titles["/dev/nvme0n1"]
    assert all(finding.severity is not Severity.CRITICAL for finding in findings)


@pytest.mark.os_agnostic
def test_when_a_windows_capture_is_built_it_maps_disks_to_controllers() -> None:
    """Verify the Windows mapping path, on a capture from a real Windows box."""
    inventory = build_from(load(WINDOWS_HOST))

    assert inventory.hostname == WINDOWS_HOST
    assert inventory.privileged is True
    assert inventory.controllers
    assert all(controller.kind is ControllerKind.AHCI for controller in inventory.controllers)

    disk = inventory.disks[0]
    assert disk.node == "PhysicalDrive0"
    assert disk.model == "QEMU HARDDISK"
    assert disk.serial == "EAS39CR"
    assert disk.bus is BusType.SATA
    assert disk.size_bytes == 536_870_912_000
    assert disk.controller_address is not None
    assert disk.controller_address.startswith("0000:")


@pytest.mark.os_agnostic
def test_when_windows_reports_a_temperature_it_reaches_the_model() -> None:
    """Verify the Windows temperature query feeds the same health model."""
    inventory = build_from(load(WINDOWS_HOST))
    health = inventory.disks[0].health

    assert health is not None
    assert health.temperature_c is not None
    assert 0 < health.temperature_c < 100


@pytest.mark.os_agnostic
def test_when_a_device_reports_no_rotation_rate_the_kind_stays_unknown() -> None:
    """Verify an emulated disk that declares nothing is not guessed at.

    The QEMU disk reports neither a rotation rate nor a seek penalty, so calling
    it either solid state or rotating would be an invention.
    """
    inventory = build_from(load(WINDOWS_HOST))

    assert inventory.disks[0].kind is DiskKind.UNKNOWN


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("text", "expected"),
    [("8.0 GT/s PCIe", 8.0), ("16.0 GT/s PCIe", 16.0), ("Unknown", None), ("", None), (None, None)],
)
def test_pcie_speed_parsing(text: str | None, expected: float | None) -> None:
    """Verify the sysfs PCIe speed strings parse, including the unknown forms."""
    assert parse_pcie_speed(text) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("text", "expected"),
    [("6.0 Gbit", 6.0), ("12.0 Gbit", 12.0), ("1.5 Gbps", 1.5), ("<unknown>", None), ("Unknown", None)],
)
def test_link_rate_parsing(text: str, expected: float | None) -> None:
    """Verify SAS and SATA rate strings parse, including both unknown spellings."""
    assert parse_link_rate(text) == expected


@pytest.mark.os_agnostic
def test_controller_kind_mapping() -> None:
    """Verify PCI class triples map to the right controller kinds."""
    assert controller_kind_of(0x010601) is ControllerKind.AHCI
    assert controller_kind_of(0x010700) is ControllerKind.SAS
    assert controller_kind_of(0x010802) is ControllerKind.NVME
    assert controller_kind_of(0x010400) is ControllerKind.RAID
    assert controller_kind_of(0x030000) is ControllerKind.UNKNOWN
    assert controller_kind_of(None) is ControllerKind.UNKNOWN


@pytest.mark.os_agnostic
def test_controller_address_is_the_last_pci_address_in_the_path() -> None:
    """Verify the endpoint, not the root port, is taken as the controller."""
    path = "/sys/devices/pci0000:00/0000:00:03.0/0000:03:00.0/host6/port-6:0/end_device-6:0"

    assert controller_address_of(path) == "0000:03:00.0"
    assert controller_address_of("/sys/devices/virtual/block/loop0") is None


@pytest.mark.os_agnostic
def test_an_ahci_controller_reports_ports_only_when_its_bitmap_says_so() -> None:
    """Verify the kernel's ata_port count is never passed off as the port count.

    libata creates one ata_port per DECLARED port, so counting them reports the
    capability register's NP field rather than the ports the board actually
    wired. On the captured linux-sas-hba chipset the firmware leaves the bitmap at
    zero, and the kernel's own log for it reads "0/6 ports implemented (port
    mask 0x0)": six sockets that are not there. Advertising them sends somebody
    looking for connectors that do not exist.

    linux-minimal is the control. Its bitmap is valid and reports two of six
    implemented, so that count must survive.
    """
    unusable_bitmap = {c.address: c for c in build_from(load("linux-sas-hba")).controllers}
    valid_bitmap = {c.address: c for c in build_from(load("linux-minimal")).controllers}

    assert unusable_bitmap["0000:00:1f.2"].port_count is None
    assert unusable_bitmap["0000:00:1f.2"].ports_free is None
    assert valid_bitmap["0000:00:1f.2"].port_count == 2


@pytest.mark.os_agnostic
def test_a_root_capture_carries_the_boards_own_slot_numbers() -> None:
    """Verify the slot number survives capture and replay.

    It is the only readable datum tying a port to something a person can point
    at, because no source reports the form factor. Cross-checked against
    lspci on the live machine: 00:01.0 is slot 1, 00:1a.0 is slot 28 and
    00:1d.4 is slot 16.
    """
    inventory = build_from(load("linux-nvme-board"))
    by_address = {slot.address: slot for slot in inventory.slots}

    assert inventory.board == "Micro-Star International Co., Ltd. MEG Z690 ACE (MS-7D27)"
    assert by_address["0000:00:01.0"].physical_slot_number == 1
    assert by_address["0000:00:1a.0"].physical_slot_number == 28
    assert by_address["0000:00:1d.4"].physical_slot_number == 16
    assert by_address["0000:00:1b.0"].physical_slot_number is None, "no connector, so no slot number"


@pytest.mark.os_agnostic
def test_an_unprivileged_capture_reports_no_slot_numbers() -> None:
    """Verify an unreadable slot number stays absent rather than becoming zero."""
    payload = load("linux-nvme-board")
    for entry in payload["pci"].values():
        entry.pop("slot_number", None)
        entry.pop("slot_implemented", None)
    payload["euid"] = 1000

    inventory = build_from(payload)

    assert not inventory.privileged
    assert all(slot.physical_slot_number is None for slot in inventory.slots)
    assert all(slot.connector_present is None for slot in inventory.slots)
