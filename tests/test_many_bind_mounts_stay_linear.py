"""Thousands of mounts of one device cost work in proportion to their number.

Mountpoints were deduplicated with ``not in`` on a list, so 20,000 bind mounts
of one device took five seconds in ``lsdsk disks`` and 40,000 twelve.

The arm counts work rather than timing it, because a time ratio on a loaded
runner cannot tell a slow neighbour from a quadratic loop. Counting needs one
thing more here: ``not in`` on a list compares in C and makes no Python call
and runs no Python line per element, so ``work_to_run`` alone reads the same
ratio on the quadratic code as on the linear one. The mountpoints are
therefore a ``str`` subclass whose comparison and hash are Python methods,
which makes every comparison the deduplication performs a counted call.
"""

from __future__ import annotations

from typing import Any

import pytest
from workcount import work_to_run

from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.usage import resolve_usage
from lsdsk.domain.enums import Environment

SMALL, LARGE = 1_000, 4_000


class _CountedText(str):
    """A mountpoint whose every comparison and hash is a Python call."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return str.__eq__(self, other)

    def __hash__(self) -> int:
        return str.__hash__(self)


def _binds(count: int) -> LinuxCapture:
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "linux",
        "hostname": "h",
        "kernel": "6.1",
        "pci": {},
        "block": {"sdc": {"dev": "8:32", "holders": [], "partitions": {}}},
        "mounts": [{"dev": "8:32", "mountpoint": f"/bind/{n}", "fstype": "ext4", "source": ""} for n in range(count)],
        "swaps": [],
        "stacked": {},
        "signatures": {},
    }
    capture = LinuxCapture.model_validate(reading)
    # Validation turns a str subclass back into str, so the counted text goes
    # in afterwards through copies, which do not validate.
    mounts = [mount.model_copy(update={"mountpoint": _CountedText(mount.mountpoint)}) for mount in capture.mounts or ()]
    return capture.model_copy(update={"mounts": mounts})


def _work(capture: LinuxCapture) -> int:
    usage, work = work_to_run(lambda: resolve_usage(capture, Environment.BARE_METAL))
    # Control: every mountpoint is distinct and reaches the disk, so the work
    # counted is the work that scales (a deduplicating shortcut would not).
    disk = usage["sdc"]
    assert disk is not None
    assert len(disk.uses[0].mounts) == len(capture.mounts or ())
    return work


@pytest.mark.os_agnostic
def test_the_counted_text_survives_into_the_capture() -> None:
    capture = _binds(2)

    assert all(type(mount.mountpoint) is _CountedText for mount in capture.mounts or ())


@pytest.mark.os_agnostic
def test_resolving_the_usage_of_many_bind_mounts_grows_linearly() -> None:
    ratio = _work(_binds(LARGE)) / _work(_binds(SMALL))

    # Four times the mounts: linear reads about 4.3, the list scan 14.5.
    assert ratio < 6, f"{LARGE} mounts cost {ratio:.1f}x the work of {SMALL}"
