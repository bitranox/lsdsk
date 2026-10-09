"""A Windows IDENTIFY blob too short to decode leaves the disk without identity, not without a disk."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.enums import BusType, DiskKind

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


@pytest.mark.os_agnostic
def test_a_sas_disk_answering_a_ten_byte_identify_is_built_with_no_identity_and_its_bus() -> None:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    (entry,) = payload["disks"].values()
    entry["device"]["bus_type"] = "sas"
    entry["rotating"] = True
    entry["ata"]["identify"] = base64.b64encode(bytes(10)).decode("ascii")
    disk = build_from(payload).disks[0]
    assert (disk.bus, disk.kind, disk.link.negotiated_gbps) == (BusType.SAS, DiskKind.HDD, None)
