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

    The band is chosen on the figure as it would be WRITTEN, not on the raw
    value: 9.96 rounds to ten and is written as a whole number, 0.0996 rounds
    to a tenth and is written with one decimal, and 0.00096 rounds to 0.001
    and needs no fourth decimal.

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
        >>> format_rate(9.96), format_rate(0.0996), format_rate(0.00096)
        ('10', '0.1', '0.001')
    """
    if per_hour <= 0:
        return "0"
    if float(f"{per_hour:.1f}") >= _WHOLE_FROM:
        return f"{per_hour:.0f}"
    if float(f"{per_hour:.{_SMALL_DECIMALS}f}") >= _ONE_DECIMAL_FROM:
        return f"{per_hour:.1f}"
    return _small_rate(per_hour)


def _small_rate(per_hour: float) -> str:
    """A rate below a tenth, with as many decimals as reach its first significant digit.

    The count is read off the figure after rounding, because rounding can
    carry into the place before: 0.00096 needs four decimals to show its first
    digit and rounds to ``0.0010``, whose first digit is already the third.
    """
    decimals = max(_SMALL_DECIMALS, -math.floor(math.log10(per_hour)))
    written = f"{per_hour:.{decimals}f}"
    fraction = written.partition(".")[2]
    reached = len(fraction) - len(fraction.lstrip("0")) + 1
    return f"{per_hour:.{max(_SMALL_DECIMALS, reached)}f}" if reached < decimals else written


__all__ = ["format_rate"]
