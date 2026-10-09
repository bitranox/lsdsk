"""Thousands of mounts of one device cost time in proportion to their number.

Mountpoints were deduplicated with ``not in`` on a list, so 20,000 bind mounts
of one device took five seconds in ``lsdsk disks`` and 40,000 twelve.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.usage import resolve_usage
from lsdsk.domain.enums import Environment

SMALL, LARGE = 4_000, 16_000


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
    return LinuxCapture.model_validate(reading)


def _best_seconds(capture: LinuxCapture) -> float:
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        resolve_usage(capture, Environment.BARE_METAL)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.os_agnostic
def test_resolving_the_usage_of_many_bind_mounts_grows_linearly() -> None:
    small, large = _binds(SMALL), _binds(LARGE)
    # Control: every mountpoint is distinct and reaches the disk, so the work
    # being timed is the one that scales (a deduplicating shortcut would not).
    usage = resolve_usage(large, Environment.BARE_METAL)["sdc"]
    assert usage is not None
    assert len(usage.uses[0].mounts) == LARGE

    ratio = _best_seconds(large) / _best_seconds(small)
    # Four times the mounts: linear is near 4, a list scan per mount near 16.
    assert ratio < 9, f"{LARGE} mounts cost {ratio:.1f}x the time of {SMALL}"
