"""Each field of an IDENTIFY page and of the NVMe structures is read from its own place, at its own width.

The committed captures carry real drives whose model and serial are shorter than
their fields and whose geometry is the common case, so a decoder that dropped the
last character of a full-width string, or read a sector size when the geometry
word said it should not, still agreed with them. These pages are built by hand,
one property each, and the length, offset and mask legs are the ones pinned.
"""

from __future__ import annotations

import struct

import pytest

from lsdsk.adapters.hw.decode.ata_identify import (
    IDENTIFY_LENGTH,
    VPD_ATA_INFORMATION_IDENTIFY_OFFSET,
    decode_identify,
    decode_vpd_ata_information,
)
from lsdsk.adapters.hw.decode.nvme import (
    IDENTIFY_CONTROLLER_LENGTH,
    SMART_LOG_LENGTH,
    NvmeIdentity,
    decode_identify_controller,
    decode_smart_log,
)
from lsdsk.domain.enums import DiskKind

KELVIN = 273


def _text_words(text: str, length: int) -> list[int]:
    """An ATA string field: first character in the high byte of each word, padded with spaces."""
    padded = text.ljust(length * 2).encode("ascii")
    return [(padded[index] << 8) | padded[index + 1] for index in range(0, length * 2, 2)]


def _page(**fields: int | str) -> bytes:
    """An IDENTIFY page with a model, serial, firmware and LBA28 count, then the overrides.

    Keys are ``wN`` for a word or ``model`` / ``serial`` / ``firmware`` for a field.
    """
    words = [0] * 256
    words[27:47] = _text_words(str(fields.pop("model", "MODEL")), 20)
    words[10:20] = _text_words(str(fields.pop("serial", "SERIAL")), 10)
    words[23:27] = _text_words(str(fields.pop("firmware", "FW")), 4)
    words[60], words[61] = 0x0100, 0
    for name, value in fields.items():
        words[int(name.removeprefix("w"))] = int(value)
    return struct.pack("<256H", *words)


def _bare(**fields: int | str) -> bytes:
    """An IDENTIFY page naming nothing, then the overrides."""
    words = [0] * 256
    for name, value in fields.items():
        words[int(name.removeprefix("w"))] = int(value)
    return struct.pack("<256H", *words)


def test_full_width_text_fields_lose_no_character() -> None:
    model, serial, firmware = "M" * 39 + "Z", "S" * 19 + "Y", "F" * 7 + "X"

    identity = decode_identify(_page(model=model, serial=serial, firmware=firmware))

    assert (identity.model, identity.serial, identity.firmware) == (model, serial, firmware)


def test_each_text_field_is_read_from_its_own_words() -> None:
    identity = decode_identify(_page(model="the model", serial="the serial", firmware="rev"))

    assert (identity.model, identity.serial, identity.firmware) == ("the model", "the serial", "rev")


def test_the_28_bit_count_is_two_words_low_first() -> None:
    assert decode_identify(_page(w60=0x2222, w61=0x0011)).sectors == 0x0011_2222


def test_the_48_bit_count_is_three_words_low_first_and_wins_over_the_28_bit_one() -> None:
    identity = decode_identify(_page(w60=5, w100=0x1111, w101=0x2222, w102=0x3333))

    assert identity.sectors == 0x3333_2222_1111


def test_a_page_with_no_count_at_all_has_none() -> None:
    assert decode_identify(_page(w60=0)).sectors is None


@pytest.mark.parametrize(
    ("geometry", "expected_size"),
    [
        (0x4000 | 0x1000, 4096),  # valid, and says the sector is larger than 512 bytes
        (0x4000, 512),  # valid, and says nothing about a larger sector
        (0x1000, 512),  # bit 14 clear: the word is not valid
        (0xC000 | 0x1000, 512),  # bit 15 set: the word is not valid
    ],
)
def test_the_geometry_word_is_believed_only_when_it_is_valid_and_says_larger(geometry: int, expected_size: int) -> None:
    identity = decode_identify(_page(w106=geometry, w117=2048, w118=0))

    assert identity.sector_size == expected_size
    assert identity.sectors == 0x0100


@pytest.mark.parametrize(("words", "size"), [(257, 514), (2048, 4096), (1024, 2048)])
def test_a_logical_sector_word_count_inside_the_real_range_sets_the_size(words: int, size: int) -> None:
    identity = decode_identify(_page(w106=0x5000, w117=words & 0xFFFF, w118=words >> 16))

    assert identity.sector_size == size


@pytest.mark.parametrize("words", [0, 256, 2049, 0x1_0000, 0x1_0400, 0xFFFF_FFFF])
def test_a_logical_sector_word_count_outside_it_withholds_the_capacity(words: int) -> None:
    identity = decode_identify(_page(w106=0x5000, w117=words & 0xFFFF, w118=words >> 16))

    assert identity.sectors is None
    assert identity.size_bytes is None
    assert identity.sector_size == 512


@pytest.mark.parametrize(
    ("rate", "kind", "rpm"),
    [
        (1, DiskKind.SSD, None),
        (0, DiskKind.UNKNOWN, None),
        (2, DiskKind.UNKNOWN, None),
        (0x0400, DiskKind.UNKNOWN, None),
        (0x0401, DiskKind.HDD, 0x0401),
        (7200, DiskKind.HDD, 7200),
        (20_000, DiskKind.HDD, 20_000),
        (20_001, DiskKind.UNKNOWN, None),
        (0xFFFF, DiskKind.UNKNOWN, None),
    ],
)
def test_the_nominal_rotation_rate_decides_the_media_kind(rate: int, kind: DiskKind, rpm: int | None) -> None:
    identity = decode_identify(_page(w217=rate))

    assert (identity.kind, identity.rotation_rpm) == (kind, rpm)


@pytest.mark.parametrize(
    ("capability", "maximum"),
    [
        (0x0002, 1.5),
        (0x0004, 3.0),
        (0x0008, 6.0),
        (0x000E, 6.0),
        (0x000C, 6.0),
        (0x0006, 3.0),
        (0x0001, None),
        (0x0010, None),
        (0x0000, None),
        (0xFFFF, None),
    ],
)
def test_the_supported_rates_bitmap_gives_the_highest_generation(capability: int, maximum: float | None) -> None:
    assert decode_identify(_page(w76=capability)).max_gbps == maximum


@pytest.mark.parametrize(
    ("current", "negotiated"),
    [
        (1 << 1, 1.5),
        (2 << 1, 3.0),
        (3 << 1, 6.0),
        (0, None),
        (4 << 1, None),
        (0x8000 | (3 << 1), 6.0),
        (0x0001, None),
        (5 << 1, None),
        (7 << 1, None),
        ((3 << 1) | (1 << 4), 6.0),
    ],
)
def test_the_negotiated_rate_is_the_three_bits_above_bit_zero(current: int, negotiated: float | None) -> None:
    assert decode_identify(_page(w77=current)).negotiated_gbps == negotiated


@pytest.mark.parametrize(
    ("word82", "supported"), [(0x0001, True), (0x0003, True), (0x0002, False), (0x0000, False), (0xFFFF, False)]
)
def test_smart_support_is_bit_zero_of_word_82_unless_the_word_is_unimplemented(word82: int, supported: bool) -> None:
    assert decode_identify(_page(w82=word82)).smart_supported is supported


@pytest.mark.parametrize(
    "page",
    [
        _bare(w27=0x4142),
        _bare(w10=0x4142),
        _bare(w23=0x4142),
        _bare(w60=1),
        _bare(w61=1),
        _bare(w100=1),
        _bare(w101=1),
        _bare(w102=1),
    ],
    ids=["model", "serial", "firmware", "lba28-low", "lba28-high", "lba48-0", "lba48-1", "lba48-2"],
)
def test_any_one_piece_of_identity_is_enough(page: bytes) -> None:
    assert decode_identify(page) is not None


def test_a_page_naming_nothing_and_counting_nothing_is_refused_even_with_other_words_set() -> None:
    with pytest.raises(ValueError, match="no identity"):
        decode_identify(_bare(w76=0x0008, w82=0x0001, w217=7200))


def test_the_identify_page_is_read_from_its_first_512_bytes_only() -> None:
    identity = decode_identify(_page(model="head") + b"\xff" * 64)

    assert identity.model == "head"


def test_a_page_one_byte_short_is_refused_and_exactly_512_is_read() -> None:
    page = _page()
    assert len(page) == IDENTIFY_LENGTH
    decode_identify(page)
    with pytest.raises(ValueError, match="511 bytes"):
        decode_identify(page[:-1])


def test_the_vpd_wrapper_reads_the_identify_payload_after_its_60_byte_header() -> None:
    header = b"\xaa" * VPD_ATA_INFORMATION_IDENTIFY_OFFSET

    assert decode_vpd_ata_information(header + _page(model="inside")).model == "inside"


def test_a_vpd_page_one_byte_short_of_header_plus_payload_is_refused() -> None:
    page = bytes(VPD_ATA_INFORMATION_IDENTIFY_OFFSET) + _page()

    decode_vpd_ata_information(page)
    with pytest.raises(ValueError, match="571 bytes"):
        decode_vpd_ata_information(page[:-1])


def _controller(**spans: bytes) -> bytes:
    page = bytearray(IDENTIFY_CONTROLLER_LENGTH)
    page[4:24] = b"S" * 19 + b"Y"
    page[24:64] = b"M" * 39 + b"Z"
    page[64:72] = b"F" * 7 + b"X"
    for name, value in spans.items():
        start = int(name.removeprefix("at"))
        page[start : start + len(value)] = value
    return bytes(page)


def test_nvme_text_fields_are_full_width_and_each_in_its_own_place() -> None:
    identity = decode_identify_controller(_controller())

    assert identity == NvmeIdentity("M" * 39 + "Z", "S" * 19 + "Y", "F" * 7 + "X")


def test_nvme_thresholds_are_two_kelvin_words_after_byte_266() -> None:
    identity = decode_identify_controller(_controller(at266=struct.pack("<HH", 70 + KELVIN, 80 + KELVIN)))

    assert (identity.warning_temperature_c, identity.critical_temperature_c) == (70, 80)


@pytest.mark.parametrize("kelvin", [0, 10, 2000, 0xFFFF])
def test_an_unset_or_implausible_nvme_threshold_is_not_reported(kelvin: int) -> None:
    identity = decode_identify_controller(_controller(at266=struct.pack("<HH", kelvin, kelvin)))

    assert (identity.warning_temperature_c, identity.critical_temperature_c) == (None, None)


def test_an_identify_controller_page_one_byte_short_is_refused() -> None:
    decode_identify_controller(_controller())
    with pytest.raises(ValueError, match="4095 bytes"):
        decode_identify_controller(_controller()[:-1])


def _log(**spans: bytes) -> bytes:
    page = bytearray(SMART_LOG_LENGTH)
    for name, value in spans.items():
        start = int(name.removeprefix("at"))
        page[start : start + len(value)] = value
    return bytes(page)


def _u128(value: int) -> bytes:
    return value.to_bytes(16, "little")


def test_every_smart_log_counter_is_read_from_its_own_offset_at_full_width() -> None:
    page = _log(
        at0=bytes([0x04]),
        at1=struct.pack("<H", 41 + KELVIN),
        at3=bytes([77, 9, 12]),
        at32=_u128(3 + 2**70),
        at48=_u128(5 + 2**71),
        at112=_u128(7 + 2**72),
        at128=_u128(11),
        at144=_u128(13 + 2**73),
        at160=_u128(17 + 2**74),
        at176=_u128(19 + 2**75),
    )

    health = decode_smart_log(page)

    assert health.critical_warning == 0x04
    assert health.ok is False
    assert health.temperature_c == 41
    assert (health.available_spare, health.available_spare_threshold, health.percent_used) == (77, 9, 12)
    assert health.bytes_read == (3 + 2**70) * 512_000
    assert health.bytes_written == (5 + 2**71) * 512_000
    assert health.power_cycles == 7 + 2**72
    assert health.power_on_hours == 11
    assert health.unsafe_shutdowns == 13 + 2**73
    assert health.media_errors == 17 + 2**74
    assert health.error_log_entries == 19 + 2**75


def test_a_counter_wider_than_64_bits_is_kept() -> None:
    page = _log(at3=bytes([100]), at128=_u128(2**100))

    assert decode_smart_log(page).power_on_hours == 2**100


def test_a_clear_critical_warning_is_ok_and_the_vendor_thresholds_come_from_the_identity() -> None:
    identity = NvmeIdentity("m", "s", "f", warning_temperature_c=70, critical_temperature_c=80)
    page = _log(at3=bytes([100]))

    with_identity = decode_smart_log(page, identity)
    without = decode_smart_log(page)

    assert with_identity.ok is True
    assert (with_identity.temperature_warning_c, with_identity.temperature_critical_c) == (70, 80)
    assert (without.temperature_warning_c, without.temperature_critical_c) == (None, None)


def test_a_smart_log_one_byte_short_is_refused_and_a_full_page_with_only_the_last_byte_set_is_a_reading() -> None:
    with pytest.raises(ValueError, match="511 bytes"):
        decode_smart_log(bytes(SMART_LOG_LENGTH - 1))

    page = bytearray(SMART_LOG_LENGTH)
    page[SMART_LOG_LENGTH - 1] = 1
    assert decode_smart_log(bytes(page)).ok is True


def test_only_the_first_512_bytes_of_a_longer_log_decide_whether_it_is_empty() -> None:
    assert decode_smart_log(bytes(SMART_LOG_LENGTH) + b"\x01" * 16).ok is None
