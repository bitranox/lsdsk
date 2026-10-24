"""A btrfs member list costs memory in proportion to its length, not to its square.

Each member of a mounted btrfs was given its own copy of the whole member
list, and the disk-usage rules rebuilt that table once per disk, so a 100 KB
replay naming 8,000 members took 12 seconds and 4.2 GB. The arm bounds the
memory the build allocates rather than its time, because a peak traced by
``tracemalloc`` is the same on every runner and every load, while a time ratio
cannot tell a loaded neighbour from a quadratic table.

The list is long enough that the square cannot fit under the bound by any
margin (20,000 members squared is 400 million table entries), and short enough
that the linear build stays far below it.
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lsdsk.adapters.hw import snapshot

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

_CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-btrfs-raid1-zoned-xfs.json"
_MEMBERS = 20_000
_PEAK_BYTES = 64 * 2**20


def _reading(extra_members: int) -> dict[str, Any]:
    reading: dict[str, Any] = json.loads(_CAPTURE.read_text(encoding="utf-8"))
    # The capture's own two-disk btrfs, joined by members no inventoried disk carries.
    reading["btrfs_devices"] = [["8:16", "8:32", *(f"9:{n}" for n in range(extra_members))]]
    return reading


def _build_traced(reading: dict[str, Any]) -> tuple[Inventory, int]:
    tracemalloc.start()
    try:
        inventory = snapshot.build_from(reading)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return inventory, peak


def test_a_btrfs_member_list_of_twenty_thousand_builds_within_a_bounded_peak() -> None:
    reading = _reading(_MEMBERS)
    inventory, peak = _build_traced(reading)
    assert peak < _PEAK_BYTES, f"building a {_MEMBERS}-member btrfs allocated {peak / 2**20:.0f} MiB at its peak"
    # Control: the long list was really read, and still joins the btrfs to both of its disks.
    filesystems = {each.name: each for each in inventory.filesystems or ()}
    assert filesystems["/mnt/b"].disks == ("sdb", "sdc")
    assert len(reading["btrfs_devices"][0]) == _MEMBERS + 2
