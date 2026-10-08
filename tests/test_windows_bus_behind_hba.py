"""A SATA drive Windows reaches through a SAS or RAID adapter is still a SATA drive.

Windows names the transport the OS speaks to the drive, so a SATA disk behind an
LSI HBA or an Intel RST RAID volume is reported as ``sas``, ``scsi`` or ``raid``.
The Windows builder used to decode IDENTIFY only for a SATA or USB bus, so such a
drive lost its identity, its negotiated link and its kind, and its bus differed
from what the Linux builder reports for the same drive ("a drive that answers
ATA IDENTIFY is SATA"). These tests replay the committed Windows capture with its
disk's transport rewritten.
"""

from __future__ import annotations

import base64
import copy
import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.enums import BusType, DiskKind

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


def _with_six_gigabit_link(identify: str) -> str:
    """Re-encode an IDENTIFY answer so it declares a drive capable of, and running at, 6 Gb/s.

    The committed capture is a QEMU guest, whose virtual drive reports no link, so
    the figure is planted in words 76 (capabilities) and 77 (current speed), and word 217 declares it solid state.
    """
    words = list(struct.unpack("<256H", base64.b64decode(identify)[:512]))
    words[76] = 1 << 3
    words[77] = 3 << 1
    words[217] = 1
    return base64.b64encode(struct.pack("<256H", *words)).decode("ascii")


def _capture_with_transport(transport: str) -> dict[str, Any]:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    (entry,) = payload["disks"].values()
    entry["device"]["bus_type"] = transport
    entry["ata"]["identify"] = _with_six_gigabit_link(entry["ata"]["identify"])
    return copy.deepcopy(payload)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("transport", ["sata", "sas", "scsi", "raid"])
def test_a_sata_drive_keeps_its_identity_and_link_whatever_transport_windows_names(transport: str) -> None:
    disk = build_from(_capture_with_transport(transport)).disks[0]
    assert disk.bus is BusType.SATA
    assert disk.link.negotiated_gbps == 6.0
    assert disk.kind is DiskKind.SSD
    assert disk.serial == "EAS39CR"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("transport", ["sas", "raid"])
def test_the_bus_matches_the_one_the_unchanged_sata_capture_reports(transport: str) -> None:
    same = build_from(_capture_with_transport("sata")).disks[0]
    behind_hba = build_from(_capture_with_transport(transport)).disks[0]
    assert (behind_hba.bus, behind_hba.kind, behind_hba.link) == (same.bus, same.kind, same.link)


@pytest.mark.os_agnostic
def test_a_sas_drive_that_answers_no_identify_stays_sas() -> None:
    payload = _capture_with_transport("sas")
    (entry,) = payload["disks"].values()
    entry["ata"] = {"identify_error": "passthrough refused, Win32 error 1; SAT refused, SCSI status 0x02"}
    assert build_from(payload).disks[0].bus is BusType.SAS


@pytest.mark.os_agnostic
def test_a_virtual_disk_that_answers_identify_keeps_its_virtual_bus() -> None:
    assert build_from(_capture_with_transport("virtual")).disks[0].bus is BusType.VIRTUAL
