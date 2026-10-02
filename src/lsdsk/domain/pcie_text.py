"""How a finding writes a link figure - PCIe or USB - and a bandwidth in a sentence.

Kept below every rule module so a rule can be written without importing the
module that runs all of them: ``diagnostics.diagnose`` calls the fabric-link
rules, so those rules cannot import ``diagnostics`` for its formatters.

System Role:
    Domain layer. Pure text from numbers.
"""

from __future__ import annotations

from .models import UsbSpeed, pcie_generation

#: How a sentence writes a link whose width was read as zero. The same word the
#: render layer's ``theme.NO_LINK`` draws in a column, held together with it by
#: ``tests/test_one_spelling_for_a_generation.py``.
_NO_LINK_TRAINED = "none"


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
        The figure as a sentence writes it; ``PCIe none`` for a width READ as
        zero, whatever speed sits beside it, as the column draws it; or
        ``PCIe unknown`` when either half was not read - never a half-figure,
        which would read as a measurement.

    Example:
        >>> format_pcie_sentence(8.0, 8)
        'PCIe Gen3x8'
        >>> format_pcie_sentence(2.5, 0)
        'PCIe none'
        >>> format_pcie_sentence(None, None)
        'PCIe unknown'
    """
    if width == 0:
        # No lane trained. Spelling the halves would write "Gen1x0" from a
        # register's reset speed, a figure that says a link runs.
        return f"PCIe {_NO_LINK_TRAINED}"
    generation = pcie_generation(speed_gtps)
    if generation is None or width is None:
        return "PCIe unknown"
    return f"PCIe Gen{generation}x{width}"


def format_usb_sentence(speed: UsbSpeed) -> str:
    """Render a USB rate for a sentence, closed and carrying what it is worth.

    The bare figure is the rate on the box, and two links can share one:
    10 Gb/s from one Gen 2 lane and from two Gen 1 lanes both read ``USB10G``
    while carrying 1.21 and 1.00 GB/s. A sentence naming only the figure then
    says a link runs "at USB10G but both ends support USB10G", which reads as no
    fault at all. So the sentence writes what the column writes, bandwidth
    included, and ``tests/test_one_spelling_for_a_generation.py`` holds the two
    together, as it does for ``format_pcie_sentence``.

    Args:
        speed: The rate, read.

    Returns:
        The figure with its bandwidth in parentheses.

    Example:
        >>> from .enums import UsbLaneRate
        >>> format_usb_sentence(UsbSpeed(lane_rate=UsbLaneRate.GEN1, lanes=2))
        'USB10G (1.00 GB/s)'
        >>> format_usb_sentence(UsbSpeed(lane_rate=UsbLaneRate.GEN2))
        'USB10G (1.21 GB/s)'
    """
    return f"{speed.figure} ({format_gbytes(speed.bandwidth_gbps)})"


__all__ = ["format_gbytes", "format_pcie_sentence", "format_usb_sentence"]
