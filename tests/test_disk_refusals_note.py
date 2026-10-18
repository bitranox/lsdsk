"""A drive reading refused for want of Administrator is named where the reader looks for the figure.

A non-elevated Windows run cannot send ATA IDENTIFY, and IDENTIFY is the only
source Windows has for a SATA drive's two link figures. Both then read ``-`` on
the topology and disk pages, which is exactly what a reader takes for a fault
when nothing on the page says why. The refusal is recorded on the drive
(``readings_refused``), so the views key on that record, never on a dash.

The inventories are built through the real Windows builder from the committed
``windows-chipset-switch`` capture, put into the shape a non-elevated run leaves:
the reader's own refusal text in place of every ATA reading. Not ``windows-ahci``:
that is a QEMU guest whose virtual disk publishes no SATA speed at all, so an
elevated run of it reads no link either and the control below could not fail.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console, RenderableType
from textual.widgets import Static

from lsdsk.adapters.config.tunables import DisplaySettings, Tunables
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import detail, full, report
from lsdsk.adapters.tui import LsdskApp
from lsdsk.domain.elevation import Elevation, ElevationPrompt, ElevationState
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
# The reader's own wording (windows/reader.py), written out rather than imported
# from a private name: the capture format carries it as text, so text is the contract.
NEEDS_ADMINISTRATOR = "needs Administrator to open the device for passthrough"
LINK_NAMED = "the SATA link (disk and link columns)"


def _windows_machine(*, elevated: bool) -> Inventory:
    payload: dict[str, Any] = json.loads((FIXTURES / "windows-chipset-switch.json").read_text(encoding="utf-8"))
    if not elevated:
        payload["elevated"] = False
        payload["euid"] = 1
        for entry in payload["disks"].values():
            if "ata" in entry:
                entry["ata"] = {"identify_error": NEEDS_ADMINISTRATOR}
    return build_from(payload)


def _drawn(renderable: RenderableType) -> str:
    sink = io.StringIO()
    Console(width=200, file=sink, force_terminal=False).print(renderable)
    return sink.getvalue()


@pytest.mark.os_agnostic
def test_the_premise_a_non_elevated_run_loses_both_sata_link_figures() -> None:
    """Without this the tests below could pass on a capture whose links were read anyway."""
    unelevated = _windows_machine(elevated=False).disks[0]
    elevated = _windows_machine(elevated=True).disks[0]

    assert [one.reading for one in unelevated.readings_refused] == ["identify"]
    assert unelevated.link.negotiated_gbps is None
    assert unelevated.link.drive_max_gbps is None
    assert elevated.link.negotiated_gbps is not None


@pytest.mark.os_agnostic
def test_the_privilege_note_names_the_sata_link_a_refused_identify_cost() -> None:
    note = report.privilege_note(_windows_machine(elevated=False))

    assert LINK_NAMED in note, note
    assert "Administrator" in note, note


@pytest.mark.os_agnostic
def test_the_privilege_note_says_nothing_of_a_link_nobody_refused() -> None:
    from lsdsk.domain.models import Inventory

    assert report.privilege_note(_windows_machine(elevated=True)) == ""
    # Unprivileged with no drive refused: the health columns are still missing,
    # the link is not, so it must not be named.
    bare = report.privilege_note(Inventory(hostname="h", privileged=False))
    assert bare, "an unprivileged run with no refusal still misses the SMART columns"
    assert "SATA link" not in bare, bare


@pytest.mark.os_agnostic
def test_the_default_page_carries_the_link_sentence() -> None:
    tunables = Tunables(DEFAULT_THRESHOLDS, DisplaySettings())
    page = _drawn(full.render_full(_windows_machine(elevated=False), [], width=200, tunables=tunables))

    assert LINK_NAMED in page


@pytest.mark.os_agnostic
def test_the_disk_panel_names_the_refused_reading_and_its_reason() -> None:
    machine = _windows_machine(elevated=False)
    record = detail.disk_detail(machine.disks[0], machine, thresholds=DEFAULT_THRESHOLDS)

    refused = [group for group in record.groups if group.label is detail.REFUSED]
    assert len(refused) == 1, [group.label for group in record.groups]
    assert refused[0].values == (("identify", (NEEDS_ADMINISTRATOR, "")),)


@pytest.mark.os_agnostic
def test_a_disk_nothing_refused_has_no_refused_group() -> None:
    machine = _windows_machine(elevated=True)
    record = detail.disk_detail(machine.disks[0], machine, thresholds=DEFAULT_THRESHOLDS)

    assert detail.REFUSED not in [group.label for group in record.groups]


async def _privilege_note_on(inventory: Inventory, page: str) -> str:
    app = LsdskApp(inventory)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press(page)
        await pilot.pause()
        note = app.query_one("#privilege-note", Static)
        return str(note.content) if note.display else ""


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize("page", ["1", "3"], ids=["topology", "disks"])
async def test_the_interactive_view_names_the_missing_link_on_the_pages_that_draw_it(page: str) -> None:
    """The two pages the user read the dashes on, driven by their own keys."""
    assert LINK_NAMED in await _privilege_note_on(_windows_machine(elevated=False), page)


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_interactive_view_draws_no_privilege_note_when_elevated() -> None:
    assert await _privilege_note_on(_windows_machine(elevated=True), "1") == ""


UAC = ElevationPrompt.UAC


async def _privilege_note_after(inventory: Inventory, elevation: Elevation | None) -> str:
    app = LsdskApp(inventory, elevation=elevation)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        note = app.query_one("#privilege-note", Static)
        return str(note.content) if note.display else ""


@pytest.mark.os_agnostic
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("elevation", "says"),
    [
        pytest.param(Elevation(state=ElevationState.DECLINED, prompt=UAC), "declined", id="declined"),
        pytest.param(
            Elevation(state=ElevationState.FAILED, prompt=UAC, detail="Windows error 5"), "Windows error 5", id="failed"
        ),
        pytest.param(Elevation(state=ElevationState.TURNED_OFF, prompt=UAC), "--no-elevate", id="turned off"),
    ],
)
async def test_a_run_that_stayed_unelevated_says_so_and_why(elevation: Elevation, says: str) -> None:
    """The user's decision: a refused prompt runs on unelevated, "but mark that clearly in the TUI"."""
    note = await _privilege_note_after(_windows_machine(elevated=False), elevation)

    assert note.startswith("NOT ELEVATED"), note
    assert says in note, note
    assert LINK_NAMED in note, "the reason was given but not what it cost"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_nothing_asked_draws_only_what_the_missing_privilege_cost() -> None:
    note = await _privilege_note_after(_windows_machine(elevated=False), Elevation(state=ElevationState.NOT_ASKED))

    assert "NOT ELEVATED" not in note
    assert LINK_NAMED in note


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_an_elevated_run_draws_no_note_whatever_it_was_told() -> None:
    assert (
        await _privilege_note_after(
            _windows_machine(elevated=True), Elevation(state=ElevationState.DECLINED, prompt=UAC)
        )
        == ""
    )
