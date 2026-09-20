"""The interactive view's own palette, and the gates that keep it honest.

The printed palette has to serve a black console AND a white one, which caps a
saturated hue at about 4.2:1 on its worst background - measured, and the reason
``theme.py`` cannot simply be brightened when somebody finds it dim. The
interactive view paints its own background, so it can carry colours the printed
one cannot, and it is then judged against THAT background instead of the four.

Three claims are held here, because the layer has three ways to go quietly
wrong: a new printed colour with no counterpart, a counterpart nobody can read,
and a counterpart that never reaches the screen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from textual.widgets import OptionList

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables, theme
from lsdsk.adapters.tui import LsdskApp
from lsdsk.adapters.tui import palette as tui_palette
from lsdsk.adapters.tui.app import PAGE_LABELS

if TYPE_CHECKING:
    from textual.pilot import Pilot

    from lsdsk.domain.models import Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

#: The size every capture of this tool is taken at, so a test that renders the
#: whole page sees what the screenshots show.
DEMO_SIZE = (180, 50)

#: WCAG's floor for body text. The printed gate sits at 4.0 because a hue
#: serving black and white at once cannot reach 4.5; this palette serves ONE
#: background family, so the excuse does not apply to it and the real floor does.
_MIN_CONTRAST = 4.5


def inventory() -> Inventory:
    """The machine every test in this module draws."""
    with FIXTURE.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return build_from(payload)


def _rgb(value: str) -> tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(v) for v in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(fore: tuple[int, int, int], back: tuple[int, int, int]) -> float:
    low, high = sorted((_relative_luminance(fore), _relative_luminance(back)))
    return (high + 0.05) / (low + 0.05)


def _printed_hues() -> set[str]:
    """Every hex colour the render layer can put in a style, read off the module.

    Read rather than listed, so a colour added to ``theme.py`` tomorrow is in
    range of these gates without anybody remembering to add it here.
    """
    return {
        value.upper()
        for name, value in vars(theme).items()
        if name.startswith("STYLE_") and isinstance(value, str) and value.startswith("#")
    }


async def _sweep_before_and_after_a_rescan(app: LsdskApp, pilot: Pilot[None]) -> list[str]:
    """Every page, as a reader opens the app on it and again after a rescan.

    The mount path and the rescan path are two ways to fill the same pane, and
    the defect this file's gate exists to catch - a second copy of a fill that
    does not go through the palette layer - lives on whichever of the two nobody
    sweeps. Measured: planting that copy on the rescan path left both palette
    suites at 94 passed, with the probe showing no printed hue before `f9` and
    two after it.

    Args:
        app: The running application.
        pilot: Its driver.

    Returns:
        One exported picture per page per pass, upper-cased for hex matching.
    """
    pictures: list[str] = []
    for pass_number in (1, 2):
        if pass_number == 2:
            await pilot.press("f9")
            await pilot.pause()
        for number, _page in enumerate(PAGE_LABELS, start=1):
            await pilot.press(str(number))
            await pilot.pause()
            pictures.append(app.export_screenshot().upper())
    return pictures


@pytest.mark.os_agnostic
def test_every_printed_colour_has_an_interactive_counterpart() -> None:
    """No colour the render layer emits may reach the view unmapped.

    The control is the scan: the printed hues come from ``theme.py`` itself, so
    a seventh colour added there with no counterpart fails here rather than
    reaching the screen in the printed palette's version of itself.
    """
    printed = _printed_hues()
    assert printed, "the control: no printed hex colours found, so this asserted nothing"
    mapped = {key.upper() for key in tui_palette.hue_map()}
    assert printed <= mapped, f"unmapped: {sorted(printed - mapped)}"
    for printed_hue, interactive_hue in tui_palette.hue_map().items():
        assert printed_hue.upper() != interactive_hue.upper(), f"{printed_hue} maps to itself"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_every_interactive_colour_is_legible_on_the_background_it_lands_on() -> None:
    """Measured against the backgrounds the app paints, not the ones it declares.

    The widget's own ``background`` style and the theme's ``$surface`` both read
    #1E1E1E here while the text is actually composited onto #272727, so a gate
    written from either would be measuring a background no character sits on.
    ``rich_style`` is what the text is drawn with, so it is what is asked.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=DEMO_SIZE) as pilot:
        backgrounds: set[tuple[int, int, int]] = set()
        for number, _page in enumerate(PAGE_LABELS, start=1):
            await pilot.press(str(number))
            await pilot.pause()
            for widget in app.screen.query("*"):
                triplet = None if widget.rich_style.bgcolor is None else widget.rich_style.bgcolor.triplet
                if triplet is not None:
                    backgrounds.add((triplet.red, triplet.green, triplet.blue))
    assert backgrounds, "the control: no background was measured, so this asserted nothing"

    failures: list[str] = []
    for name, value in sorted(tui_palette.PALETTE.roles().items()):
        for background in sorted(backgrounds):
            ratio = _contrast(_rgb(value), background)
            if ratio < _MIN_CONTRAST:
                failures.append(
                    f"{name} ({value}) is {ratio:.1f}:1 on #{background[0]:02X}{background[1]:02X}{background[2]:02X}"
                )
    assert not failures, "unreadable: " + "; ".join(failures)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_no_printed_colour_reaches_the_interactive_view() -> None:
    """Every page, end to end: the printed palette must not appear on screen.

    Asserted on the exported picture rather than on the layer's own map, which
    could only confirm the layer ran. The two-sided control is what stops it
    passing vacuously: a page showing no severity at all would satisfy "none of
    the printed six appear" while proving nothing, so the interactive six are
    required to appear as well.
    """
    printed = _printed_hues()
    # The control: the render layer really does emit these, so their absence
    # from the view is the layer's doing rather than the fixture being quiet.
    machine = inventory()
    emitted = {
        style.upper()
        for disk in machine.disks
        for _text, style in tables.disk_table_row(disk, machine.port_link_for(disk)).values()
        if style
    }
    assert any(hue in " ".join(emitted) for hue in printed), "the control: the render layer emitted no printed colour"

    app = LsdskApp(machine)
    async with app.run_test(size=DEMO_SIZE) as pilot:
        drawn = " ".join(await _sweep_before_and_after_a_rescan(app, pilot))

    leaked = sorted(hue for hue in printed if hue in drawn)
    assert not leaked, f"the printed palette reached the interactive view: {leaked}"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_interactive_palette_is_what_reaches_the_screen() -> None:
    """The other half of the gate above: these colours must actually be drawn."""
    app = LsdskApp(inventory())
    async with app.run_test(size=DEMO_SIZE) as pilot:
        drawn = " ".join(await _sweep_before_and_after_a_rescan(app, pilot))

    palette = tui_palette.PALETTE
    for role in ("at_capability", "hint", "warning", "opportunity", "unknown", "header"):
        value = getattr(palette, role).upper()
        assert value in drawn, f"{role} ({value}) is defined but never drawn"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_a_line_the_cursor_cannot_stop_on_is_drawn_like_one_it_can() -> None:
    """Nothing in the interactive view is dimmed, including a repeated header.

    Textual dims a disabled option by dropping its alpha to 0.38, and the tree's
    vertical rules run THROUGH the repeated column headers, which are disabled
    so the cursor skips them. So the rule dimmed for the length of a header and
    the tree looked broken there. Asserted as an equality between the two
    component styles rather than against a literal colour, because the point is
    that they match, not what they are.
    """
    app = LsdskApp(inventory())
    async with app.run_test(size=DEMO_SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        listing = app.query_one("#tree-lines", OptionList)
        enabled = listing.get_component_styles("option-list--option").color
        disabled = listing.get_component_styles("option-list--option-disabled").color
        assert disabled == enabled, f"a skipped line is drawn {disabled}, a reachable one {enabled}"
        assert disabled.a == 1.0, f"a colour at alpha {disabled.a} is blended toward the background, which is dimming"
