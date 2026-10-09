"""A PCIe sentence with one half unread says ``unknown``, never a half-figure.

``format_pcie_sentence`` writes ``PCIe unknown`` when EITHER the speed or the width
was not read. A generation with no width, or a width with no generation, would
read as a measurement if it were spelled, which is what the ``or`` guards. The
doctest covers the both-missing case only, so turning ``or`` into ``and`` kept
every test green while ``(8.0, None)`` became ``PCIe Gen3xNone``.
"""

from __future__ import annotations

import pytest

from lsdsk.domain.pcie_text import format_pcie_sentence


@pytest.mark.parametrize(
    ("speed_gtps", "width"),
    [(None, 8), (8.0, None), (None, None)],
    ids=["speed-unread", "width-unread", "both-unread"],
)
def test_a_link_with_an_unread_half_is_unknown(speed_gtps: float | None, width: int | None) -> None:
    """Each half alone, and both, read as unknown."""
    assert format_pcie_sentence(speed_gtps, width) == "PCIe unknown"


def test_a_link_with_both_halves_read_is_spelled() -> None:
    """The control: a fully read link is not swallowed by the guard."""
    assert format_pcie_sentence(8.0, 8) == "PCIe Gen3x8"
