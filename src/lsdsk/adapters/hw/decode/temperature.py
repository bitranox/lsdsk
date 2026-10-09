"""What a drive temperature has to look like to be believed.

A monitor file or a log page can carry garbage: a sysfs attribute written by a
broken driver, or an NVMe page a dead bus answered with all ones. Reading it
as a temperature prints ``100000000000000C`` or ``65262C`` beside a healthy
drive, so a figure outside the range any storage device can report is treated
as not measured, exactly like one the drive never published.

System Role:
    Adapter layer, pure decoding; shared by the Linux hwmon reader and the NVMe
    log decoder so both draw the line in one place.
"""

from __future__ import annotations

# Wider than any operating range a drive datasheet lists (industrial parts go to
# -40 C and 85 C; NAND throttles and shuts down below 125 C), so a real reading
# is never refused, and narrow enough that every value a floating bus or a
# corrupt attribute produces (0xFFFF kelvin is 65262 C) lies outside it.
MIN_PLAUSIBLE_CELSIUS = -60
MAX_PLAUSIBLE_CELSIUS = 200


def plausible_celsius(reading: int, *, per_degree: int = 1) -> int | None:
    """Round a temperature to whole degrees, or ``None`` when it cannot be real.

    The range is checked on the integer as read, before any division: a
    sysfs attribute can hold an integer of any length, and dividing one past
    the float range raises instead of answering.

    Args:
        reading: The temperature as the device published it.
        per_degree: How many units of ``reading`` make one degree Celsius
            (1000 for the millidegrees sysfs publishes).

    Returns:
        The rounded temperature, or ``None`` outside the plausible range.

    Example:
        >>> plausible_celsius(41_600, per_degree=1000)
        42
        >>> plausible_celsius(65262) is None
        True
        >>> plausible_celsius(10**400, per_degree=1000) is None
        True
    """
    if not MIN_PLAUSIBLE_CELSIUS * per_degree <= reading <= MAX_PLAUSIBLE_CELSIUS * per_degree:
        return None
    return round(reading / per_degree)


__all__ = ["MAX_PLAUSIBLE_CELSIUS", "MIN_PLAUSIBLE_CELSIUS", "plausible_celsius"]
