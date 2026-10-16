"""How a counter's rate is written, in the trend table and in a finding's sentence alike.

Kept in the domain so the rule that reports a rising counter can write its rate
the way the trend table's column does without importing the render layer. The
two used to carry a formatter each, and they drifted: the finding wrote one
decimal, so a drive gaining one CRC error in thirty power-on hours was "about
0.0 an hour" - a rate of zero in the very sentence explaining why the drive is
failing now - while the table beside it said ``0.033``.

System Role:
    Domain layer. Pure text from numbers.
"""

from __future__ import annotations

import math

#: From here a decimal is noise: a tenth of an error an hour means nothing at ten.
_WHOLE_FROM = 10
#: From here one decimal is enough; below it a fixed decimal reads as zero.
_ONE_DECIMAL_FROM = 0.1
#: The fewest decimals a rate below a tenth is written with.
_SMALL_DECIMALS = 3


def format_rate(per_hour: float) -> str:
    """Write a rate per power-on hour, dropping a decimal that would be noise.

    A rate that is not zero is never written as one: below a tenth it carries
    three decimals, and more where three would still read as zero, enough to
    reach its first significant digit.

    Args:
        per_hour: The rate against the drive's own power-on hours.

    Returns:
        The figure as a reader would quote it.

    Example:
        >>> format_rate(1109.4), format_rate(1.0), format_rate(1 / 30)
        ('1109', '1.0', '0.033')
        >>> format_rate(1 / 3000), format_rate(1 / 50_000)
        ('0.0003', '0.00002')
        >>> format_rate(0.0)
        '0'
    """
    if per_hour >= _WHOLE_FROM:
        return f"{per_hour:.0f}"
    if per_hour >= _ONE_DECIMAL_FROM:
        return f"{per_hour:.1f}"
    if per_hour <= 0:
        return "0"
    decimals = max(_SMALL_DECIMALS, -math.floor(math.log10(per_hour)))
    return f"{per_hour:.{decimals}f}"


__all__ = ["format_rate"]
