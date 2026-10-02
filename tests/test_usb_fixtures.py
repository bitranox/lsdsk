"""The USB disks in the committed captures, built end to end from what the readers recorded.

Each capture is a production-reader snapshot of one real USB disk, so these
assert what the builders and the rule make of a real machine rather than of a
hand-built source. The figures are written from the capture, not from the plan
that asked for it: the Windows one was taken with the disk on a USB-C root
port, which runs it at its own top speed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.tree import FabricView, fabric_lines
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import BusType, TreeDensity, UsbTransport
from lsdsk.domain.models import PciNode

if TYPE_CHECKING:
    from lsdsk.domain.models import Disk, Finding, Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _machine(host: str) -> Inventory:
    return build_from(json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8")))


def _usb_disk(machine: Inventory) -> Disk:
    disks = [disk for disk in machine.disks if disk.bus is BusType.USB]
    assert len(disks) == 1, [disk.path for disk in machine.disks]
    return disks[0]


def _usb_findings(machine: Inventory, disk: Disk) -> list[Finding]:
    return [finding for finding in diagnose(machine) if finding.subject == disk.path]


@pytest.mark.os_agnostic
def test_the_windows_usb_disk_runs_at_its_own_top_speed_on_a_root_port() -> None:
    """Root hub port 17, a USB-C port, runs the 10 Gb/s enclosure at 10 Gb/s on one lane."""
    disk = _usb_disk(_machine("windows-usb-uas"))
    usb = disk.usb

    assert usb is not None, "a disk Windows places under a USB device must carry its link"
    assert usb.running is not None and usb.device_max is not None
    assert (usb.running.figure, usb.running.lanes) == ("USB10G", 1)
    assert usb.device_max.figure == "USB10G"
    assert usb.transport is UsbTransport.UAS
    assert not usb.behind_hub and usb.upstream is None
    assert not usb.on_usb2_twin


@pytest.mark.os_agnostic
def test_a_windows_root_port_names_no_rate_so_nothing_is_called_achievable() -> None:
    """A root port says only that it is USB 3, so the socket end stays unread.

    The control is the drive end on the same link, which WAS read: an unread
    socket must not be filled in from it, or a USB 2 bottleneck nobody measured
    would read as fine.
    """
    machine = _machine("windows-usb-uas")
    disk = _usb_disk(machine)
    usb = disk.usb

    assert usb is not None and usb.device_max is not None
    assert usb.port_max is None
    assert usb.achievable is None
    assert not usb.is_underperforming
    assert _usb_findings(machine, disk) == []


@pytest.mark.os_agnostic
def test_the_drive_inside_the_windows_enclosure_keeps_its_own_sata_link() -> None:
    """IDENTIFY is decoded for a USB disk, so the SSD behind the bridge reports 6 Gb/s."""
    disk = _usb_disk(_machine("windows-usb-uas"))

    assert disk.link.negotiated_gbps == pytest.approx(6.0)
    assert disk.link.drive_max_gbps == pytest.approx(6.0)
    assert disk.model.startswith("SanDisk")


@pytest.mark.os_agnostic
def test_the_linux_usb_disk_is_held_to_usb_2_by_its_port() -> None:
    """The 10 Gb/s enclosure on an EHCI port behind a USB 2.0 hub, kernel 6.8.

    The BOS came through usbfs, the fallback for a kernel without
    bos_descriptors, which is what tells the device can do USB10G at all.
    """
    disk = _usb_disk(_machine("linux-usb-ehci"))
    usb = disk.usb

    assert usb is not None and usb.running is not None and usb.device_max is not None
    assert usb.port_max is not None and usb.upstream is not None
    assert usb.running.figure == "USB480M"
    assert usb.device_max.figure == "USB10G"
    assert usb.port_max.figure == "USB480M"
    assert usb.behind_hub and usb.upstream.figure == "USB480M"
    assert usb.transport is UsbTransport.UAS
    assert usb.achievable is not None and usb.achievable.figure == "USB480M"


@pytest.mark.os_agnostic
def test_the_linux_usb_disk_raises_the_port_finding_and_nothing_else_about_its_link() -> None:
    """The port is the ceiling, so the finding names the port, not the link."""
    machine = _machine("linux-usb-ehci")
    disk = _usb_disk(machine)

    titles = [finding.title for finding in _usb_findings(machine, disk)]

    assert titles == [f"{disk.model} can do USB10G (1.21 GB/s) but its port only offers USB480M (0.06 GB/s)"], titles


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", ["linux-usb-ehci", "windows-usb-uas"])
@pytest.mark.parametrize("density", list(TreeDensity), ids=str)
def test_a_usb_disk_is_drawn_under_its_own_host_controller(host: str, density: TreeDensity) -> None:
    """The host controller is a known device, so the disk hangs under it at every density.

    A USB host controller is no storage class, and the tree used to hang drives
    under storage classes only, so a disk whose controller was in the fabric
    was drawn under "not attached to a known controller" - a sentence that was
    false on both captures. The reduced densities must keep the host controller
    too, or the disk has nothing to hang under there.
    """
    machine = _machine(host)
    disk = _usb_disk(machine)
    lines = fabric_lines(machine, diagnose(machine), 200, FabricView(density=density))

    at = next(index for index, line in enumerate(lines) if line.subject == disk)
    above = [line.subject for line in lines[:at] if isinstance(line.subject, PciNode)]

    assert above, f"{host} {density.value}: the disk is drawn above every device"
    assert above[-1].address == disk.controller_address, (above[-1].address, disk.controller_address)
    # The case this exists for: a host controller the storage classes do not cover.
    assert not above[-1].is_storage, above[-1].class_code
    assert not any("not attached" in line.text.plain for line in lines)
