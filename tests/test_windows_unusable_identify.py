"""An IDENTIFY answer that carries no identity is no identity, and the disk keeps what Windows named.

A SAS, SCSI or RAID disk is not an ATA device, so a passthrough that "succeeds"
with a zero-filled or all-ones page, or with a page too short to be one, must
not promote it to SATA with an unknown kind. The Windows-named bus and the
seek-penalty kind stay.
"""

from __future__ import annotations

import base64
import copy
import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.decode.ata_identify import IDENTIFY_LENGTH, decode_identify
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.enums import BusType, DiskKind

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _sas_capture_answering(identify: bytes) -> dict[str, Any]:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    (entry,) = payload["disks"].values()
    entry["device"]["bus_type"] = "sas"
    entry["rotating"] = True
    entry["ata"]["identify"] = _b64(identify)
    return copy.deepcopy(payload)


UNUSABLE = {
    "zeros": bytes(IDENTIFY_LENGTH),
    "ones": b"\xff" * IDENTIFY_LENGTH,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", sorted(UNUSABLE))
def test_a_sas_disk_whose_identify_carries_no_identity_stays_sas_with_its_kind(name: str) -> None:
    disk = build_from(_sas_capture_answering(UNUSABLE[name])).disks[0]
    assert (disk.bus, disk.kind) == (BusType.SAS, DiskKind.HDD)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["zeros", "ones"])
def test_the_decoder_refuses_a_page_with_no_identity(name: str) -> None:
    with pytest.raises(ValueError, match="no identity"):
        decode_identify(UNUSABLE[name])


@pytest.mark.os_agnostic
def test_the_decoder_refuses_a_page_with_no_names_and_no_capacity() -> None:
    words = [0] * 256
    words[217] = 1
    with pytest.raises(ValueError, match="no identity"):
        decode_identify(struct.pack("<256H", *words))


@pytest.mark.os_agnostic
def test_a_page_with_only_a_capacity_is_still_an_identity() -> None:
    words = [0] * 256
    words[60] = 1000
    assert decode_identify(struct.pack("<256H", *words)).sectors == 1000


@pytest.mark.os_agnostic
def test_the_optional_signature_is_not_required() -> None:
    words = [0] * 256
    words[27] = 0x4142
    assert decode_identify(struct.pack("<256H", *words)).model == "AB"
