"""Every number the rules judge by, in one place and overridable.

A threshold baked into a function is an untested assumption wearing a constant's
clothes: nobody can tune it for their fleet, and nobody can sweep it to find out
whether the value was ever a good one. So every judgement value lives here, with
the shipped figure as the default, and reaches the rules as an argument.

What is NOT here, deliberately: anything a specification fixes. Register offsets,
IOCTL codes, the Kelvin offset, the 8b/10b encoding divisor and the 512-byte
sector are not choices, and exposing them would let a configuration file break
decoding rather than tune it.

System Role:
    Domain values. The rules take a :class:`Thresholds`; the adapter layer builds
    one from configuration and hands it in, so the domain reads nothing itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The judgement values every rule weighs against.

    Attributes:
        wear_warning_percent: Wear at which a replacement is worth planning.
        wear_critical_percent: Wear at which the drive is at its rated endurance.
        crc_errors_significant: Interface CRC count above which a count is a
            fault rather than a note. A handful can come from one hotplug.
        mixed_firmware_threshold: How many distinct firmware revisions across
            copies of one model count as a mismatch.
        wear_projection_min_points: Percentage points of measured wear movement
            required before a wear-out date is projected. Wear is an integer, so
            one point is one unit of resolution and any rate from it is noise.
        quiet_expected_min: How many errors the drive's own lifetime rate must
            have predicted across a quiet span before that silence is evidence.
        min_span_hours: Power-on hours a span must cover before a rate is
            computed at all. Below one there is nothing to divide by.

    Example:
        >>> Thresholds().wear_critical_percent
        95
        >>> Thresholds(crc_errors_significant=10).crc_errors_significant
        10
    """

    wear_warning_percent: int = 80
    wear_critical_percent: int = 95
    crc_errors_significant: int = 100
    mixed_firmware_threshold: int = 2
    wear_projection_min_points: int = 2
    quiet_expected_min: float = 10.0
    min_span_hours: int = 1


#: What the rules use when nobody says otherwise.
DEFAULT_THRESHOLDS = Thresholds()

__all__ = ["DEFAULT_THRESHOLDS", "Thresholds"]
