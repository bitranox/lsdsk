"""Whether root could open a Linux machine's disks, asked before anything is read.

The interactive view asks for a sudo password only where root would read more.
In a container ``/sys/block`` lists the host's disks while ``/dev`` holds none
of their nodes, so root cannot open them either and a password buys nothing.
``disk_nodes_reachable`` answers that from the two directories alone, with the
same rule the scan itself records as ``devices_accessible``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.linux.reader import disk_nodes_reachable

if TYPE_CHECKING:
    from pathlib import Path


def _machine(tmp_path: Path, *, listed: tuple[str, ...], nodes: tuple[str, ...]) -> tuple[Path, Path]:
    sys_block = tmp_path / "sys" / "block"
    dev = tmp_path / "dev"
    sys_block.mkdir(parents=True)
    dev.mkdir()
    for name in listed:
        (sys_block / name).mkdir()
    for name in nodes:
        (dev / name).touch()
    return sys_block, dev


@pytest.mark.os_agnostic
def test_a_machine_with_its_disk_nodes_is_reachable(tmp_path: Path) -> None:
    sys_block, dev = _machine(tmp_path, listed=("sda", "nvme0n1", "loop0"), nodes=("sda",))

    assert disk_nodes_reachable(sys_block, dev) is True


@pytest.mark.os_agnostic
def test_a_container_listing_the_host_s_disks_with_no_nodes_is_not(tmp_path: Path) -> None:
    sys_block, dev = _machine(tmp_path, listed=("sda", "nvme0n1", "loop0"), nodes=("loop0", "null"))

    assert disk_nodes_reachable(sys_block, dev) is False


@pytest.mark.os_agnostic
def test_a_machine_listing_no_disk_lsdsk_opens_is_not_judged_unreachable(tmp_path: Path) -> None:
    """With nothing to open there is no evidence either way, so the question is still worth asking."""
    sys_block, dev = _machine(tmp_path, listed=("vda", "loop0"), nodes=())

    assert disk_nodes_reachable(sys_block, dev) is True


@pytest.mark.os_agnostic
def test_a_missing_sys_block_is_not_judged_unreachable(tmp_path: Path) -> None:
    assert disk_nodes_reachable(tmp_path / "absent", tmp_path) is True
