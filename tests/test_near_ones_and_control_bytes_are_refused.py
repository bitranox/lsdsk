"""Pages that are almost, but not quite, a real answer are refused rather than decoded.

An IDENTIFY text field holding a control byte, an NVMe Identify Controller text
field holding a byte outside ASCII, and an NVMe SMART log whose spare is above
100 percent or whose reserved warning bits are set are each a bus answering
with a few bytes changed. The all-zeros and all-ones checks cannot see them, and
decoding them printed U+FFFD runs, 255 percent spare and a critical warning no
drive publishes.

The last tests hold the fallback leg of the NVMe temperature: a health log whose
composite is unreadable still gets the hardware monitor's reading.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.decode.ata_identify import decode_identify
from lsdsk.adapters.hw.decode.nvme import (
    IDENTIFY_CONTROLLER_LENGTH,
    SMART_LOG_LENGTH,
    decode_identify_controller,
    decode_smart_log,
)
from lsdsk.adapters.hw.snapshot import build_from

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CONTROL_BYTES = [*range(0x01, 0x20), 0x7F]
FIRST_MODEL_WORD = 27


def _identify_with_model_byte(byte: int) -> bytes:
    """An IDENTIFY page whose first model word is ``byte`` followed by 'X'."""
    words = [0] * 256
    words[FIRST_MODEL_WORD] = (byte << 8) | ord("X")
    words[60] = 0x0100
    return struct.pack("<256H", *words)


@pytest.mark.parametrize("byte", CONTROL_BYTES)
def test_an_identify_text_word_holding_a_control_byte_is_refused(byte: int) -> None:
    with pytest.raises(ValueError, match="not ASCII"):
        decode_identify(_identify_with_model_byte(byte))


def test_an_identify_text_word_holding_the_highest_printable_byte_is_kept() -> None:
    """Liveness: the edge of the printable range still decodes."""
    assert decode_identify(_identify_with_model_byte(0x7E)).model.startswith("~")


def test_an_identify_text_word_holding_the_lowest_printable_byte_is_kept() -> None:
    assert decode_identify(_identify_with_model_byte(0x20)).model == "X"


def _identify_controller(**fields: bytes) -> bytes:
    page = bytearray(IDENTIFY_CONTROLLER_LENGTH)
    page[4:24] = b"SERIAL0".ljust(20)
    page[24:64] = b"ACME NVMe 2TB".ljust(40)
    page[64:72] = b"FW1".ljust(8)
    offsets = {"serial": 4, "model": 24, "firmware": 64}
    for name, value in fields.items():
        page[offsets[name] : offsets[name] + len(value)] = value
    return bytes(page)


def test_a_clean_identify_controller_page_decodes() -> None:
    """Liveness: the hand-built page the refusals are planted in is a good one."""
    assert decode_identify_controller(_identify_controller()).model == "ACME NVMe 2TB"


@pytest.mark.parametrize("field", ["serial", "model", "firmware"])
@pytest.mark.parametrize("byte", [0x01, 0x1F, 0x7F, 0x80, 0xFE])
def test_an_identify_controller_text_field_with_a_non_printable_byte_is_refused(field: str, byte: int) -> None:
    with pytest.raises(ValueError, match="not ASCII"):
        decode_identify_controller(_identify_controller(**{field: bytes([byte])}))


def test_an_identify_controller_text_field_padded_with_nul_is_kept() -> None:
    page = _identify_controller(model=b"ACME".ljust(40, b"\x00"))
    assert decode_identify_controller(page).model == "ACME"


def _smart_log(*, spare: int = 100, warning: int = 0) -> bytes:
    page = bytearray(SMART_LOG_LENGTH)
    page[0] = warning
    page[1:3] = (273 + 38).to_bytes(2, "little")
    page[3] = spare
    page[4] = 10
    return bytes(page)


def test_a_plain_nvme_smart_log_decodes() -> None:
    """Liveness: the hand-built log the refusals are planted in is a good one."""
    health = decode_smart_log(_smart_log())
    assert health.available_spare == 100
    assert health.ok is True


@pytest.mark.parametrize("spare", [101, 200, 255])
def test_an_nvme_available_spare_above_one_hundred_percent_is_refused(spare: int) -> None:
    with pytest.raises(ValueError, match="does not allow"):
        decode_smart_log(_smart_log(spare=spare))


@pytest.mark.parametrize("warning", [0x40, 0x80, 0xC0, 0xFF])
def test_an_nvme_critical_warning_with_a_reserved_bit_set_is_refused(warning: int) -> None:
    with pytest.raises(ValueError, match="does not allow"):
        decode_smart_log(_smart_log(warning=warning))


@pytest.mark.parametrize("warning", [0x01, 0x20, 0x3F])
def test_an_nvme_critical_warning_with_only_defined_bits_is_kept(warning: int) -> None:
    assert decode_smart_log(_smart_log(warning=warning)).critical_warning == warning


def _nvme_board() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((FIXTURES / "linux-nvme-board.json").read_text(encoding="utf-8")))


def _nvme0_temperature_after(plant: Any) -> tuple[int | None, int | None]:
    reading = _nvme_board()
    plant(reading)
    (disk,) = [disk for disk in build_from(reading).disks if disk.node == "nvme0n1"]
    assert disk.health is not None
    return disk.health.temperature_c, disk.health.power_on_hours


def test_an_unreadable_composite_falls_back_to_the_hardware_monitor() -> None:
    """The log is kept for everything else; only its missing temperature is filled in."""

    def plant(reading: dict[str, Any]) -> None:
        entry = reading["nvme"]["nvme0n1"]
        page = bytearray(base64.b64decode(entry["smart_log"]))
        page[1:3] = bytes(2)  # composite temperature: not reported
        entry["smart_log"] = base64.b64encode(bytes(page)).decode("ascii")
        reading["classes"]["hwmon"]["hwmon3"]["temp1_input"] = "47000"

    temperature, power_on_hours = _nvme0_temperature_after(plant)

    assert temperature == 47
    assert power_on_hours is not None


def test_a_readable_composite_wins_over_the_hardware_monitor() -> None:
    """Liveness: the monitor is only the fallback, so the same harness sees the log's figure."""

    def plant(reading: dict[str, Any]) -> None:
        page = bytearray(base64.b64decode(reading["nvme"]["nvme0n1"]["smart_log"]))
        page[1:3] = (273 + 33).to_bytes(2, "little")
        reading["nvme"]["nvme0n1"]["smart_log"] = base64.b64encode(bytes(page)).decode("ascii")
        reading["classes"]["hwmon"]["hwmon3"]["temp1_input"] = "47000"

    assert _nvme0_temperature_after(plant)[0] == 33
