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
from rich.cells import cell_len
from rich.console import Console

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.safe_console import ascii_fallback
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import tables
from lsdsk.adapters.render.layout import ELLIPSIS
from lsdsk.adapters.render.trend import render_trend, trend_rows, verdict_text
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.history import History, record
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
    """The table's data rows: the lines after the header.

    A table with no rows draws no header (the filesystems view over a list that
    was not read or read empty), so there is nothing to look for.
    """
    if not count:
        return []
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
    if not count:
        return []
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


#: Two readings of one machine fifteen hours apart: the pair a trend needs, and
#: the one whose `sdd` total runs to seven digits.
_EARLIER, _LATER = "2026-08-05T01:23:00+00:00", "2026-08-05T17:03:00+00:00"


def _trended_machine() -> tuple[Inventory, History]:
    """The later capture, with the earlier one recorded as its past."""
    earlier, _ = _machine("linux-sas-hba")
    later, _ = _machine("linux-sas-hba-later")
    history = record(History(hostname="linux-sas-hba"), earlier.disks, _EARLIER)
    return later, record(history, later.disks, _LATER)


def _trend_table_lines(inventory: Inventory, history: History, width: int) -> list[str]:
    """The trend table alone, as a console of this width draws it: title, header, rows."""
    table = render_trend(inventory, history, width=width).renderables[0]
    console = Console(width=width, record=True, color_system=None, file=io.StringIO())
    console.print(table)
    return console.export_text().splitlines()


def _ends_with_a_prefix_of(row: str, verdict: str) -> bool:
    """Whether a row ends in its verdict, whole or cut with the ASCII clip mark."""
    drawn = row.rstrip()
    if drawn.endswith(verdict):
        return True
    cut = drawn.removesuffix(ELLIPSIS)
    return cut != drawn and any(cut.endswith(verdict[:length]) for length in range(1, len(verdict)))


def test_every_trend_row_keeps_its_total_and_its_verdict_on_one_line_at_every_width() -> None:
    """The trend table is laid out as the other tables are: one line, whole counts, a verdict.

    It used to fit its columns and then hand Rich whatever it had: below about
    forty-six columns Rich cut a seven-digit total to ``21961`` and an ellipsis
    and dropped the verdict, the one column the table exists for; and at the
    piped default a long verdict wrapped onto a second line that read as a row.
    """
    inventory, history = _trended_machine()
    rows = trend_rows(inventory, history)
    assert any(row.trend.latest is not None and row.trend.latest >= 1_000_000 for row in rows), (
        "the control: no seven-digit total, so a cut count could not show"
    )
    for width in range(20, 201):
        lines = _trend_table_lines(inventory, history, width)
        where = f"trend at width {width}"
        text = "\n".join(lines)
        assert RICH_ELLIPSIS not in text, f"{where}: Rich cut a cell\n{text}"
        overlong = [line for line in ascii_fallback(text, "cp437").splitlines() if len(line) > width]
        assert not overlong, f"{where}: wider than the console on cp437: {overlong}"
        drawn = lines[-len(rows) :]
        assert all(line.startswith("/") for line in drawn), f"{where}: a row wrapped\n{text}"
        assert sum(1 for line in lines if line.startswith("/")) == len(rows), f"{where}: a row wrapped\n{text}"
        header = lines[-len(rows) - 1].split()
        assert "total" in header, f"{where}: the totals were dropped\n{text}"
        assert any(title.startswith("v") for title in header), f"{where}: the verdict was dropped\n{text}"
        for line, row in zip(drawn, rows, strict=True):
            assert str(row.trend.latest) in line.split(), f"{where}: total {row.trend.latest} cut in {line!r}"
            assert _ends_with_a_prefix_of(line, verdict_text(row.trend)), f"{where}: verdict lost in {line!r}"


def test_the_trend_command_at_a_narrow_piped_width_keeps_every_total_and_verdict(
    production_factory: Callable[[], AppServices], tmp_path: Path
) -> None:
    """The reported case through the real command: `trend` at a piped width of 34 over a recorded pair."""
    store = tmp_path / "history.json"
    runner = CliRunner(charset="cp437")

    def run(command: str, capture: str, *options: str) -> list[str]:
        argv = ["--history-file", str(store), *options, command, "--replay", str(FIXTURES / f"{capture}.json")]
        result = runner.invoke(cli, argv, obj=production_factory)
        assert result.exception is None or isinstance(result.exception, SystemExit), result.output
        return result.stdout.splitlines()

    run("record", "linux-sas-hba")
    run("record", "linux-sas-hba-later")
    assert store.is_file(), "the control: nothing was recorded, so the trend has no past to read"
    narrow = run("trend", "linux-sas-hba-later", "--no-record", "--set", "display.piped_width=34")
    assert any("2196127" in line for line in narrow), "\n".join(narrow)
    assert any("rising" in line for line in narrow), "\n".join(narrow)
    assert all(len(line) <= 34 for line in narrow), "\n".join(line for line in narrow if len(line) > 34)


#: The tables whose first column the layout never narrows by fitting, so a cut
#: first cell can only be the one-line cut's work. The filesystems table's
#: first column is flexible and may be narrowed before any cut.
_CUT_FIRST = ("disks", "controllers", "health")


def _table_width(text: str) -> int:
    """How wide Rich drew the table: every line of it is padded to that width."""
    return max(cell_len(line) for line in text.splitlines())


#: Where a marked row's first data cell starts: the two-cell marker column and
#: the cell of padding Rich puts on each side of the boundary after it.
_FIRST_CELL_AFTER_MARKER = 4


def _first_cells(rows: list[str], *, marker: bool) -> list[str]:
    """Each row's first data cell: after the marker column and its padding, where there is one."""
    offset = _FIRST_CELL_AFTER_MARKER if marker else 0
    return [row[offset:].split(" ", 1)[0] for row in rows]


@pytest.mark.parametrize("capture", CAPTURES)
def test_the_one_line_cut_takes_no_more_of_a_row_than_the_console_lacks(capture: str) -> None:
    """Where the cut clips a row's first cell, the table it leaves fills the console exactly.

    A table drawn by Rich is narrower than the layout reckons - its rows carry a
    two-cell marker where the layout counts a three-cell gutter - and the cut is
    told so. Told nothing, it took one cell more than the row overran by, and a
    device name lost a character the console had room for.
    """
    inventory, findings = _machine(capture)
    clipped = 0
    for name, (render, count) in _tables(inventory, findings).items():
        if name not in _CUT_FIRST:
            continue
        for width in range(14, 81):
            text = _text(render(width), width)
            if any(cell.endswith(ELLIPSIS) for cell in _first_cells(_rows(text, count), marker=True)):
                clipped += 1
                assert _table_width(text) == width, f"{name} at {width}: the cut left room unused\n{text}"
    assert clipped, f"the control: no table of {capture} was cut into its first column, so this asserted nothing"


def test_the_one_line_cut_of_the_trend_table_takes_no_more_than_the_console_lacks() -> None:
    """The same for the table that draws no marker column, which has more room still than the layout reckons."""
    inventory, history = _trended_machine()
    rows = trend_rows(inventory, history)
    clipped = 0
    for width in range(14, 81):
        text = "\n".join(_trend_table_lines(inventory, history, width))
        drawn = text.splitlines()[-len(rows) :]
        if any(cell.endswith(ELLIPSIS) for cell in _first_cells(drawn, marker=False)):
            clipped += 1
            assert _table_width(text) == width, f"trend at {width}: the cut left room unused\n{text}"
    assert clipped, "the control: the trend table was never cut into its first column"


def test_a_trend_row_whose_verdict_is_cut_uses_the_full_width() -> None:
    """Once the verdict is cut, it takes back every cell the columns dropped beside it left free.

    Fitting narrowed the verdict to its minimum before it dropped the rate
    column, and never gave back what the drop freed: at eighty columns every row
    ended at column 67 with the verdict cut to fourteen cells and thirteen free.
    A row whose verdict is cut must end at the console's edge. The control is a
    row whose verdict fits whole at the same width, which must not be stretched.
    """
    inventory, history = _trended_machine()
    rows = trend_rows(inventory, history)
    cut_rows = whole_rows = 0
    for width in range(20, 201):
        drawn = _trend_table_lines(inventory, history, width)[-len(rows) :]
        for line, row in zip(drawn, rows, strict=True):
            verdict = verdict_text(row.trend)
            if line.rstrip().endswith(verdict):
                whole_rows += 1
                continue
            cut_rows += 1
            assert line.rstrip().endswith(ELLIPSIS), f"width {width}: {verdict!r} neither whole nor cut in {line!r}"
            assert cell_len(line.rstrip()) == width, f"width {width}: a cut verdict left room unused: {line!r}"
    assert cut_rows, "the control: no verdict was ever cut, so the width it takes back was never asked"
    assert whole_rows, "the control: no verdict ever fitted whole, so the cut rows had nothing to differ from"
