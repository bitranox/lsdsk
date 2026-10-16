"""The four printed tables keep every row on one line, with its marker, at every width.

The tree has always reduced a row to one line once its undroppable columns
overran the width. The tables did not: below about forty columns they handed
Rich a table wider than the console, and Rich met that by squeezing every
column - the two-character marker column included, so a row's severity was
gone - and by cutting cells with its own ellipsis, so a link figure read
``Gen3...`` and the cut text no longer matched the ASCII ``>`` every other view
marks a cut with. On a cp437 console that ellipsis became three characters and
the rows wrapped.

The sweep runs every committed capture through all four tables at every width
from 20 to 220 and asks four things of each row: its marker is the one it
carries at the tool's own piped width, Rich never cut a cell, no line is wider
than the console once the encoding-safe stream has rewritten it for cp437, and a
link figure is either whole or not drawn at all.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from rich.console import Console

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.safe_console import ascii_fallback
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.table import Table

    from lsdsk.composition import AppServices
    from lsdsk.domain.models import Finding, Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))
#: Every width up to 64, which is where the undroppable columns run out of room
#: on every capture here, then every fifth to 220: each width is a full render
#: of four tables, and the wide end only re-checks that nothing changed.
WIDTHS = (*range(20, 65), *range(65, 221, 5))
#: The width a row's marker is read at as the reference: the tool's piped default.
REFERENCE_WIDTH = 120
#: Rich's own ellipsis. The tables cut with the ASCII clip mark instead, so this
#: character on a page means Rich cut a cell the layout handed it whole.
RICH_ELLIPSIS = "\u2026"


def _machine(capture: str) -> tuple[Inventory, list[Finding]]:
    inventory = build_from(json.loads((FIXTURES / f"{capture}.json").read_text(encoding="utf-8")))
    return inventory, list(diagnose(inventory, thresholds=DEFAULT_THRESHOLDS))


def _tables(inventory: Inventory, findings: list[Finding]) -> dict[str, tuple[Callable[[int], Table], int]]:
    """Each table, as a function of width, with the number of rows it draws."""
    return {
        "disks": (lambda width: tables.render_disks(inventory, findings, width=width), len(inventory.disks)),
        "controllers": (
            lambda width: tables.render_controllers(inventory, findings, width=width),
            len(inventory.controllers),
        ),
        "health": (
            lambda width: tables.render_health(inventory, findings, width=width, thresholds=DEFAULT_THRESHOLDS),
            len(inventory.disks),
        ),
        "filesystems": (
            lambda width: tables.render_filesystems(inventory, findings, width=width),
            len(inventory.filesystems or ()),
        ),
    }


def _text(table: Table, width: int) -> str:
    console = Console(width=width, record=True, color_system=None, file=io.StringIO())
    console.print(table)
    return console.export_text()


def _header_index(lines: list[str]) -> int:
    """Where the column headings are: the first line that opens blank, under a title that may wrap."""
    return next(index for index, line in enumerate(lines) if line.startswith(" "))


def _rows(text: str, count: int) -> list[str]:
    """The table's data rows: the lines after the header."""
    lines = text.splitlines()
    header = _header_index(lines)
    return lines[header + 1 : header + 1 + count]


def _markers(text: str, count: int) -> list[str]:
    return [row[:2] for row in _rows(text, count)]


def _link_figures(inventory: Inventory) -> dict[str, list[dict[str, str]]]:
    """Per table, each row's link figures in their plain form - a figure the rich form only extends."""
    return {
        "disks": [
            {"link": tables.disk_table_row(disk, inventory.port_link_for(disk))["link"][0]} for disk in inventory.disks
        ],
        # No severities: they decide a row's marker and never its link cells.
        "controllers": [
            {key: tables.controller_table_row(one, inventory, {}).cells[key][0] for key in ("running", "capable")}
            for one in inventory.controllers
        ],
    }


def _cut_figures(text: str, count: int, figures: list[dict[str, str]]) -> list[str]:
    """Every link figure a drawn column holds only part of."""
    lines = text.splitlines()
    header = lines[_header_index(lines)].split()
    return [
        f"{key} {figure!r} in {row!r}"
        for row, cells in zip(_rows(text, count), figures, strict=True)
        for key, figure in cells.items()
        if key in header and figure not in row
    ]


@pytest.mark.parametrize("capture", CAPTURES)
def test_every_row_keeps_its_marker_and_its_figures_whole_at_every_width(capture: str) -> None:
    inventory, findings = _machine(capture)
    figures = _link_figures(inventory)
    for name, (render, count) in _tables(inventory, findings).items():
        reference = _markers(_text(render(REFERENCE_WIDTH), REFERENCE_WIDTH), count)
        for width in WIDTHS:
            text = _text(render(width), width)
            where = f"{capture} {name} at width {width}"
            assert RICH_ELLIPSIS not in text, f"{where}: Rich cut a cell\n{text}"
            assert _markers(text, count) == reference, f"{where}: a marker moved\n{text}"
            overlong = [line for line in ascii_fallback(text, "cp437").splitlines() if len(line) > width]
            assert not overlong, f"{where}: wider than the console on cp437: {overlong}"
            assert not _cut_figures(text, count, figures.get(name, [{}] * count)), f"{where}: a link figure was cut"


def test_the_sweep_has_markers_to_lose_and_figures_to_cut() -> None:
    """The control: a sweep over rows that mark nothing, or draw no link, could not see either go."""
    inventory, findings = _machine("linux-sas-hba")
    render, count = _tables(inventory, findings)["disks"]
    text = _text(render(REFERENCE_WIDTH), REFERENCE_WIDTH)
    marked = [marker for marker in _markers(text, count) if marker.strip()]
    assert len(marked) >= 5, marked
    figures = _link_figures(inventory)["disks"]
    assert not _cut_figures(text, count, figures)
    assert _cut_figures(text.replace("Gen3x4", "Gen3>"), count, figures), "the figure check cannot see a cut"


def test_the_disks_command_on_a_cp437_console_keeps_every_marker_at_a_narrow_width(
    production_factory: Callable[[], AppServices],
) -> None:
    """The reported case, through the real command: `disks` at a piped width of 34 on a cp437 stream."""
    target = str(FIXTURES / "linux-sas-hba.json")
    runner = CliRunner(charset="cp437")

    def run(width: int) -> list[str]:
        argv = ["--no-record", "--set", f"display.piped_width={width}", "disks", "--replay", target]
        return runner.invoke(cli, argv, obj=production_factory).stdout.splitlines()

    marks = {"!", "!!", "~"}
    wide = run(REFERENCE_WIDTH)
    narrow = run(34)
    marked = sum(1 for line in wide if line[:2].strip() in marks)
    assert marked >= 5, wide
    assert sum(1 for line in narrow if line[:2].strip() in marks) == marked, "\n".join(narrow)
    assert all(len(line) <= 34 for line in narrow), "\n".join(line for line in narrow if len(line) > 34)


def test_a_filesystem_row_keeps_how_full_and_where_at_every_width() -> None:
    """`use%` and `disks` outlive the one-line cut: a row without them answers nothing."""
    inventory, findings = _machine("linux-zfs-filesystems")
    filesystems = inventory.filesystems or ()
    assert filesystems, "the capture carries no filesystem, so the sweep would assert nothing"
    for width in WIDTHS:
        text = _text(tables.render_filesystems(inventory, findings, width=width), width)
        lines = text.splitlines()
        header = lines[_header_index(lines)].split()
        assert "use%" in header, f"use% dropped at width {width}\n{text}"
        assert any(title.startswith("dis") for title in header), f"disks dropped at width {width}\n{text}"
        for row, filesystem in zip(_rows(text, len(filesystems)), filesystems, strict=True):
            assert f"{filesystem.used_percent}%" in row, f"{filesystem.name}'s fill lost at width {width}: {row!r}"
            assert filesystem.disks[0][:2] in row.split("%", 1)[1], (
                f"{filesystem.name}'s disks lost at {width}: {row!r}"
            )
