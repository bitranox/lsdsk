"""Four edges of the Linux reading that answered wrong rather than "not read".

* A ``/sys/block`` link that loops makes ``Path.resolve()`` raise
  ``RuntimeError`` on Python 3.11 and 3.12 - not ``OSError`` - and that escaped
  the virtual-device check and ended the scan.
* A mount or swap source naming a CHARACTER device (``/dev/null``, ``/dev/fuse``)
  has an ``st_rdev`` too, so it was recorded as a block device number and could
  join a mount to whatever disk shares it.
* A PCI vendor or device id is sixteen bits, and a capture is untrusted: a wider
  one was carried as a measurement.
* ``/proc/self/mountinfo`` and ``/proc/cpuinfo`` were read at the one-megabyte
  sysfs-attribute cap, so a container host's mount table that ``read_mounts``
  accepts (up to 8 MiB) lost its container marker, and a guest with enough
  vCPUs to pass a megabyte of cpuinfo lost its hypervisor flag and read as bare
  metal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.linux.mounts import read_mounts, read_swaps
from lsdsk.adapters.hw.linux.reader import MAX_SYSFS_BYTES, read_block, read_environment
from lsdsk.adapters.hw.snapshot import build_from

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# A device node every POSIX machine has, and which is a character device.
_CHARACTER_DEVICE = "/dev/null"


@pytest.mark.os_posix
def test_a_block_link_that_loops_is_read_as_real_rather_than_ending_the_scan(tmp_path: Path) -> None:
    """An unresolvable link says nothing either way, so the device stays physical."""
    block = tmp_path / "sys" / "block"
    block.mkdir(parents=True)
    (block / "sdzzloop").symlink_to(block / "sdzzloop2")
    (block / "sdzzloop2").symlink_to(block / "sdzzloop")

    entries = read_block(block)

    assert entries["sdzzloop"].get("virtual") is not True


@pytest.mark.os_posix
def test_a_character_device_is_never_recorded_as_a_mounts_block_device(tmp_path: Path) -> None:
    assert Path(_CHARACTER_DEVICE).is_char_device(), "the premise needs a character device to exist"
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(f"36 35 8:1 / /mnt rw,relatime shared:1 - ext4 {_CHARACTER_DEVICE} rw\n", encoding="utf-8")

    (row,) = read_mounts(mountinfo) or []

    assert "source_dev" not in row


@pytest.mark.os_posix
def test_a_character_device_is_never_recorded_as_a_swap_partition(tmp_path: Path) -> None:
    swaps = tmp_path / "swaps"
    swaps.write_text(f"Filename Type Size Used Priority\n{_CHARACTER_DEVICE} partition 1 0 -2\n", encoding="utf-8")

    assert read_swaps(swaps) == [{}]


def _a_block_device_node() -> str | None:
    """Any block device node this machine has, or ``None`` - a dev container has none."""
    dev = Path("/dev")
    nodes = sorted(path for path in dev.iterdir() if path.is_block_device()) if dev.is_dir() else []
    return str(nodes[0]) if nodes else None


@pytest.mark.os_posix
@pytest.mark.skipif(_a_block_device_node() is None, reason="this machine has no block device node to resolve")
def test_a_block_device_still_resolves_to_its_device_number(tmp_path: Path) -> None:
    """The control for the character-device refusal: a real block device is still a source."""
    node = _a_block_device_node()
    assert node is not None
    rdev = Path(node).stat().st_rdev
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(f"36 35 8:1 / /mnt rw,relatime shared:1 - ext4 {node} rw\n", encoding="utf-8")

    (row,) = read_mounts(mountinfo) or []

    assert row["source_dev"] == f"{os.major(rdev)}:{os.minor(rdev)}"


def _with_storage_ids(name: str, vendor: str, device: str) -> tuple[dict[str, Any], str]:
    """A committed capture whose first storage controller carries the given ids, and that controller's address."""
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    key, entry = next((key, entry) for key, entry in data["pci"].items() if str(entry.get("class")).startswith("0x01"))
    entry["vendor"], entry["device"] = vendor, device
    return data, str(entry.get("address", key))


def _controller_vendor(name: str, vendor: str) -> int | None:
    """The vendor the builder gives the controller whose ids were replaced."""
    data, address = _with_storage_ids(name, vendor, "0x0001")
    by_address = {controller.address: controller for controller in build_from(data).controllers}
    return by_address[address].vendor


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["linux-minimal", "windows-ahci"])
@pytest.mark.parametrize("vendor", ["0x123456", "-0x1"])
def test_a_pci_vendor_id_outside_sixteen_bits_is_unread(name: str, vendor: str) -> None:
    assert _controller_vendor(name, vendor) is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["linux-minimal", "windows-ahci"])
def test_a_pci_id_inside_sixteen_bits_is_still_read(name: str) -> None:
    """The control: the bound must not swallow a real identifier."""
    assert _controller_vendor(name, "0xffff") == 0xFFFF


def _proc_with(tmp_path: Path, *, mountinfo: str, cpuinfo: str) -> Path:
    """A ``/proc`` holding only the two files the size bound is about."""
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "mountinfo").write_text(mountinfo, encoding="utf-8")
    (proc / "cpuinfo").write_text(cpuinfo, encoding="utf-8")
    return proc


def _padding(after: str, *, past: int) -> str:
    """Lines of ordinary text after ``after`` until the whole passes ``past`` characters."""
    line = "processor\t: 1\nflags\t\t: fpu vme de pse tsc msr pae mce\n\n"
    return after + line * (past // len(line) + 1)


@pytest.mark.os_agnostic
def test_a_container_hosts_mount_table_past_a_megabyte_still_carries_its_marker(tmp_path: Path) -> None:
    marker = "6226 6210 0:99 /proc/cpuinfo /proc/cpuinfo rw - fuse.lxcfs lxcfs rw\n"
    filler = "412 33 0:62 / /var/lib/docker/overlay2/abcdef/merged rw - overlay overlay rw\n"
    mountinfo = marker + filler * (MAX_SYSFS_BYTES // len(filler) + 1)
    assert len(mountinfo) > MAX_SYSFS_BYTES

    evidence = read_environment(proc=_proc_with(tmp_path, mountinfo=mountinfo, cpuinfo=""))

    assert evidence.get("mount_markers") == "lxc"


@pytest.mark.os_agnostic
def test_a_guest_whose_cpuinfo_passes_a_megabyte_still_reports_the_hypervisor_flag(tmp_path: Path) -> None:
    first = "processor\t: 0\nflags\t\t: fpu vme de pse hypervisor lahf_lm\n\n"
    cpuinfo = _padding(first, past=MAX_SYSFS_BYTES)

    evidence = read_environment(proc=_proc_with(tmp_path, mountinfo="", cpuinfo=cpuinfo))

    assert evidence["hypervisor_flag"] is True


@pytest.mark.os_agnostic
def test_bare_metal_cpuinfo_reports_no_hypervisor_flag(tmp_path: Path) -> None:
    """The control: a flag line without ``hypervisor`` must still read False."""
    cpuinfo = _padding("processor\t: 0\nflags\t\t: fpu vme de pse lahf_lm\n\n", past=MAX_SYSFS_BYTES)

    evidence = read_environment(proc=_proc_with(tmp_path, mountinfo="", cpuinfo=cpuinfo))

    assert evidence["hypervisor_flag"] is False
