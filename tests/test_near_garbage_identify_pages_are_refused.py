"""An IDENTIFY page that is nearly noise is refused, a real one never is.

All zeros and all ones were already refused. A bus that answers mostly with
floating lines still gets past that with one word different: 0xFFFF everywhere
bar one zero, or a 0x2020 fill, decoded to a SATA drive named from U+FFFD with
2^64-1 sectors, and promoted a SAS disk to SATA because it "answered IDENTIFY".
The checks are on what ATA fixes: the three strings are ASCII, the sector count
fits the 48-bit LBA field (and the 28-bit one), and a rotation rate is an RPM.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.decode.ata_identify import (
    IDENTIFY_LENGTH,
    decode_identify,
    decode_vpd_ata_information,
)
from lsdsk.domain.enums import DiskKind

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _page(**fields: int) -> bytes:
    """A plausible IDENTIFY page: model, serial, firmware and a 28-bit LBA count, then the overrides."""
    words = [0] * 256
    words[27:30] = [0x4142, 0x4344, 0x4546]  # model "ABCDEF"
    words[10:12] = [0x3132, 0x3334]  # serial "1234"
    words[23:25] = [0x4657, 0x3100]  # firmware "FW1"
    words[60], words[61] = 0x1000, 0x0001
    for name, value in fields.items():
        words[int(name.removeprefix("w"))] = value
    return struct.pack("<256H", *words)


def _fill(word: int, *, except_word: int | None = None) -> bytes:
    words = [word] * 256
    if except_word is not None:
        words[except_word] = 0
    return struct.pack("<256H", *words)


@pytest.mark.os_agnostic
def test_the_plausible_page_the_other_cases_start_from_decodes() -> None:
    """Liveness: every refusal below is the planted defect, not the base page."""
    identity = decode_identify(_page())

    assert (identity.model, identity.serial, identity.firmware) == ("ABCDEF", "1234", "FW1")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "page",
    [_fill(0xFFFF, except_word=0), _fill(0xFFFF, except_word=255), _fill(0x2020)],
    ids=["ones-with-a-zero-first", "ones-with-a-zero-last", "space-fill"],
)
def test_a_page_that_is_nearly_noise_is_refused(page: bytes) -> None:
    with pytest.raises(ValueError, match="IDENTIFY"):
        decode_identify(page)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["w27", "w10", "w23"])
def test_a_string_field_of_non_ascii_bytes_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match="not ASCII"):
        decode_identify(_page(**{field: 0xE9E9}))


@pytest.mark.os_agnostic
def test_a_sector_count_beyond_48_bits_is_refused() -> None:
    with pytest.raises(ValueError, match="sector count"):
        decode_identify(_page(w103=0x0001))


@pytest.mark.os_agnostic
def test_a_28_bit_sector_count_beyond_28_bits_is_refused() -> None:
    with pytest.raises(ValueError, match="sector count"):
        decode_identify(_page(w61=0x1000))


@pytest.mark.os_agnostic
def test_the_largest_real_sector_counts_are_kept() -> None:
    identity = decode_identify(_page(w60=0xFFFF, w61=0x0FFF, w100=0xFFFF, w101=0xFFFF, w102=0xFFFF))

    assert identity.sectors == 2**48 - 1


@pytest.mark.os_agnostic
def test_a_rotation_rate_no_drive_spins_at_is_not_a_rotation_rate() -> None:
    identity = decode_identify(_page(w217=0xFFF0))

    assert identity.kind is DiskKind.UNKNOWN
    assert identity.rotation_rpm is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rpm", [5400, 7200, 10_000, 15_000])
def test_a_real_rotation_rate_is_kept(rpm: int) -> None:
    identity = decode_identify(_page(w217=rpm))

    assert (identity.kind, identity.rotation_rpm) == (DiskKind.HDD, rpm)


@pytest.mark.os_agnostic
def test_the_vpd_wrapper_refuses_the_same_noise() -> None:
    page = bytes(60) + _fill(0xFFFF, except_word=0)

    with pytest.raises(ValueError, match="IDENTIFY"):
        decode_vpd_ata_information(page)


def _section(value: object) -> dict[str, Any]:
    """A JSON object from a capture, or an empty one when the capture holds none."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _committed_identify_blobs() -> list[tuple[str, bytes]]:
    blobs: list[tuple[str, bytes]] = []
    for capture in sorted(FIXTURES.glob("*.json")):
        data = _section(json.loads(capture.read_text(encoding="utf-8")))
        for node, ata in _section(data.get("ata")).items():
            if _section(ata).get("identify"):
                blobs.append((f"{capture.name}:{node}:identify", base64.b64decode(ata["identify"])))
        for node, block in _section(data.get("block")).items():
            page = _section(_section(block).get("vpd")).get("vpd_pg89")
            if page:
                blobs.append((f"{capture.name}:{node}:vpd", base64.b64decode(page)[60:]))
        for entry in _section(data.get("disks")).values():
            identify = _section(_section(entry).get("ata")).get("identify")
            if identify:
                blobs.append((f"{capture.name}:{entry['node']}:identify", base64.b64decode(identify)))
    return blobs


@pytest.mark.os_agnostic
def test_every_identify_page_in_every_committed_capture_still_decodes() -> None:
    blobs = _committed_identify_blobs()
    assert len(blobs) > 50, f"only {len(blobs)} pages were examined: the walk reached too little"
    assert all(len(blob) >= IDENTIFY_LENGTH for _, blob in blobs)

    for name, blob in blobs:
        try:
            decode_identify(blob)
        except ValueError as refusal:
            pytest.fail(f"{name} is a real page and was refused: {refusal}")
