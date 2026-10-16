"""A figure exactly AT a limit lands on the side the limit's own wording says.

Every judgement here compares a reading against a limit, and a mutation of the
comparison (``<`` for ``<=``) moves only the reading that equals the limit,
which no ordinary fixture happens to carry. Each case therefore sits on the
limit and one step either side of it, so both a mutated edge and a limit that
slid by one fail.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.render import detail, theme
from lsdsk.adapters.render.trend import format_rate
from lsdsk.domain.diagnostics import diagnose_health
from lsdsk.domain.enums import BusType, DiskKind, Severity
from lsdsk.domain.history import DiskSeries, Sample
from lsdsk.domain.models import Disk, Health, Inventory
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds


def _disk(health: Health) -> Disk:
    return Disk(node="sda", path="/dev/sda", model="Test Drive", kind=DiskKind.SSD, bus=BusType.NVME, health=health)


def _severities(health: Health, *, title_has: str, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> list[Severity]:
    findings = diagnose_health(_disk(health), thresholds=thresholds)
    return [finding.severity for finding in findings if title_has in finding.title]


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (DEFAULT_THRESHOLDS.crc_errors_significant - 1, Severity.HINT),
        (DEFAULT_THRESHOLDS.crc_errors_significant, Severity.WARNING),
        (DEFAULT_THRESHOLDS.crc_errors_significant + 1, Severity.WARNING),
    ],
)
def test_a_crc_count_at_the_threshold_is_a_fault_not_a_note(count: int, expected: Severity) -> None:
    """The threshold is documented as the count AT OR ABOVE which a count is a fault."""
    assert _severities(Health(crc_errors=count), title_has="CRC") == [expected]


def test_a_configured_crc_threshold_is_met_by_a_count_equal_to_it() -> None:
    assert _severities(Health(crc_errors=7), title_has="CRC", thresholds=Thresholds(crc_errors_significant=7)) == [
        Severity.WARNING
    ]
    assert _severities(Health(crc_errors=6), title_has="CRC", thresholds=Thresholds(crc_errors_significant=7)) == [
        Severity.HINT
    ]


@pytest.mark.parametrize(
    ("reading", "expected"),
    [(84, []), (85, [Severity.CRITICAL]), (86, [Severity.CRITICAL])],
)
def test_a_drive_at_its_own_critical_temperature_is_critical(reading: int, expected: list[Severity]) -> None:
    health = Health(temperature_c=reading, temperature_critical_c=85)

    assert _severities(health, title_has=" C") == expected


@pytest.mark.parametrize(
    ("reading", "expected"),
    [(81, []), (82, [Severity.WARNING]), (83, [Severity.WARNING])],
)
def test_a_drive_at_its_own_warning_temperature_is_warned_about(reading: int, expected: list[Severity]) -> None:
    health = Health(temperature_c=reading, temperature_warning_c=82, temperature_critical_c=90)

    assert _severities(health, title_has=" C") == expected


def _rising_wear(*, before: int, after: int, over_hours: int) -> tuple[Disk, DiskSeries]:
    """A drive that went from `before` to `after` percent used in `over_hours` power-on hours."""
    samples = (
        Sample(power_on_hours=1000, captured_at="a", percent_used=before),
        Sample(power_on_hours=1000 + over_hours, captured_at="b", percent_used=after),
    )
    return _disk(Health(percent_used=after)), DiskSeries(identity="naa.1", model="X", samples=samples)


def _projection(disk: Disk, series: DiskSeries) -> str:
    wear = [finding for finding in diagnose_health(disk, series) if "endurance" in finding.title]
    assert len(wear) == 1, "the drive reported no wear finding, so no projection could be judged"
    return wear[0].detail


def test_a_wear_rise_of_exactly_the_minimum_points_is_projected() -> None:
    """Two points is the shipped floor: the first rise a rate can be taken from."""
    minimum = DEFAULT_THRESHOLDS.wear_projection_min_points
    disk, series = _rising_wear(before=91 - minimum, after=91, over_hours=10 * minimum)

    assert "reaches 100% in about" in _projection(disk, series)


def test_a_wear_rise_one_point_short_of_the_minimum_is_not_projected() -> None:
    minimum = DEFAULT_THRESHOLDS.wear_projection_min_points
    disk, series = _rising_wear(before=91 - minimum + 1, after=91, over_hours=10)

    assert "reaches 100%" not in _projection(disk, series)


def test_a_drive_already_at_100_percent_has_no_date_to_project() -> None:
    disk, series = _rising_wear(before=98, after=100, over_hours=10)

    assert "reaches 100%" not in _projection(disk, series)


def test_a_projection_under_a_year_is_given_in_days() -> None:
    """Three points in 80 hours at 91% used leaves 240 power-on hours."""
    disk, series = _rising_wear(before=88, after=91, over_hours=80)

    assert "reaches 100% in about 10 days of power-on time." in _projection(disk, series)


def test_a_projection_of_exactly_one_year_is_given_in_years() -> None:
    """Two points in 876 hours at 80% used leaves exactly 8760 power-on hours."""
    disk, series = _rising_wear(before=78, after=80, over_hours=876)

    assert "reaches 100% in about 1.0 years of power-on time." in _projection(disk, series)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (1023, "1023B"),
        (1024, "1.0KiB"),
        (10 * 1024**3 - 1, "10GiB"),
        (int(9.94 * 1024**3), "9.9GiB"),
        (10 * 1024**3, "10GiB"),
    ],
)
def test_a_capacity_changes_unit_and_precision_exactly_at_the_step(size: int, expected: str) -> None:
    assert theme.format_size(size) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (999, "999B"),
        (1000, "1.0KB/1000B"),
        (10 * 1000**3 - 1, "10GB/9.3GiB"),
        (9_940_000_000, "9.9GB/9.3GiB"),
        (10 * 1000**3, "10GB/9.3GiB"),
    ],
)
def test_a_capacity_on_both_scales_changes_unit_exactly_at_each_step(size: int, expected: str) -> None:
    assert theme.format_size_both(size) == expected


@pytest.mark.parametrize(
    ("celsius", "style"),
    [
        (81, theme.STYLE_AT_CAPABILITY),
        (82, theme.STYLE_BELOW_CAPABILITY),
        (84, theme.STYLE_BELOW_CAPABILITY),
        (85, theme.STYLE_FAILING),
    ],
)
def test_a_temperature_is_styled_by_the_drives_own_limits_from_the_limit_itself(celsius: int, style: str) -> None:
    assert theme.format_temperature(celsius, warning=82, critical=85) == (f"{celsius}C", style)


@pytest.mark.parametrize(
    ("celsius", "style"),
    [
        (theme.TEMPERATURE_WARM - 1, theme.STYLE_AT_CAPABILITY),
        (theme.TEMPERATURE_WARM, theme.STYLE_BELOW_CAPABILITY),
        (theme.TEMPERATURE_HOT - 1, theme.STYLE_BELOW_CAPABILITY),
        (theme.TEMPERATURE_HOT, theme.STYLE_FAILING),
    ],
)
def test_a_temperature_with_no_declared_limit_is_styled_from_the_generic_bands_themselves(
    celsius: int, style: str
) -> None:
    assert theme.format_temperature(celsius) == (f"{celsius}C", style)


@pytest.mark.parametrize(
    ("used", "style"),
    [
        (DEFAULT_THRESHOLDS.wear_warning_percent - 1, theme.STYLE_AT_CAPABILITY),
        (DEFAULT_THRESHOLDS.wear_warning_percent, theme.STYLE_BELOW_CAPABILITY),
        (DEFAULT_THRESHOLDS.wear_critical_percent - 1, theme.STYLE_BELOW_CAPABILITY),
        (DEFAULT_THRESHOLDS.wear_critical_percent, theme.STYLE_FAILING),
    ],
)
def test_wear_is_styled_from_the_threshold_itself(used: int, style: str) -> None:
    assert theme.format_wear(used, DEFAULT_THRESHOLDS) == (f"{used}%", style)


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(10.0, "10"), (9.96, "10.0"), (0.1, "0.1"), (0.0999, "0.100")],
)
def test_a_rate_drops_a_decimal_from_ten_and_adds_one_below_a_tenth(rate: float, expected: str) -> None:
    assert format_rate(rate) == expected


def _spare_cell(spare: int, threshold: int) -> tuple[str, str]:
    health = Health(available_spare=spare, available_spare_threshold=threshold)
    drive = _disk(health)
    record = detail.disk_detail(drive, Inventory(hostname="h", disks=(drive,)), thresholds=DEFAULT_THRESHOLDS)
    (group,) = [group for group in record.groups if group.label.value == "health"]
    return dict(group.values)["spare"]


def test_spare_capacity_is_failing_only_below_the_drives_own_threshold() -> None:
    """NVMe raises its critical warning when the spare FALLS BELOW the threshold, not on reaching it."""
    assert _spare_cell(10, 10) == ("10% of 10%", theme.STYLE_AT_CAPABILITY)
    assert _spare_cell(9, 10) == ("9% of 10%", theme.STYLE_FAILING)
