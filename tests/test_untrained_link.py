"""A link the kernel reports at width x0 is no link, and every view draws it one way.

The register was READ and said no lanes are trained: an empty root port, or a
function with no link of its own. Drawn from its halves it came out two ways in
one section - ``Gen1x0`` where the port published its reset speed beside the
zero width, as if a Gen1 link ran, and ``-`` where it published ``Unknown``,
which the legend defines as a register nobody read. Both were wrong, so both
now draw :data:`theme.NO_LINK` with a legend line, in the tree, in the tables'
link pair and in the detail panel alike.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.render import detail, theme
from lsdsk.adapters.render.tree import FabricView, fabric_lines
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import TreeDensity
from lsdsk.domain.models import PcieLink, PciNode

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# An empty root port on the linux-usb-ehci board: LnkSta 2.5 GT/s x0 beside a
# Gen3x4 capability, and the DMI port beside it publishing Unknown x0.
EMPTY_ROOT_PORT = "0000:00:01.0"
DMI_PORT = "0000:00:00.0"
# A figure spelled from a zero width, which is what must never be drawn. Matched
# as a figure, because a bare "x0" is also the start of every hex class code.
ZERO_WIDTH_FIGURE = re.compile(r"\bGen\d+x0\b")
EMPTY = PcieLink(current_speed_gtps=2.5, current_width=0, max_speed_gtps=8.0, max_width=4)


def _machine(host: str) -> Inventory:
    from lsdsk.adapters.hw.snapshot import build_from

    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return build_from(payload)


def _drawn(renderable: object, width: int = 160) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("speed", [2.5, None], ids=["reset-speed", "unknown-speed"])
def test_a_zero_width_is_no_link_whatever_speed_sits_beside_it(speed: float | None) -> None:
    assert theme.format_pcie_generation(speed, 0) == theme.NO_LINK


@pytest.mark.os_agnostic
def test_a_trained_link_still_draws_its_figure() -> None:
    """The control: the marker is about the zero width, not about Gen1."""
    assert theme.format_pcie_generation(2.5, 1) == "Gen1x1"


@pytest.mark.os_agnostic
def test_no_link_carries_no_bandwidth_and_no_unread_style_in_the_tree() -> None:
    running = theme.hop_link_cells(EMPTY, capability_present=True, bandwidth=True).running
    assert running == (theme.NO_LINK, "")


@pytest.mark.os_agnostic
def test_no_link_is_not_drawn_as_a_shortfall_in_the_tables() -> None:
    """An empty port is not a link running below its capability, so it is not amber."""
    assert theme.link_pair_cells(EMPTY, bandwidth=True).running == (theme.NO_LINK, "")


@pytest.mark.os_agnostic
def test_the_hop_legend_explains_no_link() -> None:
    assert theme.hop_legend([theme.NO_LINK]) == f"{theme.NO_LINK} = no link trained"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("address", [EMPTY_ROOT_PORT, DMI_PORT])
def test_both_untrained_ports_draw_one_way_in_the_tree(address: str) -> None:
    machine = _machine("linux-usb-ehci")
    lines = fabric_lines(machine, diagnose(machine), 200, FabricView(density=TreeDensity.FULL))
    row = next(
        line.text.plain for line in lines if isinstance(line.subject, PciNode) and line.subject.address == address
    )
    # The capable cell may carry its bandwidth in parentheses; the running one
    # follows it.
    assert re.search(rf"{address}\s+\S+(?: \([\d.]+ GB/s\))?\s+{theme.NO_LINK}\s", row), row
    text = "\n".join(line.text.plain for line in lines)
    assert not ZERO_WIDTH_FIGURE.search(text)
    assert f"{theme.NO_LINK} = no link trained" in text


@pytest.mark.os_agnostic
def test_the_detail_panel_draws_and_explains_no_link() -> None:
    machine = _machine("linux-usb-ehci")
    node = next(node for node in machine.pci_tree if node.address == EMPTY_ROOT_PORT)
    panel = _drawn(detail.render_detail(detail.node_detail(node), diagnose(machine)))
    assert not ZERO_WIDTH_FIGURE.search(panel), panel
    assert re.search(rf"running {theme.NO_LINK}\b", panel), panel
    assert detail.NO_LINK_LEGEND in panel, panel
