"""A store that could not be read must never read as a store that is empty.

The two produce the same trend section - no rows - and mean opposite things. One
says nothing has happened yet, which is reassuring; the other says the record
somebody has been keeping is unreachable, which is a fault to fix. The refusal
already reaches stderr, but a caller that archives stdout alone, and a reader on
the interactive page where there is no stderr, would be told the reassuring one.

So the fact is asserted at all three places that draw the section - the `trend`
command, the whole-machine page, and the interactive page - because the wear
floor reached three of its four consumers once and the miss was invisible.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console
from textual.widgets import Static

from lsdsk.adapters.cli import cli
from lsdsk.adapters.history.store import HistoryRead
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.full import render_full
from lsdsk.adapters.render.trend import render_trend
from lsdsk.adapters.tui import LsdskApp
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.history import History

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.domain.models import Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"

#: What a store that was never written produces, and what a refused one must not.
NOTHING_EVER_RECORDED = "No counter history recorded yet"

#: The sentence the reader produced, carried whole rather than summarised.
REFUSAL = "Expecting property name enclosed in double quotes"


def machine() -> Inventory:
    """The inventory every test here draws."""
    with FIXTURE.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return build_from(payload)


def drawn(renderable: object, width: int = 118) -> str:
    """Render something to plain text the way a reader would see it.

    Whitespace is collapsed, because everything here matches on a SENTENCE and a
    console wraps one wherever the width falls. Matched against the raw
    rendering, an assertion passes or fails on the console width rather than on
    what the page says.
    """
    buffer = io.StringIO()
    Console(width=width, file=buffer, no_color=True).print(renderable)
    return " ".join(buffer.getvalue().split())


@pytest.mark.os_agnostic
def test_the_trend_command_says_the_store_was_refused_rather_than_never_written(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """On stdout, where the page a caller archives is.

    The control is the same command over a store that genuinely does not exist:
    it must still say nothing was ever recorded, or this test would pass on a
    build that simply deleted that sentence.
    """
    broken = tmp_path / "broken.json"
    broken.write_text("{ broken", encoding="utf-8")
    refused = cli_runner.invoke(
        cli, ["--history-file", str(broken), "trend", "--replay", str(FIXTURE)], obj=production_factory
    )
    said = " ".join(strip_ansi(refused.stdout).split())

    assert NOTHING_EVER_RECORDED not in said, f"a refused store reads as an empty one:\n{said}"
    assert "could not be read" in said, f"stdout never says the store was refused:\n{said}"

    absent = cli_runner.invoke(
        cli,
        ["--history-file", str(tmp_path / "absent.json"), "trend", "--replay", str(FIXTURE)],
        obj=production_factory,
    )
    unwrapped = " ".join(strip_ansi(absent.stdout).split())
    assert NOTHING_EVER_RECORDED in unwrapped, "the control lost the sentence it exists to hold"


@pytest.mark.os_agnostic
def test_the_whole_machine_page_carries_the_refusal_into_the_file_somebody_archives() -> None:
    """``render_full`` is what a pipe, a redirect and a CI log get."""
    inventory = machine()
    findings = diagnose(inventory)
    empty = History(hostname=inventory.hostname)

    refused = drawn(render_full(inventory, findings, width=118, history=HistoryRead(empty, False, REFUSAL)))
    assert NOTHING_EVER_RECORDED not in refused, "the page reads as a machine with nothing recorded yet"
    assert "could not be read" in refused

    control = drawn(render_full(inventory, findings, width=118, history=HistoryRead(empty, writable=True)))
    assert NOTHING_EVER_RECORDED in control, "without a refusal the page must still say nothing was recorded"


@pytest.mark.os_agnostic
@pytest.mark.asyncio
async def test_the_interactive_trend_page_carries_the_refusal_where_there_is_no_stderr() -> None:
    """The page a reader opens has no second channel to put a warning on."""
    inventory = machine()
    app = LsdskApp(inventory, History(hostname=inventory.hostname), store_refusal=REFUSAL)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("8")
        await pilot.pause()
        said = drawn(app.query_one("#trend-body", Static).content)

    assert NOTHING_EVER_RECORDED not in said, f"the page reads as a machine with nothing recorded yet:\n{said}"
    assert "could not be read" in said, said[:300]


@pytest.mark.os_agnostic
def test_the_refusal_is_quoted_rather_than_summarised() -> None:
    """A reader fixes the store from what the reader of it said, not from a paraphrase."""
    inventory = machine()
    said = drawn(render_trend(inventory, History(hostname=inventory.hostname), width=118, store_refusal=REFUSAL))
    assert REFUSAL in said, said[:300]
