"""The ext4 and xfs paths of the filesystems section, held against a real machine.

Every other ext4/xfs test drives a hand-built capture or ``FakeFsmap``, so what they prove is
that the code agrees with what its author expected a kernel to say. ``linux-ext4-xfs-filesystems``
was taken by the production reader on a Linux 6.8 virtual machine with one ext4 and one xfs disk
mounted, and the figures below are what that machine's own tools reported at the capture, not
what lsdsk computed: ``df -B1`` for size, used and available, ``e2freefrag`` for the ext4 free
extents and ``xfs_spaceman -c freesp`` for the xfs ones. A replay that disagrees with them is
lsdsk reading the kernel's answer wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from lsdsk.adapters.hw.snapshot import build_from

if TYPE_CHECKING:
    from lsdsk.domain.models import Filesystem, Inventory

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-ext4-xfs-filesystems.json"


class GroundTruth(NamedTuple):
    """What the machine's own tools said about one mount."""

    fs_type: str
    disk: str
    size: int
    used: int
    available: int
    free_extents: int
    free_extent_bytes: int
    largest_free_extent: int


#: df -B1, then e2freefrag (block size 4096: 973009 free blocks, max 707200 KB) and
#: xfs_spaceman freesp (993481 free blocks in 103 extents, the largest 262003 blocks, the top
#: bucket 4 extents of 955428 blocks).
MEASURED: dict[str, GroundTruth] = {
    "/mnt/ext4": GroundTruth("ext4", "sdb", 4141580288, 156135424, 3754024960, 83, 973009 * 4096, 707200 * 1024),
    "/mnt/xfs": GroundTruth("xfs", "sdc", 4225761280, 271032320, 3954728960, 103, 993481 * 4096, 262003 * 4096),
}


def _machine() -> Inventory:
    payload: dict[str, Any] = json.loads(CAPTURE.read_text(encoding="utf-8"))
    return build_from(payload)


def _mount(machine: Inventory, name: str) -> Filesystem:
    assert machine.filesystems is not None, "the capture carries no filesystem reading at all"
    found = [fs for fs in machine.filesystems if fs.name == name]
    assert len(found) == 1, f"{name}: {len(found)} rows - the capture no longer holds this mount"
    return found[0]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", sorted(MEASURED))
def test_the_space_figures_are_what_df_said(name: str) -> None:
    fs = _mount(_machine(), name)
    truth = MEASURED[name]
    assert (fs.fs_type, fs.disks) == (truth.fs_type, (truth.disk,))
    assert fs.size_bytes == truth.size
    assert fs.used_bytes == truth.used
    assert fs.free_bytes == truth.available, "free is what a user can still write: df's available"
    assert not fs.readings_refused


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", sorted(MEASURED))
def test_the_free_extents_are_what_the_filesystem_tools_said(name: str) -> None:
    fs = _mount(_machine(), name)
    truth = MEASURED[name]
    assert fs.free_space is not None, f"{name}: no free-extent reading, so GETFSMAP did not reach the row"
    assert fs.free_space.free_extent_count == truth.free_extents
    assert fs.free_space.free_extent_bytes == truth.free_extent_bytes
    assert fs.free_space.largest_free_extent_bytes == truth.largest_free_extent
    assert fs.free_space.fragmentation_percent is not None
    # The buckets hold BYTES, so together they are the free-extent total, not its count.
    assert sum(fs.free_space.histogram) == truth.free_extent_bytes, "every free byte lands in one bucket"


@pytest.mark.os_agnostic
def test_xfs_free_extents_exceed_what_statvfs_calls_free() -> None:
    """GETFSMAP counts xfs's reserved blocks as free extents; statvfs does not count them as free.

    Measured on this machine: 993481 free blocks by GETFSMAP against 965510 by statvfs, about
    109 MiB of reservation. On ext4 the two agree. A test that compared ``free_extent_bytes`` with
    ``free_bytes`` on xfs would read that reservation as a defect.
    """
    machine = _machine()
    xfs, ext4 = _mount(machine, "/mnt/xfs"), _mount(machine, "/mnt/ext4")
    assert xfs.free_space is not None
    assert ext4.free_space is not None
    assert xfs.free_space.free_extent_bytes is not None
    assert xfs.free_bytes is not None
    assert xfs.free_space.free_extent_bytes > xfs.free_bytes
    assert ext4.free_space.free_extent_bytes == 973009 * 4096, "the ext4 control: GETFSMAP equals statvfs bfree"


@pytest.mark.os_agnostic
def test_the_largest_xfs_bucket_is_what_freesp_counted() -> None:
    fs = _mount(_machine(), "/mnt/xfs")
    assert fs.free_space is not None
    assert fs.free_space.histogram[-1] == 955428 * 4096
