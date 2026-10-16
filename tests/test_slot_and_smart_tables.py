"""The slots and SMART tables: every heading sits over its own column.

Both tables are drawn line by line rather than through a Rich ``Table``, and
both borrowed the disk tree's header, which leaves room for a severity marker
in front of its columns. Their rows have no such field, so every heading sat
three cells right of the values it names - ``port`` over the middle of an
address, ``raw`` past the end of its figures - on every machine, at every width.

A heading is held to its column by what a reader sees: a left-set column's
heading starts where its values start, and a right-set one's ends where its
values end. That is asserted on the rendered lines of every committed capture,
so it needs no knowledge of how the layout was arrived at.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.cells import cell_len
from rich.console import Group
from rich.text import Text

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report
from lsdsk.adapters.render.layout import ELLIPSIS
from lsdsk.domain.diagnostics import register_default_links
from lsdsk.domain.enums import Align

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lsdsk.adapters.render.layout import Column
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))
#: The tool's piped default and a wide console: the two widths a reader most
#: often meets, and the misalignment did not depend on either.
ALIGNMENT_WIDTHS = (120, 200)


def _machine(capture: str) -> Inventory:
    return build_from(json.loads((FIXTURES / f"{capture}.json").read_text(encoding="utf-8")))


def _texts(section: object) -> list[str]:
    """The plain text of each line a section draws, in order."""
    assert isinstance(section, Group), f"the section is no longer a group of lines: {type(section)}"
    return [item.plain for item in section.renderables if isinstance(item, Text)]


def _slot_table(inventory: Inventory, width: int) -> tuple[str, list[str]]:
    """The slots section's header line and its rows, as text."""
    lines = _texts(report.render_slots(inventory, width=width))
    return lines[1], lines[2 : 2 + len(inventory.slots)]


def _smart_tables(inventory: Inventory, width: int) -> list[tuple[str, list[str]]]:
    """Each disk's attribute header and rows from the SMART section, as text."""
    lines = _texts(report.render_smart(inventory, width=width))
    tables: list[tuple[str, list[str]]] = []
    for disk in inventory.disks:
        attributes = () if disk.health is None else disk.health.attributes
        if not attributes:
            continue
        heading = next(index for index, line in enumerate(lines) if line.startswith(f"{disk.path}  "))
        tables.append((lines[heading + 1], lines[heading + 2 : heading + 2 + len(attributes)]))
    return tables


def _misplaced_headings(columns: Sequence[Column], header: str, rows: Sequence[str]) -> list[str]:
    """Every heading that does not sit over its own column's values.

    A left-set heading must start where every value starts - a character there
    and a blank before it - and a right-set one must end where every value ends.
    A heading shifted by any amount fails one of the two on a row whose value is
    shorter or longer than the heading.
    """
    misplaced: list[str] = []
    for column in columns:
        title = column.title
        start = header.find(f" {title} ") + 1
        if not title or start == 0:
            continue
        if column.align is Align.RIGHT:
            edge = start + len(title) - 1
            bad = [
                row for row in rows if row[edge : edge + 1] in ("", " ") or row[edge + 1 : edge + 2] not in ("", " ")
            ]
        else:
            bad = [row for row in rows if row[start : start + 1] in ("", " ") or row[start - 1 : start] != " "]
        if bad:
            misplaced.append(f"{title!r}: {header!r} over {bad[0]!r}")
    return misplaced


@pytest.mark.parametrize("capture", CAPTURES)
def test_every_slots_heading_sits_over_its_own_column(capture: str) -> None:
    """``port`` starts where the addresses start, ``slot`` where the socket numbers do, and so on."""
    inventory = _machine(capture)
    if not inventory.slots:
        pytest.skip(f"{capture} has no PCIe ports to tabulate")
    for width in ALIGNMENT_WIDTHS:
        header, rows = _slot_table(inventory, width)
        assert rows, f"{capture}: no slot rows, so nothing below asserts anything"
        checked = [column for column in report.SLOT_COLUMNS if f" {column.title} " in f" {header} "]
        assert checked, f"{capture} at {width}: no heading found in {header!r}"
        assert not _misplaced_headings(checked, f"{header} ", rows), _misplaced_headings(checked, f"{header} ", rows)


@pytest.mark.parametrize("capture", CAPTURES)
def test_every_smart_heading_sits_over_its_own_column(capture: str) -> None:
    """``id`` ends where the ids end, ``attribute`` starts where the names start, ``raw`` ends with the figures."""
    inventory = _machine(capture)
    for width in ALIGNMENT_WIDTHS:
        disks = _smart_tables(inventory, width)
        if not disks:
            pytest.skip(f"{capture} has no drive with an attribute table")
        for header, rows in disks:
            checked = [
                column for column in report.ATTRIBUTE_COLUMNS if column.title and f" {column.title} " in f" {header} "
            ]
            assert checked, f"{capture} at {width}: no heading found in {header!r}"
            misplaced = _misplaced_headings(checked, f"{header} ", rows)
            assert not misplaced, misplaced


#: Every width a reader could plausibly give the tool, from far below where the
#: undroppable columns fit to well past where nothing is dropped.
ONE_LINE_WIDTHS = range(20, 201)


def _overlong(lines: Sequence[str], width: int) -> list[str]:
    """The lines a console of this width would have to wrap."""
    return [line for line in lines if cell_len(line) > width]


@pytest.mark.parametrize("capture", CAPTURES)
def test_every_slots_row_stays_on_one_line_and_keeps_its_verdict(capture: str) -> None:
    """A port's row fits the console at every width, and the verdict it exists for survives.

    The rows were laid out by fitting columns alone, so below the width the
    undroppable ones need they ran past the console and wrapped, and the tail
    of a row read as a row of its own.
    """
    inventory = _machine(capture)
    defaults = register_default_links(inventory)
    verdicts = [
        report.slot_verdict(slot, register_default=slot.occupant_address in defaults)[0] for slot in inventory.slots
    ]
    for width in ONE_LINE_WIDTHS:
        header, rows = _slot_table(inventory, width)
        wrapped = _overlong([header, *rows], width)
        assert not wrapped, f"{capture} at {width}: a slots line is wider than the console: {wrapped}"
        for row, verdict in zip(rows, verdicts, strict=True):
            drawn = row.rstrip()
            kept = drawn.endswith(verdict) or any(
                drawn.endswith(verdict[:length] + ELLIPSIS) for length in range(1, len(verdict))
            )
            assert kept, f"{capture} at {width}: the verdict {verdict!r} is gone from {row!r}"


@pytest.mark.parametrize("capture", CAPTURES)
def test_every_smart_row_stays_on_one_line_and_keeps_its_attribute(capture: str) -> None:
    """An attribute's row fits the console at every width, and still says which attribute it is."""
    inventory = _machine(capture)
    attributes = [disk.health.attributes for disk in inventory.disks if disk.health and disk.health.attributes]
    for width in ONE_LINE_WIDTHS:
        tables = _smart_tables(inventory, width)
        assert len(tables) == len(attributes), f"{capture} at {width}: a drive's table went missing"
        for (header, rows), drive in zip(tables, attributes, strict=True):
            wrapped = _overlong([header, *rows], width)
            assert not wrapped, f"{capture} at {width}: a SMART line is wider than the console: {wrapped}"
            for row, attribute in zip(rows, drive, strict=True):
                named = str(attribute.id) in row.split() and (attribute.name or "-")[:2] in row
                assert named, f"{capture} at {width}: {row!r} no longer says it is attribute {attribute.id}"
