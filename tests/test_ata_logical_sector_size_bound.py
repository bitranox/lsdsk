"""An implausible logical sector size must not inflate a disk's capacity.

``AtaIdentity.size_bytes`` multiplies the sector count by ``sector_size``, and
the Linux builder's ``_size_bytes`` falls back to it whenever sysfs could not
report a size. Words 117-118 of IDENTIFY publish the logical sector size in
16-bit words only when word 106 says so, and nothing in the decoder checked
that value for plausibility before multiplying it in: the committed
``linux-sas-hba`` capture's own ``sda`` decodes word 106 as ``0x4000`` (the
"larger than 512 bytes" bit clear, so the fallback of 512 applies), but a
device publishing ``0x5000`` there together with ``117``/``118`` left at
``0xFFFF`` - the all-ones "not reported" pattern elsewhere in IDENTIFY - was
read as a 131070-byte logical sector and multiplied straight into the disk's
reported capacity.
"""

from __future__ import annotations

import base64
import copy
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.hw.decode.ata_identify import decode_identify
from lsdsk.adapters.hw.snapshot import build_from

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hw"

#: A realistic absurd value: words 117-118 left at 0xFFFF, the all-ones
#: "nothing reported" pattern, multiplied out to a logical sector of 131070
#: bytes - more than ten times the largest format any ATA device ships.
_IMPLAUSIBLE_WORDS_117_118 = 0xFFFF


def _words(overrides: dict[int, int]) -> list[int]:
    """A 256-word IDENTIFY buffer of zeros with the given words set."""
    words = [0] * 256
    for index, value in overrides.items():
        words[index] = value
    return words


def _blob(words: list[int]) -> bytes:
    return struct.pack("<256H", *words)


def _lba48(sectors: int) -> dict[int, int]:
    """Words 100-103 for an LBA48 sector count."""
    return {
        100: sectors & 0xFFFF,
        101: (sectors >> 16) & 0xFFFF,
        102: (sectors >> 32) & 0xFFFF,
        103: (sectors >> 48) & 0xFFFF,
    }


@pytest.mark.os_agnostic
def test_a_real_4kn_drive_still_reports_its_true_sector_size() -> None:
    """Control: a real 4Kn drive (2048 words = 4096 bytes) decodes plainly."""
    overrides = {106: 0x5000, 117: 2048, 118: 0}
    overrides.update(_lba48(1_000_000_000))
    identity = decode_identify(_blob(_words(overrides)))

    assert identity.sector_size == 4096
    assert identity.sectors == 1_000_000_000
    assert identity.size_bytes == 1_000_000_000 * 4096


@pytest.mark.os_agnostic
def test_an_implausible_logical_sector_size_is_not_trusted_into_a_capacity() -> None:
    """RED on the unfixed source: words 117/118 = 0xFFFF must not multiply in.

    Unfixed, ``_sector_geometry`` computes
    ``(0xFFFF | (0xFFFF << 16)) * 2 == 8589934590`` bytes per sector and
    multiplies it by the sector count below, which is exactly the shape of
    the reproduced defect (a few-TB drive reporting tens of exabytes).
    """
    overrides = {106: 0x5000, 117: _IMPLAUSIBLE_WORDS_117_118, 118: _IMPLAUSIBLE_WORDS_117_118}
    overrides.update(_lba48(7_814_037_168))
    identity = decode_identify(_blob(_words(overrides)))

    assert identity.size_bytes is None, (
        f"an implausible logical sector size must not be multiplied into a capacity, got {identity.size_bytes}"
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize("logical_words", [0, 1, 256, 2049, 0xFFFF, 0xFFFFFFFF])
def test_logical_sector_sizes_outside_the_real_world_range_are_refused(logical_words: int) -> None:
    """Unit arm: every boundary value outside a real device's range is refused."""
    overrides = {
        106: 0x5000,
        117: logical_words & 0xFFFF,
        118: (logical_words >> 16) & 0xFFFF,
    }
    overrides.update(_lba48(1_000_000))
    identity = decode_identify(_blob(_words(overrides)))

    assert identity.size_bytes is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("logical_words", [257, 1024, 2048])
def test_logical_sector_sizes_inside_the_real_world_range_are_accepted(logical_words: int) -> None:
    """Unit arm: the boundary values a real device can publish still decode."""
    overrides = {
        106: 0x5000,
        117: logical_words & 0xFFFF,
        118: (logical_words >> 16) & 0xFFFF,
    }
    overrides.update(_lba48(1_000_000))
    identity = decode_identify(_blob(_words(overrides)))

    assert identity.sector_size == logical_words * 2
    assert identity.size_bytes == 1_000_000 * logical_words * 2


def _load_capture(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


@pytest.mark.os_agnostic
def test_a_mutated_capture_with_an_absurd_logical_sector_drops_the_size_entirely() -> None:
    """End to end: a replay of a mutated capture never prints an absurd capacity.

    Builds the mutated capture in this test from the committed
    ``linux-sas-hba`` fixture (never a new committed file): ``sda``'s own
    IDENTIFY blob is decoded, words 106/117/118 are overwritten to the
    implausible shape reproduced against real output, and the mutated blob is
    spliced back into a deep copy of the capture before it is built into an
    ``Inventory`` the same way a real replay would be.
    """
    payload = copy.deepcopy(_load_capture("linux-sas-hba.json"))
    record = payload["ata"]["sda"]
    words = list(struct.unpack("<256H", base64.b64decode(record["identify"])))
    words[106] = 0x5000
    words[117] = _IMPLAUSIBLE_WORDS_117_118
    words[118] = _IMPLAUSIBLE_WORDS_117_118
    record["identify"] = base64.b64encode(_blob(words)).decode("ascii")
    # sysfs could not report a size either, which is what sends the builder to
    # the identity's own size_bytes as a fallback.
    payload["block"]["sda"]["size"] = None

    inventory = build_from(payload)
    sda = next(disk for disk in inventory.disks if disk.node == "sda")

    assert sda.size_bytes is None, f"an absurd logical sector size reached the built disk: {sda.size_bytes}"


@pytest.mark.os_agnostic
def test_the_unmutated_capture_still_reports_sdas_real_size() -> None:
    """Control: the committed capture, unmutated, keeps reporting the real size."""
    payload = _load_capture("linux-sas-hba.json")
    inventory = build_from(payload)
    sda = next(disk for disk in inventory.disks if disk.node == "sda")

    assert sda.size_bytes is not None
    assert 3 * 10**12 <= sda.size_bytes <= 5 * 10**12
