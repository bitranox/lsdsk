"""How a finding writes a PCIe figure and a bandwidth in a sentence.

Kept below every rule module so a rule can be written without importing the
module that runs all of them: ``diagnostics.diagnose`` calls the fabric-link
rules, so those rules cannot import ``diagnostics`` for its formatters.

System Role:
    Domain layer. Pure text from numbers.
"""

from __future__ import annotations

from .models import pcie_generation


def format_gbytes(value: float | None) -> str:
    """Render a GB/s figure for a message, or a placeholder when unknown.

    Args:
        value: The bandwidth in GB/s, or ``None`` if unread.

    Returns:
        The figure with two decimals and its unit, or ``unknown``.

    Example:
        >>> format_gbytes(7.876)
        '7.88 GB/s'
        >>> format_gbytes(None)
        'unknown'
    """
    return "unknown" if value is None else f"{value:.2f} GB/s"


def format_pcie_sentence(speed_gtps: float | None, width: int | None) -> str:
    """Render a PCIe link as a generation and width, for a sentence.

    Written closed and in the marketing form, as the render layer writes it in a
    column. One spelling for the whole tool: a finding that said ``PCIe 3.0x8``
    while the table above it said ``Gen3x8`` would be describing the same link in
    two hands, and a reader comparing the two has to work out that they agree.

    The domain cannot reach the render layer's formatter, so this is the second
    place that spelling is written, and the two are held together by a test in
    ``tests/test_one_spelling_for_a_generation.py`` rather than by convention.

    Args:
        speed_gtps: The link's speed in GT/s per lane, or ``None`` if unread.
        width: The link's negotiated lane count, or ``None`` if unread.

    Returns:
        The figure as a sentence writes it, or ``PCIe unknown`` when either half
        was not read - never a half-figure, which would read as a measurement.

    Example:
        >>> format_pcie_sentence(8.0, 8)
        'PCIe Gen3x8'
        >>> format_pcie_sentence(None, None)
        'PCIe unknown'
    """
    generation = pcie_generation(speed_gtps)
    if generation is None or width is None:
        return "PCIe unknown"
    return f"PCIe Gen{generation}x{width}"


__all__ = ["format_gbytes", "format_pcie_sentence"]
