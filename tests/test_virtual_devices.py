"""Kernel-virtual block devices: classified, kept apart, and shown on request.

A zram, loop or zvol node is part of the machine and has no transport, no
counters and no port. Both halves of that matter. Dropping it made hardware the
machine really has vanish from its own inventory; letting it into the physical
list made a device with nothing to say fail every check that asks a drive a
question.

So it is kept in its own list. `Inventory.disks` stays the physical drives every
rule and every reading is about, and `Inventory.virtual_disks` carries the rest,
which the tree and the disk table show as a summary line unless asked to list
them.
"""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import BusType

_PCI_PATH = "/sys/devices/pci0000:00/0000:00:17.0/ata5/host4/target4:0:0/4:0:0:0"


def _capture() -> dict[str, Any]:
    """A Linux capture holding one real disk and two kernel-virtual devices."""
    return {
        "platform": "linux",
        "hostname": "example",
        "block": {
            "sda": {
                "size": "1024",
                "queue": {"rotational": "0"},
                "device": {"model": "A Model"},
                "device_path": _PCI_PATH,
            },
            "zram0": {"size": "2048", "queue": {"rotational": "0"}, "virtual": True},
            "loop0": {"size": "8", "queue": {"rotational": "0"}, "virtual": True},
        },
    }


@pytest.mark.os_agnostic
class TestTheInventoryKeepsThemApart:
    """Which list a device lands in is what every later rule reads."""

    def test_a_physical_disk_is_a_disk(self) -> None:
        assert [disk.node for disk in build_from(_capture()).disks] == ["sda"]

    def test_a_virtual_device_is_not_in_the_physical_list(self) -> None:
        assert "zram0" not in [disk.node for disk in build_from(_capture()).disks]

    def test_every_virtual_device_is_carried_separately(self) -> None:
        assert [disk.node for disk in build_from(_capture()).virtual_disks] == ["loop0", "zram0"]

    def test_a_virtual_device_says_which_bus_it_is_on(self) -> None:
        """`unknown` reads as "could not be determined", which is wrong here.

        Nothing failed to be read: the kernel says this device has no transport,
        and `virtual` says exactly that.
        """
        buses = {disk.bus for disk in build_from(_capture()).virtual_disks}
        assert buses == {BusType.VIRTUAL}


@pytest.mark.os_agnostic
class TestNoRuleJudgesAVirtualDevice:
    """A device with no link and no SMART must raise nothing, ever."""

    def test_no_finding_names_a_virtual_device(self) -> None:
        findings = diagnose(build_from(_capture()))
        virtual_paths = {disk.path for disk in build_from(_capture()).virtual_disks}
        assert not [finding for finding in findings if finding.subject in virtual_paths]
