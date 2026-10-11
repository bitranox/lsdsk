"""Every value the ATA SMART decoder reads is pinned to the byte it comes from.

A table of thirty 12-byte entries after a two-byte revision: identifier, flags,
normalised value, worst, a 48-bit raw field. The fixtures hold a few real
drives, so a decoder that read the wrong width, the wrong slot or the wrong unit
could still agree with them. These tests build the table by hand, one property
per test, so each leg of the decoder fails on its own.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode.ata_smart import SMART_DATA_LENGTH, decode_attributes, decode_health, overall_health

ENTRY = 12
FIRST = 2


def _page(*entries: tuple[int, int, int, int], at: int = 0) -> bytes:
    """A SMART data page with ``(id, value, worst, raw)`` entries from slot ``at``."""
    page = bytearray(SMART_DATA_LENGTH)
    for index, (identifier, value, worst, raw) in enumerate(entries, start=at):
        offset = FIRST + index * ENTRY
        page[offset] = identifier
        page[offset + 3] = value
        page[offset + 4] = worst
        page[offset + 5 : offset + 11] = raw.to_bytes(6, "little")
    return bytes(page)


def _thresholds(*entries: tuple[int, int]) -> bytes:
    page = bytearray(SMART_DATA_LENGTH)
    for index, (identifier, threshold) in enumerate(entries):
        offset = FIRST + index * ENTRY
        page[offset] = identifier
        page[offset + 1] = threshold
    return bytes(page)


def test_the_first_and_the_thirtieth_slot_are_read_and_a_thirty_first_is_not() -> None:
    page = bytearray(_page((5, 100, 99, 7)))
    page[FIRST + 29 * ENTRY] = 9
    page[FIRST + 30 * ENTRY] = 12  # past the table: the page has room, the standard does not

    ids = [attribute.id for attribute in decode_attributes(bytes(page))]

    assert ids == [5, 9]


def test_an_entry_is_decoded_from_its_own_bytes() -> None:
    (attribute,) = decode_attributes(_page((5, 101, 102, 0x0000_AABB_CCDD_EEFF)))

    assert (attribute.id, attribute.value, attribute.worst) == (5, 101, 102)
    assert attribute.raw == 0x0000_AABB_CCDD_EEFF
    assert attribute.name == "Reallocated_Sector_Ct"


def test_the_raw_field_is_48_bits_wide_and_stops_before_the_next_byte() -> None:
    page = bytearray(_page((5, 100, 100, 2**48 - 1)))
    page[FIRST + 11] = 0xFF  # the entry's last byte is not part of the raw value

    (attribute,) = decode_attributes(bytes(page))

    assert attribute.raw == 2**48 - 1


def test_an_identifier_zero_slot_is_empty_whatever_else_it_holds() -> None:
    page = bytearray(_page((5, 100, 100, 1)))
    page[FIRST + ENTRY + 3] = 77

    assert [attribute.id for attribute in decode_attributes(bytes(page))] == [5]


def test_a_threshold_is_matched_to_its_attribute_by_identifier() -> None:
    attributes = decode_attributes(_page((5, 100, 100, 0), (9, 90, 90, 0)), _thresholds((9, 40), (5, 10)))

    assert {attribute.id: attribute.threshold for attribute in attributes} == {5: 10, 9: 40}


def test_a_threshold_page_that_is_too_short_supplies_none() -> None:
    (attribute,) = decode_attributes(_page((5, 100, 100, 0)), _thresholds((5, 10))[: SMART_DATA_LENGTH - 1])

    assert attribute.threshold is None


def test_a_page_shorter_than_512_bytes_is_refused() -> None:
    with pytest.raises(ValueError, match="511 bytes"):
        decode_attributes(bytes(SMART_DATA_LENGTH - 1))


def test_a_threshold_of_zero_is_advisory_and_never_fails_the_drive() -> None:
    attributes = decode_attributes(_page((1, 0, 0, 0)), _thresholds((1, 0)))

    assert overall_health(attributes) is True


@pytest.mark.parametrize(("value", "failing"), [(11, False), (10, True), (9, True)])
def test_an_attribute_fails_exactly_when_its_value_falls_to_its_threshold(value: int, failing: bool) -> None:
    attributes = decode_attributes(_page((5, value, value, 0)), _thresholds((5, 10)))

    assert overall_health(attributes) is not failing


def test_one_failing_attribute_among_healthy_ones_fails_the_drive() -> None:
    attributes = decode_attributes(
        _page((5, 100, 100, 0), (9, 5, 5, 0), (12, 100, 100, 0)), _thresholds((5, 10), (9, 10), (12, 10))
    )

    assert overall_health(attributes) is False


def test_a_page_with_no_thresholds_gives_no_verdict() -> None:
    assert decode_health(_page((5, 100, 100, 0))).ok is None


def test_a_verdict_read_from_the_drive_wins_over_the_one_computed() -> None:
    page, limits = _page((5, 1, 1, 0)), _thresholds((5, 10))

    assert decode_health(page, limits).ok is False
    assert decode_health(page, limits, self_assessment_ok=True).ok is True
    assert decode_health(_page((5, 100, 100, 0)), _thresholds((5, 10)), self_assessment_ok=False).ok is False


def test_host_totals_are_counted_in_512_byte_sectors() -> None:
    health = decode_health(_page((241, 100, 100, 1000), (242, 100, 100, 3)))

    assert health.bytes_written == 1000 * 512
    assert health.bytes_read == 3 * 512


def test_each_counter_comes_from_its_own_attribute() -> None:
    health = decode_health(
        _page((9, 100, 100, 1111), (5, 100, 100, 2), (197, 100, 100, 3), (199, 100, 100, 4)),
    )

    assert (health.power_on_hours, health.reallocated_sectors, health.pending_sectors, health.crc_errors) == (
        1111,
        2,
        3,
        4,
    )


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([(198, 100, 100, 5), (187, 100, 100, 9)], 9),
        ([(198, 100, 100, 9), (187, 100, 100, 5)], 9),
        ([(198, 100, 100, 6)], 6),
        ([(187, 100, 100, 6)], 6),
        ([], None),
    ],
)
def test_unrecoverable_errors_are_the_larger_of_the_two_counters(
    entries: list[tuple[int, int, int, int]], expected: int | None
) -> None:
    assert decode_health(_page(*entries)).uncorrectable_sectors == expected


@pytest.mark.parametrize(("value", "used"), [(100, 0), (1, 99), (0, 100)])
def test_wear_is_the_complement_of_the_normalised_value_across_its_range(value: int, used: int) -> None:
    assert decode_health(_page((177, value, value, 0))).percent_used == used


def test_a_normalised_value_above_100_is_not_a_wear_figure() -> None:
    assert decode_health(_page((177, 101, 101, 0))).percent_used is None


@pytest.mark.parametrize("pair", [(177, 233), (233, 202), (202, 231), (231, 173)])
def test_the_wear_attributes_are_tried_in_the_order_vendors_agree_on(pair: tuple[int, int]) -> None:
    first, second = pair
    health = decode_health(_page((second, 70, 70, 0), (first, 90, 90, 0)))

    assert health.percent_used == 10, f"{first} must be preferred to {second}"


def test_a_wear_attribute_outside_its_range_falls_through_to_the_next() -> None:
    assert decode_health(_page((177, 200, 200, 0), (233, 80, 80, 0))).percent_used == 20


def test_only_the_low_byte_of_a_temperature_is_the_temperature() -> None:
    assert decode_health(_page((194, 100, 100, 0x0050_0040_012A))).temperature_c == 42


def test_the_temperature_attribute_is_preferred_to_the_airflow_one() -> None:
    assert decode_health(_page((190, 100, 100, 50), (194, 100, 100, 30))).temperature_c == 30
    assert decode_health(_page((190, 100, 100, 50))).temperature_c == 50


def test_an_implausible_temperature_attribute_falls_through_to_the_airflow_one() -> None:
    assert decode_health(_page((194, 100, 100, 0xFF), (190, 100, 100, 35))).temperature_c == 35
