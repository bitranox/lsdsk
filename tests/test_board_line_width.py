"""The topology page's board line takes one physical line at every width.

The line names the board, its root complexes, the best root-port link and the
device count. Printed at a width it did not fit, it wrapped and stranded
``PCI devices`` on a line of its own, which reads as a second heading. Optional
fields are given up in priority order and the board name is cut last, marked.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.layout import ELLIPSIS
from lsdsk.adapters.render.tree import Fabric, FabricView, board_line
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))


def _board_at(host: str, width: int, *, board: str | None = None) -> tuple[list[str], str]:
    payload = cast("dict[str, Any]", json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8")))
    machine = build_from(payload)
    if board is not None:
        machine = machine.with_changes(board=board)
    fabric = Fabric.of(machine, width, FabricView(), thresholds=DEFAULT_THRESHOLDS)
    console = Console(file=io.StringIO(), width=width, no_color=True)
    with console.capture() as capture:
        console.print(board_line(machine, fabric))
    return capture.get().rstrip("\n").split("\n"), (machine.board or machine.hostname)


@pytest.mark.parametrize("host", CAPTURES)
def test_the_board_line_takes_one_line_at_every_width(host: str) -> None:
    for width in range(20, 201):
        drawn, _name = _board_at(host, width)
        assert len(drawn) == 1, f"{host} at width {width}: the board line took {len(drawn)} lines: {drawn}"


def test_the_board_line_keeps_every_field_where_the_width_allows() -> None:
    drawn, _name = _board_at("linux-nvme-board", 200)
    assert "root complex" in drawn[0]
    assert "root ports to PCIe" in drawn[0]
    assert drawn[0].rstrip().endswith("PCI devices")


def test_the_board_line_gives_up_fields_before_it_cuts_the_board_name() -> None:
    wide, name = _board_at("linux-nvme-board", 200)
    narrowed = next(
        drawn[0]
        for width in range(len(name) + len("   45 PCI devices"), 200)
        if "root ports to PCIe" not in (drawn := _board_at("linux-nvme-board", width)[0])[0]
    )
    assert name in narrowed, f"a field survived the name: {narrowed!r}"
    assert ELLIPSIS not in narrowed
    assert "PCI devices" in narrowed
    assert wide[0] != narrowed


def test_a_board_name_that_cannot_fit_is_cut_and_marked() -> None:
    long_name = "A" * 150
    for width in (20, 60, 100):
        drawn, _name = _board_at("linux-nvme-board", width, board=long_name)
        assert len(drawn) == 1, drawn
        assert drawn[0].startswith("A")
        assert ELLIPSIS in drawn[0], f"a cut name at width {width} was not marked: {drawn[0]!r}"
