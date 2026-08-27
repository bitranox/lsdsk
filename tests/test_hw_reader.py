"""What the Linux reader accepts as a disk.

A block device that is not backed by hardware has no transport and no SMART, so
letting one into the inventory produces a disk that can never answer any of the
questions this tool asks. The reader filtered those by NAME PREFIX, and the list
was written from the names known at the time.

These run on every platform: the reader is importable anywhere and `read_block`
takes its sysfs root as an argument, so a temporary tree stands in for `/sys`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.linux.reader import is_physical_disk, read_block

if TYPE_CHECKING:
    from pathlib import Path


def _make_block_device(sysfs: Path, device_path: str, node: str, *, physical: bool) -> None:
    """Create one block device under `sysfs`, linked from /sys/block as the kernel does."""
    target = sysfs / device_path / node
    (target / "queue").mkdir(parents=True)
    (target / "size").write_text("1024\n", encoding="utf-8")
    (target / "queue" / "rotational").write_text("0\n", encoding="utf-8")
    if physical:
        (target / "device").mkdir()
        (target / "device" / "model").write_text("A Model\n", encoding="utf-8")
    block = sysfs / "block"
    block.mkdir(exist_ok=True)
    (block / node).symlink_to(target)


@pytest.fixture
def sysfs(tmp_path: Path) -> Path:
    """A sysfs tree holding one real disk and one kernel-virtual block device."""
    root = tmp_path / "sys"
    _make_block_device(root, "devices/pci0000:00/0000:00:17.0/ata5/host4/block", "sda", physical=True)
    _make_block_device(root, "devices/virtual/block", "zram0", physical=False)
    return root


class TestOnlyRealHardwareIsADisk:
    """The inventory holds disks, so a RAM-backed device must not reach it."""

    def test_a_real_disk_is_read(self, sysfs: Path) -> None:
        assert "sda" in read_block(sysfs / "block")

    def test_a_kernel_virtual_device_is_not_a_disk(self, sysfs: Path) -> None:
        """zram is RAM, so it reports no transport and no counters, forever.

        Measured on five Proxmox hosts: `zram0` reached the inventory as a disk
        on a bus called `unknown`, and the hardware contract then failed on
        every one of them for a device that cannot pass it.
        """
        assert "zram0" not in read_block(sysfs / "block")


class TestTheNameIsNotTheAnswer:
    """Why the kernel's own placement decides this and the name does not."""

    def test_the_name_filter_does_not_recognise_zram(self) -> None:
        """Pinned deliberately: this is the gap, not an oversight to fix here.

        `zram0` starts with none of the known virtual prefixes - `ram` does not
        match it - so a name test calls it physical. Extending the list would
        fix this one name and leave the next one to be found the same way.
        """
        assert is_physical_disk("zram0")
        assert not is_physical_disk("ram0")
