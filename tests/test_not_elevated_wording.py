"""The NOT ELEVATED banner names the prompt the run went through, and joins its two halves as prose.

A run told not to ask is told so in the words of ITS prompt: Windows asks for
Administrator, Linux and macOS for root. Worded for the other one, a reader on a
Linux box is sent looking for an Administrator prompt that never exists there.
The interactive view then puts what the missing privilege cost after the
reason, and the two are one paragraph only if a space separates them.
"""

from __future__ import annotations

import pytest
from textual.widgets import Static

from lsdsk.adapters.render import report
from lsdsk.adapters.tui import LsdskApp
from lsdsk.domain.elevation import Elevation, ElevationPrompt, ElevationState
from lsdsk.domain.models import Inventory

UNPRIVILEGED = Inventory(hostname="h", privileged=False)


@pytest.mark.parametrize(
    ("prompt", "sentence"),
    [
        pytest.param(
            ElevationPrompt.UAC,
            "NOT ELEVATED: this run does not ask for Administrator (--no-elevate or display.auto_elevate = false).",
            id="uac",
        ),
        pytest.param(
            ElevationPrompt.SUDO,
            "NOT ELEVATED: this run does not ask for root (--no-elevate or display.auto_elevate = false).",
            id="sudo",
        ),
    ],
)
def test_a_run_told_not_to_ask_is_told_so_in_its_own_prompts_words(prompt: ElevationPrompt, sentence: str) -> None:
    turned_off = Elevation(state=ElevationState.TURNED_OFF, prompt=prompt)

    assert report.elevation_note(turned_off, UNPRIVILEGED) == sentence


@pytest.mark.asyncio
async def test_the_interactive_view_sets_the_reason_and_its_cost_apart_by_one_space() -> None:
    """The reason ends a sentence and the cost begins the next; run together they read as one word."""
    elevation = Elevation(state=ElevationState.TURNED_OFF, prompt=ElevationPrompt.SUDO)
    why = report.elevation_note(elevation, UNPRIVILEGED)
    cost = report.privilege_note(UNPRIVILEGED)
    assert why, "the control: this run must have a reason to give"
    assert cost, "the control: this run must have a cost to name"

    app = LsdskApp(UNPRIVILEGED, elevation=elevation)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        note = str(app.query_one("#privilege-note", Static).content)

    assert note == f"{why} {cost}"
    assert "false). Not read:" in note, note
