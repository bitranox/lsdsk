"""The interactive pages carry the same notes and legends the printed pages do.

Two sentences were drawn by every printed page and by no interactive one: that a
disk interface could not be read, and what the ``*`` after a link figure means.
Each test drives the app and reads back what a reader is shown, with the
untouched capture as the control that keeps it from passing on a view that says
everything everywhere.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report
from lsdsk.adapters.tui import LsdskApp

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CHIPSET = "windows-chipset-switch"
SAS = "linux-sas-hba"
LEGEND = "register default, not the real link"
MARKED_FIGURE = re.compile(r"Gen\d+x\d+\*")


def _screen_text(svg: str) -> str:
    """The characters an exported Textual screenshot draws, one line per row."""
    rows: dict[int, list[tuple[float, str]]] = {}
    for found in re.finditer(
        r'<text[^>]*?x="([\d.]+)"[^>]*?clip-path="url\(#[^)]*-line-(\d+)\)"[^>]*>(.*?)</text>', svg
    ):
        x, line, content = float(found.group(1)), int(found.group(2)), found.group(3)
        rows.setdefault(line, []).append((x, html.unescape(content).replace("\xa0", " ")))
    return "\n".join("".join(text for _x, text in sorted(rows[line])) for line in sorted(rows))


def _capture(name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def _with_an_unreadable_interface() -> dict[str, Any]:
    """The windows-ahci capture with its drives replaced by one interface nobody could read."""
    payload = _capture("windows-ahci")
    payload["disks"] = {
        "unreadable disk interface 1": {
            "path": "unreadable disk interface 1",
            "parent": None,
            "ancestors": [],
            "error": "could not read the device interface path, Win32 error 5",
            "interface_unreadable": True,
        }
    }
    return payload


async def _shown(payload: dict[str, Any], *keys: str, hide_panel: bool = False) -> str:
    """What the app draws after `keys` are pressed, optionally with the detail panel hidden."""
    app = LsdskApp(build_from(payload))
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause()
        if hide_panel:
            await pilot.press("i")
        for key in keys:
            await pilot.press(key)
        await pilot.pause()
        return _screen_text(app.export_screenshot())


@pytest.mark.asyncio
async def test_the_interactive_view_says_a_disk_interface_was_not_read() -> None:
    payload = _with_an_unreadable_interface()
    sentence = report.unread_interfaces_note(build_from(payload))
    assert sentence, "the control inventory carries no unread interface, so this proves nothing"

    for keys in ((), ("3",)):
        assert sentence in await _shown(payload, *keys)


@pytest.mark.asyncio
async def test_the_interactive_view_is_silent_when_every_interface_was_read() -> None:
    screen = await _shown(_capture("windows-ahci"))
    assert "interface not read" not in screen
    assert "interfaces not read" not in screen


@pytest.mark.asyncio
async def test_a_rescan_keeps_the_unread_interface_note_in_step_with_the_inventory() -> None:
    payload = _with_an_unreadable_interface()
    sentence = report.unread_interfaces_note(build_from(payload))
    app = LsdskApp(build_from(payload))
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause()
        assert sentence in _screen_text(app.export_screenshot())
        app.inventory = build_from(_capture("windows-ahci"))
        await pilot.press("f9")
        await pilot.pause()
        assert sentence not in _screen_text(app.export_screenshot())


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "page"), [("2", "controllers"), ("7", "slots")])
async def test_a_link_marked_as_a_register_default_is_explained_on_the_page_that_draws_it(key: str, page: str) -> None:
    screen = await _shown(_capture(CHIPSET), key, hide_panel=True)

    assert MARKED_FIGURE.search(screen), f"the {page} page drew no marked figure, so the legend proves nothing"
    assert LEGEND in screen, f"the {page} page drew a marked figure and did not say what the mark is"


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "page"), [("2", "controllers"), ("7", "slots")])
async def test_a_page_without_a_marked_link_draws_no_legend(key: str, page: str) -> None:
    screen = await _shown(_capture(SAS), key, hide_panel=True)

    assert re.search(r"Gen\d+x\d+", screen), f"the {page} page drew no link figure at all"
    assert not MARKED_FIGURE.search(screen)
    assert LEGEND not in screen
