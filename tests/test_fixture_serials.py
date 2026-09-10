"""A committed capture must carry one serial per drive, in every copy of it.

A capture records the same drive serial in up to eight places, because the
reader stores the raw structures it read rather than a decoded summary: the ATA
IDENTIFY serial field, the copy of IDENTIFY embedded in VPD page 0x89, VPD page
0x80, two designators of VPD page 0x83, the vendor-specific tail of the standard
INQUIRY response, the NVMe Identify Controller SN field, the subsystem NQN that
embeds it in a name string, and the sysfs ``wwid`` that embeds it as hex.

Fixtures are captures from real machines with the serials replaced, so a scrub
that reaches some of those and not others leaves the real identifier published
in the ones it missed AND makes the fixture disagree with itself. Both halves
were true of this repository: the scrub covered the ATA and NVMe fields and
missed the four SCSI/VPD copies, the NQN and the hex ``wwid``, which no text
search for the serial can find.

Enumerating the locations in prose is what failed, so this asserts the property
instead: every copy of a drive's serial in a fixture must agree with the one
the tool reports for that drive. A recapture that misses a location fails here
and is told which one, and a location added to the reader is covered the moment
a fixture carries it.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from lsdsk.adapters.hw.decode.ata_identify import decode_identify, decode_vpd_ata_information
from lsdsk.adapters.hw.decode.nvme import decode_identify_controller

if TYPE_CHECKING:
    from collections.abc import Mapping

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# The fixtures carry this many serial copies between them. A check that decodes
# none of them passes on anything, so the floor is asserted rather than assumed.
_LOCATIONS_THE_FIXTURES_CARRY = 100

# A serial no drive here carries, for the control to plant.
_ANOTHER_SERIAL = b"S0METHINGELSE00     "

_INQUIRY_SERIAL_START = 36
_INQUIRY_SERIAL_END = 56
_NQN_START = 768
_NQN_END = 1024
# SAT-4: a T10 vendor-id designator for an ATA device is "ATA     " (8 bytes),
# the 40-byte IDENTIFY model, then the 20-byte IDENTIFY serial. Any other shape
# has no fixed serial offset, so it is left alone rather than guessed at.
_SAT_VENDOR = b"ATA     "
_SAT_DESIGNATOR_LENGTH = 68
_SAT_SERIAL_START = 48


def _text(raw: bytes) -> str:
    """The printable reading of a raw field, as a SCSI ASCII field is space padded."""
    return "".join(chr(byte) if 32 <= byte < 127 else " " for byte in raw).strip()


def _is_sat_designator(data: bytes) -> bool:
    """Whether this T10 designator has the SAT layout whose serial offset is fixed."""
    return len(data) == _SAT_DESIGNATOR_LENGTH and data.startswith(_SAT_VENDOR)


def _page_83_serials(blob: bytes) -> dict[str, str]:
    """Every ASCII designator in VPD page 0x83, keyed by its offset."""
    found: dict[str, str] = {}
    offset = 4
    while offset + 4 <= len(blob):
        code_set, designator_type, length = blob[offset] & 0xF, blob[offset + 1] & 0xF, blob[offset + 3]
        data = blob[offset + 4 : offset + 4 + length]
        if code_set == 2 and designator_type == 0:
            found[f"vpd_pg83[{offset}] vendor-specific"] = _text(data)
        elif code_set == 2 and designator_type == 1 and _is_sat_designator(data):
            found[f"vpd_pg83[{offset}] t10-vendor-id"] = _text(data[_SAT_SERIAL_START:])
        offset += 4 + length
    return found


def _mapping(value: object) -> dict[str, object]:
    """The mapping a JSON value holds, or an empty one, so every lookup stays typed."""
    if not isinstance(value, dict):
        return {}
    items = cast("dict[object, object]", value).items()
    return {str(key): item for key, item in items}


def _device(capture: Mapping[str, object], section: str, device: str) -> dict[str, object]:
    """One device's record from a top-level section of a capture."""
    return _mapping(_mapping(capture.get(section)).get(device))


def _blob(source: Mapping[str, object], key: str) -> bytes | None:
    value = source.get(key)
    return base64.b64decode(value) if isinstance(value, str) else None


def _serial_locations(capture: Mapping[str, object], device: str) -> dict[str, str]:
    """Every serial this capture holds for one device, keyed by where it lives."""
    record = _device(capture, "block", device)
    vpd = _mapping(record.get("vpd"))
    ata = _device(capture, "ata", device)
    nvme = _device(capture, "nvme", device)
    found: dict[str, str] = {}

    if (identify := _blob(ata, "identify")) is not None:
        found["ata/identify"] = decode_identify(identify).serial
    if (page_89 := _blob(vpd, "vpd_pg89")) is not None:
        found["vpd_pg89"] = decode_vpd_ata_information(page_89).serial
    if (page_80 := _blob(vpd, "vpd_pg80")) is not None:
        found["vpd_pg80"] = _text(page_80[4 : 4 + page_80[3]])
    if (page_83 := _blob(vpd, "vpd_pg83")) is not None:
        found.update(_page_83_serials(page_83))
    if (inquiry := _blob(vpd, "inquiry")) is not None and len(inquiry) >= _INQUIRY_SERIAL_END:
        found["inquiry"] = _text(inquiry[_INQUIRY_SERIAL_START:_INQUIRY_SERIAL_END])
    if (controller := _blob(nvme, "identify_controller")) is not None:
        found["nvme/sn"] = decode_identify_controller(controller).serial
        nqn = _text(controller[_NQN_START:_NQN_END])
        if ":" in nqn:
            found["nvme/nqn"] = nqn.rsplit(":", 1)[-1].strip()
    wwid = record.get("wwid")
    if isinstance(wwid, str) and wwid.startswith("nvme."):
        parts = wwid.split("-")
        if len(parts) >= 2:
            found["wwid"] = _text(bytes.fromhex(parts[1]))
    return {where: serial for where, serial in found.items() if serial}


def _disagreements(capture: Mapping[str, object]) -> list[str]:
    """Locations whose serial differs from the one the tool reports for that drive."""
    devices = sorted(set(_mapping(capture.get("block"))) | set(_mapping(capture.get("nvme"))))
    problems: list[str] = []
    for device in devices:
        locations = _serial_locations(capture, device)
        reported = locations.get("ata/identify") or locations.get("nvme/sn")
        if not reported:
            continue
        problems += [
            f"{device} {where}={serial!r} but the drive reports {reported!r}"
            for where, serial in sorted(locations.items())
            if serial != reported
        ]
    return problems


def _fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _load(path: Path) -> dict[str, object]:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    return _mapping(parsed)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda path: path.stem)
def test_every_copy_of_a_serial_in_a_fixture_agrees_with_the_drive(fixture: Path) -> None:
    """No committed capture may hold two different serials for one drive."""
    problems = _disagreements(_load(fixture))
    assert not problems, f"{fixture.name} disagrees with itself:\n" + "\n".join(problems)


@pytest.mark.os_agnostic
def test_a_fixture_carries_enough_locations_for_the_check_to_mean_something() -> None:
    """A check that finds no locations passes on anything, so require the copies."""
    counts: dict[str, int] = {}
    for path in _fixtures():
        capture = _load(path)
        devices = sorted(set(_mapping(capture.get("block"))) | set(_mapping(capture.get("nvme"))))
        counts[path.stem] = sum(len(_serial_locations(capture, device)) for device in devices)
    assert sum(counts.values()) >= _LOCATIONS_THE_FIXTURES_CARRY, counts


@pytest.mark.os_agnostic
def test_the_check_names_a_location_planted_with_another_serial() -> None:
    """The control: a passing run above must mean the check could have failed."""
    capture = _load(FIXTURES / "linux-sas-hba.json")
    block = _mapping(capture.get("block"))
    record = _mapping(block.get("sda"))
    vpd = _mapping(record.get("vpd"))
    page_80 = _blob(vpd, "vpd_pg80")
    assert page_80 is not None, "the control needs a fixture that carries page 0x80"

    planted = page_80[:4] + _ANOTHER_SERIAL[: len(page_80) - 4]
    vpd["vpd_pg80"] = base64.b64encode(planted).decode("ascii")
    record["vpd"] = vpd
    block["sda"] = record
    capture["block"] = block

    problems = _disagreements(capture)

    assert any("sda vpd_pg80=" in problem for problem in problems), problems
