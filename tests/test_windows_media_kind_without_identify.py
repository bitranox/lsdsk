"""The media kind of a Windows disk whose IDENTIFY answer decided nothing.

NVMe is solid state by construction, so it is decided before the seek-penalty
reading is asked: an NVMe disk whose penalty reading is absent, or contradicts
that, is still an SSD. Reading the penalty first would turn the absent reading
into ``unknown`` for a disk that cannot be anything else.
"""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.windows.builder import build_disks
from lsdsk.adapters.hw.windows.capture import WindowsCapture
from lsdsk.domain.enums import DiskKind


def _kind_of(bus_type: str, rotating: bool | None) -> DiskKind:
    """Build the one disk of a capture that carries no IDENTIFY payload and return its media kind."""
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "win32",
        "hostname": "h",
        "kernel": "10.0",
        "pci": {},
        "disks": {
            "\\\\?\\p0": {"node": "PhysicalDrive0", "device": {"bus_type": bus_type}, "rotating": rotating},
        },
    }
    (disk,) = build_disks(WindowsCapture.model_validate(reading))
    return disk.kind


@pytest.mark.parametrize(
    ("bus_type", "rotating", "expected"),
    [
        ("nvme", None, DiskKind.SSD),
        ("nvme", True, DiskKind.SSD),
        ("nvme", False, DiskKind.SSD),
        ("sata", None, DiskKind.UNKNOWN),
        ("sata", True, DiskKind.HDD),
        ("sata", False, DiskKind.SSD),
    ],
)
def test_the_media_kind_without_identify_follows_the_bus_before_the_seek_penalty(
    bus_type: str, rotating: bool | None, expected: DiskKind
) -> None:
    assert _kind_of(bus_type, rotating) is expected
