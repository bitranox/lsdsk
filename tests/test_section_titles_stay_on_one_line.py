"""Every section title takes one physical line at every width.

The titles name the machine: ``Disks on <host>``, ``Controllers on <host>``,
``Filesystems on <host>``, ``Disk health on <host>``, ``Counter trends on
<host>`` and ``SMART attributes on <host>``. Handed to Rich whole, a title
wrapped wherever it was wider than the room it was drawn in, and stranded the
hostname on a line of its own, which reads as a second heading. A table's title
is drawn at the TABLE's width rather than the console's, so it wrapped on a wide
console too, wherever the columns that fit came to less than the title.

A title is now cut to the console, in terminal cells, with the ASCII clip mark,
and never wrapped. Only the first line of each section is rendered: Rich yields
a section line by line, so the rows below the title are never drawn, which keeps
a sweep of every capture at every width from 20 to 200 cheap.
"""

from __future__ import annotations

import functools
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.segment import Segment
from rich.table import Table

from lsdsk import __init__conf__
from lsdsk.adapters.config.tunables import DisplaySettings, Tunables
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import full, report, tables
from lsdsk.adapters.render.layout import ELLIPSIS
from lsdsk.adapters.render.trend import render_trend
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.history import History, record
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import RenderableType

    from lsdsk.domain.models import Finding, Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))
WIDTHS = range(20, 201)

#: A hostname of characters two terminal cells wide each: counted in
#: characters it is half the room it takes on screen.
WIDE_HOSTNAME = "".join(map(chr, (0x30B5, 0x30FC, 0x30D0, 0x30FC))) * 4

#: Two readings of one machine fifteen hours apart: the pair a trend needs.
_EARLIER, _LATER = "2026-08-05T01:23:00+00:00", "2026-08-05T17:03:00+00:00"


@functools.cache
def _machine(capture: str) -> tuple[Inventory, tuple[Finding, ...]]:
    inventory = build_from(json.loads((FIXTURES / f"{capture}.json").read_text(encoding="utf-8")))
    return inventory, tuple(diagnose(inventory, thresholds=DEFAULT_THRESHOLDS))


def _sections(inventory: Inventory, findings: tuple[Finding, ...]) -> dict[str, Callable[[int], RenderableType]]:
    """Each titled section, as a function of width, keyed by the words before the hostname."""
    return {
        "Disks on": lambda width: tables.render_disks(inventory, findings, width=width),
        "Controllers on": lambda width: tables.render_controllers(inventory, findings, width=width),
        "Filesystems on": lambda width: tables.render_filesystems(inventory, findings, width=width),
        "Disk health on": lambda width: tables.render_health(
            inventory, findings, width=width, thresholds=DEFAULT_THRESHOLDS
        ),
        "SMART attributes on": lambda width: report.render_smart(inventory, width=width),
    }


def _first_line(section: RenderableType, width: int) -> str:
    """The first line a console of this width draws for a section, and nothing after it."""
    console = Console(width=width, color_system=None, file=io.StringIO())
    first = next(iter(Segment.split_lines(console.render(section, console.options))))
    return "".join(segment.text for segment in first).rstrip()


def _whole_or_marked(title: str, drawn: str, width: int) -> str:
    """Why ``drawn`` is not ``title`` on one line of ``width`` cells, or an empty string."""
    if cell_len(drawn) > width:
        return f"{drawn!r} is {cell_len(drawn)} cells on a console of {width}"
    if drawn == title:
        return ""
    if drawn.endswith(ELLIPSIS) and title.startswith(drawn.removesuffix(ELLIPSIS)):
        return ""
    return f"{drawn!r} is neither {title!r} nor a marked cut of it, so the rest went to another line"


def _trended(hostname: str | None = None) -> tuple[Inventory, History]:
    """The later SAS capture, with the earlier one recorded as its past, so the trend has rows."""
    earlier, _ = _machine("linux-sas-hba")
    later, _ = _machine("linux-sas-hba-later")
    if hostname is not None:
        earlier, later = earlier.with_changes(hostname=hostname), later.with_changes(hostname=hostname)
    history = record(History(hostname=later.hostname), earlier.disks, _EARLIER)
    return later, record(history, later.disks, _LATER)


@pytest.mark.parametrize("capture", CAPTURES)
def test_every_section_title_takes_one_line_at_every_width(capture: str) -> None:
    inventory, findings = _machine(capture)
    for words, section in _sections(inventory, findings).items():
        title = f"{words} {inventory.hostname}"
        for width in WIDTHS:
            problem = _whole_or_marked(title, _first_line(section(width), width), width)
            assert not problem, f"{capture}, {words} at width {width}: {problem}"


def test_the_trend_title_takes_one_line_at_every_width() -> None:
    inventory, history = _trended()
    title = f"Counter trends on {inventory.hostname}"
    premise = render_trend(inventory, history, width=200).renderables[0]
    assert isinstance(premise, Table), "the premise: the two readings give the trend rows to draw"
    for width in WIDTHS:
        problem = _whole_or_marked(title, _first_line(render_trend(inventory, history, width=width), width), width)
        assert not problem, f"width {width}: {problem}"


def test_a_title_is_whole_wherever_the_console_has_room_for_it() -> None:
    """The cut is taken only where it must be: a title that fits is never marked."""
    inventory, findings = _machine("linux-btrfs-raid1-zoned-xfs")
    for words, section in _sections(inventory, findings).items():
        title = f"{words} {inventory.hostname}"
        for width in range(cell_len(title), 201):
            assert _first_line(section(width), width) == title, f"{words} at width {width}"


def test_a_wide_character_hostname_keeps_every_title_on_one_line() -> None:
    """The cut is measured in the cells a name takes on screen, not in its characters."""
    inventory, findings = _machine("linux-minimal")
    inventory = inventory.with_changes(hostname=WIDE_HOSTNAME)
    for words, section in _sections(inventory, findings).items():
        title = f"{words} {WIDE_HOSTNAME}"
        for width in WIDTHS:
            problem = _whole_or_marked(title, _first_line(section(width), width), width)
            assert not problem, f"{words} at width {width}: {problem}"
    trended, history = _trended(WIDE_HOSTNAME)
    title = f"Counter trends on {WIDE_HOSTNAME}"
    for width in WIDTHS:
        problem = _whole_or_marked(title, _first_line(render_trend(trended, history, width=width), width), width)
        assert not problem, f"trend at width {width}: {problem}"


# The default page carries two headings of its own above the sections that title
# themselves, and the banner above everything: each names the machine as well.
_PAGE_HEADINGS = ("Topology on", "Findings on")


def _page_lines(inventory: Inventory, findings: tuple[Finding, ...], width: int) -> list[str]:
    """Every line the default page draws at ``width``, as the reader sees them."""
    console = Console(width=width, color_system=None, file=io.StringIO())
    page = full.render_full(inventory, findings, width, tunables=Tunables(DEFAULT_THRESHOLDS, DisplaySettings()))
    return ["".join(segment.text for segment in line).rstrip() for line in console.render_lines(page, pad=False)]


def _banner_lead(inventory: Inventory) -> str:
    """The part of the banner no width gives up: the build and the machine it describes."""
    return f"lsdsk {__init__conf__.version}  {inventory.hostname}"


def _banner_problem(inventory: Inventory, drawn: str, width: int) -> str:
    """Why ``drawn`` is not the banner kept to one line of ``width`` cells, or an empty string."""
    lead = _banner_lead(inventory)
    if cell_len(drawn) > width:
        return f"{drawn!r} is {cell_len(drawn)} cells on a console of {width}"
    if drawn.startswith(lead):
        return ""
    if drawn.endswith(ELLIPSIS) and lead.startswith(drawn.removesuffix(ELLIPSIS)):
        return ""
    return f"{drawn!r} is neither {lead!r} with whole fields after it nor a marked cut of it"


def _page_widths(inventory: Inventory) -> range:
    """Every width at which a heading or the banner lead is cut, and a little past the last of them."""
    longest = max(cell_len(_banner_lead(inventory)), *(cell_len(f"{w} {inventory.hostname}") for w in _PAGE_HEADINGS))
    return range(20, longest + 3)


def _assert_the_page_keeps_its_headings_on_one_line(inventory: Inventory, findings: tuple[Finding, ...]) -> None:
    for width in _page_widths(inventory):
        lines = _page_lines(inventory, findings, width)
        problem = _banner_problem(inventory, lines[0], width)
        assert not problem, f"banner at width {width}: {problem}"
        for words in _PAGE_HEADINGS:
            title = f"{words} {inventory.hostname}"
            drawn = next(line for line in lines if line.startswith(words))
            problem = _whole_or_marked(title, drawn, width)
            assert not problem, f"{words} at width {width}: {problem}"


@pytest.mark.parametrize("capture", CAPTURES)
def test_the_default_page_keeps_its_own_headings_and_banner_on_one_line(capture: str) -> None:
    inventory, findings = _machine(capture)
    _assert_the_page_keeps_its_headings_on_one_line(inventory, findings)


def test_a_wide_character_hostname_keeps_the_default_page_headings_on_one_line() -> None:
    inventory, findings = _machine("linux-minimal")
    _assert_the_page_keeps_its_headings_on_one_line(inventory.with_changes(hostname=WIDE_HOSTNAME), findings)


@pytest.mark.parametrize("capture", CAPTURES)
def test_the_banner_takes_one_line_at_every_width(capture: str) -> None:
    """The banner the section commands print is the default page's own, held at every width."""
    inventory, _ = _machine(capture)
    for width in WIDTHS:
        problem = _banner_problem(inventory, _first_line(report.render_header(inventory, width=width), width), width)
        assert not problem, f"{capture} at width {width}: {problem}"


def test_the_banner_is_whole_wherever_the_console_has_room_for_it() -> None:
    """A field is given up only where it must be: a banner that fits loses nothing."""
    inventory, _ = _machine("linux-btrfs-raid1-zoned-xfs")
    whole = _first_line(report.render_header(inventory, width=400), 400)
    assert whole.startswith(_banner_lead(inventory)), "the premise: the lead opens the banner"
    assert f"{len(inventory.disks)} disks on" in whole, "the premise: the counts are fields of the banner"
    for width in range(cell_len(whole), 201):
        assert _first_line(report.render_header(inventory, width=width), width) == whole, f"width {width}"
