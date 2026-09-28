"""The SMART and findings pages are rendered once per width, never once per showing.

Each page is one renderable that grows with the machine - a table per drive, a
paragraph per finding. Held in a ``Static``, Textual rendered it in full to
measure its height at every width the layout tried and again to draw it, so
opening the SMART page of 1000 drives cost five full renders and 3.4 s, and a
resize three more. The page now keeps the lines of the one render its width
needs and draws only the ones in the window.

Counted rather than timed: how many pieces of text Rich lays out while the
page is opened, reopened and resized, against how many ONE render of the page
lays out, measured on its own. A full render per showing multiplies that, and a
height the record panel moves by on every page switch would add one per switch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console
from rich.text import Text
from textual.widgets import TabbedContent

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui.long_page import LongPage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import FrameType

    from rich.console import RenderableType

    from lsdsk.domain.models import Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

#: Drives on the machine. Enough that one render of the page dwarfs what the
#: record panel and the fits-in-the-window question lay out beside it, which is
#: a window's worth of text rather than a page's.
_DRIVES = 60


def _machine() -> Inventory:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base = build_from(payload)
    template = next(disk for disk in base.disks if disk.health is not None and disk.health.attributes)
    disks = tuple(
        template.with_changes(path=f"/dev/sdz{index}", node=f"sdz{index}", serial=f"Z{index}")
        for index in range(_DRIVES)
    )
    return base.with_changes(disks=disks)


class _TextCount:
    """Count the pieces of text Rich lays out while it is installed.

    A text's ``__rich_console__`` is a generator, and a profile hook sees a
    call for every resumption of one, so each is counted once by its frame.
    Only this thread is watched, which is the one the pilot drives the app on.
    """

    def __init__(self) -> None:
        self.frames: set[FrameType] = set()
        self._target = Text.__rich_console__.__code__
        self._previous: Any = None

    def __enter__(self) -> _TextCount:
        self._previous = sys.getprofile()
        sys.setprofile(self._hook)
        return self

    def __exit__(self, *_exc: object) -> None:
        sys.setprofile(self._previous)

    def _hook(self, frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is self._target:
            self.frames.add(frame)


def _one_render(page: RenderableType, width: int) -> int:
    """The texts one render of a page lays out, on its own."""
    with _TextCount() as count:
        Console(width=width, record=False).render_lines(page, pad=False)
    return len(count.frames)


async def _laid_out_during(action: Callable[[], Awaitable[None]]) -> int:
    """Run ``action`` and count the texts Rich laid out meanwhile."""
    with _TextCount() as count:
        await action()
    return len(count.frames)


def _smart_page(app: LsdskApp) -> RenderableType:
    return report.render_smart(app.inventory)


def _findings_page(app: LsdskApp) -> RenderableType:
    return report.render_findings(app.findings)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "pane", "body", "page_of"),
    [("5", "smart", "#smart-body", _smart_page), ("6", "findings", "#findings-body", _findings_page)],
    ids=["smart", "findings"],
)
async def test_a_long_page_is_rendered_once_per_width_and_not_once_per_showing(
    key: str, pane: str, body: str, page_of: Callable[[LsdskApp], RenderableType]
) -> None:
    """Opening it lays it out once, reopening it not at all, and a new width once more."""
    app = LsdskApp(_machine())
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()

        async def press(name: str) -> None:
            await pilot.press(name)
            await pilot.pause()

        async def resize() -> None:
            await pilot.resize_terminal(140, 45)
            await pilot.pause()

        once = _one_render(page_of(app), 158)
        # The control: one render of the page carries every drive, so a page
        # that drew nothing cannot pass for one drawn cheaply, and the window's
        # worth laid out beside it is small against it.
        assert once > 2 * _DRIVES, f"one render of the page laid out only {once} texts"

        opened = await _laid_out_during(lambda: press(key))
        assert app.query_one(TabbedContent).active == pane
        assert app.query_one(body, LongPage).line_count > 2 * _DRIVES, "the page does not carry every drive"
        assert once <= opened < 2 * once, (
            f"opening the page laid out {opened} texts where one render lays out {once}: it is rendered more than once"
        )

        await press("1")
        reopened = await _laid_out_during(lambda: press(key))
        assert reopened < once // 2, f"reopening the page at the same width laid out {reopened} texts of {once}"

        resized = await _laid_out_during(resize)
        assert once // 2 < resized < 2 * once, (
            f"a new width laid out {resized} texts where one render lays out {once}: it is rendered more than once"
        )
