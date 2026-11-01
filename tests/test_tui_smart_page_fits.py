"""The interactive SMART page is laid out at the window's width, as ``lsdsk smart`` is at a terminal's.

The page used to hold ``render_smart`` fitted to the piped default of 120 columns whatever the
window was, so in a narrower window every attribute table was squeezed by Rich after it had been
fitted, and its rows wrapped onto a second line below about 70 columns; in a wider one the title
cut and the column choice never saw the room it had. Key 5 and ``lsdsk smart`` are one view under
one name, so the yardstick is the printed section itself, rendered at the width the page was
laid out at, line for line.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console
from textual.widgets import TabbedContent

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.long_page import LongPage

if TYPE_CHECKING:
    from textual.pilot import Pilot

    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

#: Pauses to wait for the page to lay itself out at a new width before calling it lost.
LAYOUT_PAUSES = 50

#: Narrow enough that a table fitted at 120 columns wraps, and wide enough to have room past it.
WIDTHS = (50, 60, 70, 90, 140, 200)


def _machine(capture: str) -> Inventory:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{capture}.json").read_text(encoding="utf-8"))
    return build_from(payload)


async def _laid_out(pilot: Pilot[None], body: LongPage) -> int:
    """Pause until the page has laid itself out for the size it now has, and return that width.

    The page lays out when it is next drawn, which follows the resize, so the
    wait is on the layout itself rather than on a count of pauses.
    """
    width = 0
    for _ in range(LAYOUT_PAUSES):
        await pilot.pause()
        width = body.size.width
        room = {width, width - body.styles.scrollbar_size_vertical}
        if width and body.line_count and body.virtual_size.width in room:
            return body.virtual_size.width
    raise AssertionError(f"the page never laid out for {width} columns: it holds {body.virtual_size.width}")


def _printed(inventory: Inventory, width: int) -> list[str]:
    """What ``lsdsk smart`` draws on a terminal of ``width`` columns, line by line."""
    console = Console(width=width, color_system=None, file=io.StringIO())
    rendered = console.render_lines(report.render_smart(inventory, width=width), pad=False)
    return ["".join(segment.text for segment in line).rstrip() for line in rendered]


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["linux-sas-hba", "windows-ahci"])
async def test_the_smart_page_draws_what_lsdsk_smart_draws_at_its_width(capture: str) -> None:
    app = LsdskApp(_machine(capture))
    async with app.run_test(size=(WIDTHS[0], 45)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "smart", "the premise: key 5 opens the SMART page"
        body = app.query_one("#smart-body", LongPage)
        differ: list[str] = []
        for width in WIDTHS:
            await pilot.resize_terminal(width, 45)
            laid_out = await _laid_out(pilot, body)
            drawn = [body.render_line(y).text.rstrip() for y in range(body.line_count)]
            printed = _printed(app.inventory, laid_out)
            if drawn != printed:
                first = next(
                    (index for index, pair in enumerate(zip(drawn, printed, strict=False)) if pair[0] != pair[1]),
                    min(len(drawn), len(printed)),
                )
                shown = drawn[first] if first < len(drawn) else "<end>"
                expected = printed[first] if first < len(printed) else "<end>"
                differ.append(f"window {width} (page {laid_out}): line {first} is {shown!r}, printed {expected!r}")
    assert not differ, "\n".join(differ)
