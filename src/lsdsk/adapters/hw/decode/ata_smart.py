"""Decode the 512-byte ATA SMART READ DATA and THRESHOLDS structures.

Both platforms obtain these through an ATA passthrough command, so one decoder
serves Linux SG_IO and Windows ``IOCTL_ATA_PASS_THROUGH_DIRECT`` alike.

The attribute table is only half-standardised: identifiers 1 to 254 have
conventional meanings but vendors disagree on the raw encoding, so this module
reads the normalised value where vendors agree on it and the raw value only for
attributes whose encoding is settled.

System Role:
    Pure adapter-layer decoding.  Takes bytes, returns domain values.
"""

from __future__ import annotations

from ....domain.models import Health, SmartAttribute

SMART_DATA_LENGTH = 512

# The attribute table starts after the two-byte structure revision and holds 30
# entries of 12 bytes each.
_TABLE_OFFSET = 2
_ENTRY_SIZE = 12
_ENTRY_COUNT = 30

# Conventional attribute names, limited to the ones whose meaning is agreed
# across vendors. Anything absent here is still shown, just without a name.
ATTRIBUTE_NAMES: dict[int, str] = {
    1: "Raw_Read_Error_Rate",
    3: "Spin_Up_Time",
    4: "Start_Stop_Count",
    5: "Reallocated_Sector_Ct",
    7: "Seek_Error_Rate",
    9: "Power_On_Hours",
    10: "Spin_Retry_Count",
    12: "Power_Cycle_Count",
    173: "Wear_Leveling_Count",
    177: "Wear_Leveling_Count",
    179: "Used_Rsvd_Blk_Cnt_Tot",
    181: "Program_Fail_Cnt_Total",
    182: "Erase_Fail_Count_Total",
    183: "Runtime_Bad_Block",
    184: "End-to-End_Error",
    187: "Reported_Uncorrect",
    188: "Command_Timeout",
    190: "Airflow_Temperature_Cel",
    194: "Temperature_Celsius",
    195: "Hardware_ECC_Recovered",
    196: "Reallocated_Event_Count",
    197: "Current_Pending_Sector",
    198: "Offline_Uncorrectable",
    199: "UDMA_CRC_Error_Count",
    202: "Percent_Lifetime_Remain",
    231: "SSD_Life_Left",
    233: "Media_Wearout_Indicator",
    241: "Total_LBAs_Written",
    242: "Total_LBAs_Read",
    246: "Total_Host_Sector_Write",
}

# Attributes whose normalised value counts down from 100 as the drive wears out,
# so percent used is 100 minus that value. Ordered by how much vendors agree.
_WEAR_ATTRIBUTES = (177, 233, 202, 231, 173)

# Attributes carrying the current temperature in the low byte of the raw value.
_TEMPERATURE_ATTRIBUTES = (194, 190)

# Host read and write totals are counted in 512-byte logical sectors.
_LBA_BYTES = 512

# A drive reporting outside this range is reporting a raw field that is not a
# temperature at all: some vendors pack a minimum and a maximum into the same
# raw value. A policy choice, not a figure the ATA specification fixes.
_PLAUSIBLE_TEMPERATURE_C = range(1, 150)


def decode_attributes(data: bytes, thresholds: bytes | None = None) -> tuple[SmartAttribute, ...]:
    """Decode the SMART attribute table.

    Args:
        data: The SMART READ DATA response, at least 512 bytes.
        thresholds: The SMART READ THRESHOLDS response, when it was read.

    Returns:
        Attributes in table order, skipping empty slots.

    Raises:
        ValueError: If the data buffer is too short.

    Example:
        >>> decode_attributes(bytes(512))
        ()
    """
    if len(data) < SMART_DATA_LENGTH:
        message = f"SMART data is {len(data)} bytes, expected at least {SMART_DATA_LENGTH}"
        raise ValueError(message)

    limits = _decode_thresholds(thresholds)
    attributes: list[SmartAttribute] = []
    for index in range(_ENTRY_COUNT):
        offset = _TABLE_OFFSET + index * _ENTRY_SIZE
        identifier = data[offset]
        if identifier == 0:
            continue
        attributes.append(
            SmartAttribute(
                id=identifier,
                name=ATTRIBUTE_NAMES.get(identifier, ""),
                value=data[offset + 3],
                worst=data[offset + 4],
                threshold=limits.get(identifier),
                raw=int.from_bytes(data[offset + 5 : offset + 11], "little"),
            )
        )
    return tuple(attributes)


def _decode_thresholds(blob: bytes | None) -> dict[int, int]:
    """Map attribute identifiers to their failure thresholds."""
    if blob is None or len(blob) < SMART_DATA_LENGTH:
        return {}
    limits: dict[int, int] = {}
    for index in range(_ENTRY_COUNT):
        offset = _TABLE_OFFSET + index * _ENTRY_SIZE
        identifier = blob[offset]
        if identifier != 0:
            limits[identifier] = blob[offset + 1]
    return limits


def _find(attributes: tuple[SmartAttribute, ...], identifier: int) -> SmartAttribute | None:
    """Return one attribute by identifier, or ``None`` when absent."""
    return next((attribute for attribute in attributes if attribute.id == identifier), None)


def _raw_of(attributes: tuple[SmartAttribute, ...], identifier: int) -> int | None:
    """Return one attribute's raw value, or ``None`` when absent."""
    attribute = _find(attributes, identifier)
    return None if attribute is None else attribute.raw


def _temperature(attributes: tuple[SmartAttribute, ...]) -> int | None:
    """Return the current temperature in Celsius.

    Temperature attributes pack the current reading into the low byte and often
    the lifetime minimum and maximum into higher bytes, so only the low byte is
    trustworthy across vendors.
    """
    for identifier in _TEMPERATURE_ATTRIBUTES:
        attribute = _find(attributes, identifier)
        if attribute is not None:
            celsius = attribute.raw & 0xFF
            if celsius in _PLAUSIBLE_TEMPERATURE_C:
                return celsius
    return None


def _percent_used(attributes: tuple[SmartAttribute, ...]) -> int | None:
    """Return wear as a percentage of rated endurance consumed.

    The wear attributes report life *remaining* as a normalised value counting
    down from 100, so the percentage used is its complement.
    """
    for identifier in _WEAR_ATTRIBUTES:
        attribute = _find(attributes, identifier)
        if attribute is not None and 0 <= attribute.value <= 100:  # noqa: PLR2004 - the normalised range itself
            return 100 - attribute.value
    return None


def overall_health(attributes: tuple[SmartAttribute, ...]) -> bool | None:
    """Compute the verdict a drive would give for its own health.

    This is the headline every SMART tool prints, and it is not a separate
    reading: a drive is failing exactly when a pre-fail attribute has fallen to
    or below the threshold its maker set. Attributes with a threshold of zero are
    advisory and never fail, however alarming their raw value looks.

    Args:
        attributes: The decoded attribute table, with thresholds merged in.

    Returns:
        ``True`` when every attribute is above its threshold, ``False`` when one
        is not, and ``None`` when no thresholds were read and so no verdict is
        possible.

    Example:
        >>> healthy = (SmartAttribute(5, "Reallocated_Sector_Ct", 100, 100, 10, 0),)
        >>> overall_health(healthy)
        True
        >>> failing = (SmartAttribute(5, "Reallocated_Sector_Ct", 8, 8, 10, 4096),)
        >>> overall_health(failing)
        False
        >>> overall_health(()) is None
        True
    """
    graded = [attribute for attribute in attributes if attribute.threshold is not None]
    if not graded:
        return None
    return not any(attribute.is_failing for attribute in graded)


def build_health(
    attributes: tuple[SmartAttribute, ...],
    *,
    self_assessment_ok: bool | None = None,
) -> Health:
    """Fold an ATA attribute table into the cross-platform health model.

    Args:
        attributes: The decoded attribute table.
        self_assessment_ok: An overall verdict read from the drive directly. When
            absent it is computed from the attributes, which is the same rule the
            drive applies.

    Returns:
        Health with whatever the table could supply; unread fields stay ``None``.

    Example:
        >>> build_health(()).temperature_c is None
        True
    """
    written = _raw_of(attributes, 241)
    read = _raw_of(attributes, 242)
    uncorrectable = _raw_of(attributes, 198)
    reported = _raw_of(attributes, 187)
    return Health(
        ok=self_assessment_ok if self_assessment_ok is not None else overall_health(attributes),
        temperature_c=_temperature(attributes),
        power_on_hours=_raw_of(attributes, 9),
        percent_used=_percent_used(attributes),
        reallocated_sectors=_raw_of(attributes, 5),
        pending_sectors=_raw_of(attributes, 197),
        # Two attributes count unrecoverable errors and drives populate one or
        # the other, so the larger of the two is the honest figure.
        uncorrectable_sectors=max((value for value in (uncorrectable, reported) if value is not None), default=None),
        crc_errors=_raw_of(attributes, 199),
        bytes_read=None if read is None else read * _LBA_BYTES,
        bytes_written=None if written is None else written * _LBA_BYTES,
        attributes=attributes,
    )


def decode_health(
    data: bytes,
    thresholds: bytes | None = None,
    *,
    self_assessment_ok: bool | None = None,
) -> Health:
    """Decode SMART data straight into the health model.

    Args:
        data: The SMART READ DATA response.
        thresholds: The SMART READ THRESHOLDS response, when it was read.
        self_assessment_ok: The drive's overall SMART verdict, when it was read.

    Returns:
        The disk's health.

    Example:
        >>> decode_health(bytes(512)).attributes
        ()
    """
    attributes = decode_attributes(data, thresholds)
    return build_health(attributes, self_assessment_ok=self_assessment_ok)


__all__ = [
    "ATTRIBUTE_NAMES",
    "SMART_DATA_LENGTH",
    "build_health",
    "decode_attributes",
    "decode_health",
    "overall_health",
]
