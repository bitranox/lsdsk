"""How a capacity is written, in a table column and in a finding's sentence alike.

Kept in the domain so a rule can write a capacity the way the render layer's
columns do without importing the render layer: ``theme.format_size`` and
``theme.format_size_both`` write through these two, so the figure a finding
quotes and the one in the table beside it cannot be spelled differently.

A capacity has two honest answers and they differ by 7 percent per step: a
drive is SOLD in powers of ten and REPORTS in powers of two. Written with the
scale unnamed, the figure is read as whichever the reader expects, so a drive
sold as 500 GB written ``466G`` reads as a different drive from the one on the
invoice. Both unit lists therefore name their scale.

System Role:
    Domain layer. Pure text from numbers.
"""

from __future__ import annotations

_BINARY_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_DECIMAL_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_BINARY_STEP = 1024.0
_DECIMAL_STEP = 1000.0
#: Below this a figure keeps one decimal, so ``1.5GB`` is not written ``2GB``.
_ONE_DECIMAL_BELOW = 10


def _scaled(size_bytes: int, step: float, units: tuple[str, ...]) -> str:
    """One capacity on one scale, to the unit that keeps it under three digits.

    A non-zero figure is never written as zero: below one step it is whole
    bytes, and it moves to the next unit only once it reaches one of that unit.
    """
    value = float(size_bytes)
    for unit in units:
        if value < step or unit == units[-1]:
            if unit == units[0]:
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}" if value < _ONE_DECIMAL_BELOW else f"{value:.0f}{unit}"
        value /= step
    return "-"


def format_capacity(size_bytes: int) -> str:
    """Write a capacity on the binary scale, the one the drive reports in.

    Args:
        size_bytes: Capacity in bytes.

    Returns:
        A short figure whose unit names its scale.

    Example:
        >>> format_capacity(500_107_862_016)
        '466GiB'
        >>> format_capacity(512)
        '512B'
    """
    return _scaled(size_bytes, _BINARY_STEP, _BINARY_UNITS)


def format_capacity_both(size_bytes: int) -> str:
    """Write a capacity on both scales, decimal first, as the label and the drive say it.

    Below a kilobyte the two agree and only one is written, since ``512B/512B``
    says nothing twice.

    Args:
        size_bytes: Capacity in bytes.

    Returns:
        Both figures, or the single one where they agree.

    Example:
        >>> format_capacity_both(500_107_862_016)
        '500GB/466GiB'
        >>> format_capacity_both(36_000_000)
        '36MB/34MiB'
        >>> format_capacity_both(512)
        '512B'
    """
    binary = format_capacity(size_bytes)
    decimal = _scaled(size_bytes, _DECIMAL_STEP, _DECIMAL_UNITS)
    return binary if binary == decimal else f"{decimal}/{binary}"


__all__ = ["format_capacity", "format_capacity_both"]
