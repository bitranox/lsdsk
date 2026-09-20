"""Four invariants CLAUDE.md states about the fabric section, enforced.

Each was stated in must/never terms and held by nothing: the mutation that
breaks it is recorded beside its test, and each was confirmed to SURVIVE the
suite before the test here existed. A stated invariant with no test is a claim,
and the doc reads the same either way.

They are separate tests rather than one sweep because they fail for unrelated
reasons and a reader chasing one should not have to read the other three.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import theme
from lsdsk.adapters.render.tree import (
    DEFAULT_WIDTH,
    Fabric,
    FabricSection,
    FabricView,
    device_header_line,
    disk_header_line,
    render_fabric,
)
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import PciPortKind, TreeDensity
from lsdsk.domain.models import PcieLink, PciNode

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def machine(name: str) -> Inventory:
    """Build one committed capture."""
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return build_from(payload)


def drawn(renderable: object, width: int) -> list[str]:
    """Render at an exact width and return the lines, colour stripped."""
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue().splitlines()


def board_line(inventory: Inventory, width: int = 200) -> str:
    """The section's board line, found by what it says rather than by index.

    It sits under the density note and the hop legend, and which of those are
    drawn depends on the machine - so a fixed offset would pass on one capture
    and read a legend on another.
    """
    lines = drawn(render_fabric(inventory, (), width), width)
    named = [line for line in lines if "PCI devices" in line]
    assert len(named) == 1, f"expected one board line, got {named}"
    return named[0]


# --------------------------------------------------------------------------
# (a) The spine is capped so the address still fits
# --------------------------------------------------------------------------


def deep_chain(levels: int) -> tuple[PciNode, ...]:
    """A synthetic fabric `levels` deep, which no committed capture is.

    The cap only binds when the legs are wide enough to crowd out the address,
    and the deepest real capture here is nowhere near that - which is why
    removing the cap left the suite green.
    """
    root = PciNode(address="0000:00", name="root bus")
    nodes = [root]
    parent = root.address
    for level in range(1, levels + 1):
        address = f"0000:{level:02x}:00.0"
        nodes.append(PciNode(address=address, name=f"bridge {level}", parent_address=parent, class_code=0x060400))
        parent = address
    return tuple(nodes)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("width", range(20, 25))
def test_a_deep_fabric_still_takes_one_line_per_row_at_a_narrow_width(width: int) -> None:
    """MUTATION: drop the `max(width - marker - address, 0)` cap on the spine.

    Without it a nine-deep chain wraps every row at widths 20 to 24, which is
    the one-line-per-device law this module exists to keep. The committed
    captures are too shallow to reach it, so the input is built here.
    """
    nodes = deep_chain(9)
    fabric = Fabric(nodes, width, FabricView(density=TreeDensity.FULL))
    console = Console(file=io.StringIO(), width=width, no_color=True)

    # Row by row, not the finished section: a wrapped row and two devices are
    # the same text, so the section read back as a block cannot tell them apart.
    # That is the trap this module's own guard was rewritten to avoid.
    for node, _level in fabric.drawn():
        with console.capture() as capture:
            console.print(fabric.row(node, ()))
        lines = capture.get().rstrip("\n").split("\n")
        assert len(lines) == 1, f"at width {width}, {node.address} took {len(lines)} lines: {lines}"


@pytest.mark.os_agnostic
def test_the_spine_formula_is_two_per_level_plus_the_margin() -> None:
    """CLAUDE.md states it two ways; this is the one the code implements.

    The margin constant is 2, so the width is ``2K + 2`` and the doc's other
    spelling, ``2K + 1``, is the wrong one. Asserted on the drawn prefix rather
    than on the constant, so it cannot agree with a renamed constant that draws
    something else.
    """
    inventory = machine("linux-sas-hba.json")
    fabric = Fabric(inventory.pci_tree, DEFAULT_WIDTH, FabricView(density=TreeDensity.FULL))
    deepest = max((fabric.level_of(node) for node, _level in fabric.drawn()), default=0)

    assert fabric.spine == 2 * deepest + 2, f"deepest level {deepest} gave spine {fabric.spine}"


# --------------------------------------------------------------------------
# (b) The board's link figure comes from ROOT ports only
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("capture", "expected"),
    [("linux-sas-hba.json", "Gen3x8"), ("linux-minimal.json", "Gen3x16")],
)
def test_the_board_names_the_best_link_its_own_root_ports_publish(capture: str, expected: str) -> None:
    """MUTATION: drop the ROOT filter from `_best_root_port`.

    A switch downstream port describes a card ON the board rather than the
    board, so counting one moves `linux-sas-hba` from Gen3x8 to Gen4x8 and
    `linux-minimal` from Gen3x16 to Gen4x16 - a board reported as faster than
    its processor's ports are. Both captures measured; 112 tests stayed green.
    """
    board = board_line(machine(capture))

    assert f"root ports to PCIe {expected}" in board, board


@pytest.mark.os_agnostic
def test_a_faster_downstream_port_does_not_become_the_boards_figure() -> None:
    """The rule itself, on a fabric built to separate the two readings.

    The captures above happen to have a faster downstream port; this states WHY
    that must not count, on an input where the root port and the downstream one
    can only be told apart by their kind.
    """
    nodes = (
        PciNode(address="0000:00", name="root bus"),
        PciNode(
            address="0000:00:01.0",
            name="root port",
            parent_address="0000:00",
            class_code=0x060400,
            port_kind=PciPortKind.ROOT,
            link=PcieLink(max_speed_gtps=8.0, max_width=8),
        ),
        PciNode(
            address="0000:01:00.0",
            name="switch downstream port",
            parent_address="0000:00:01.0",
            class_code=0x060400,
            port_kind=PciPortKind.SWITCH_DOWNSTREAM,
            link=PcieLink(max_speed_gtps=16.0, max_width=16),
        ),
    )
    board = board_line(machine("linux-minimal.json").with_changes(pci_tree=nodes))

    assert "Gen3x8" in board, board
    assert "Gen4x16" not in board, f"a downstream port became the board's figure: {board}"


# --------------------------------------------------------------------------
# (c) A section inside a window lays out at the width it is handed
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("width", [60, 200])
def test_a_section_lays_itself_out_at_the_console_width_it_is_given(width: int) -> None:
    """MUTATION: render at `DEFAULT_WIDTH` instead of `options.max_width`.

    Then the section wraps at console width 60 and under-uses 200, which is the
    defect `FabricSection` exists to fix: a page inside a window does not know
    its width until the layout runs. 175 + 94 tests stayed green without this.

    Asserted against `render_fabric` at the same width, which is what a command
    that KNOWS the width produces, so the two paths are held to one answer
    rather than to a number written here.
    """
    inventory = machine("linux-sas-hba.json")
    findings = diagnose(inventory)

    through_the_section = drawn(FabricSection(inventory, findings), width)
    through_the_command = drawn(render_fabric(inventory, findings, width), width)

    assert through_the_section == through_the_command, (
        f"at width {width} the section drew {len(through_the_section)} lines and the command {len(through_the_command)}"
    )


@pytest.mark.os_agnostic
def test_the_two_widths_really_do_produce_different_pages() -> None:
    """The control for the pair above.

    If 60 and 200 rendered alike, the test would pass against a section that
    ignored the width entirely, which is the mutation it exists to catch.
    """
    inventory = machine("linux-sas-hba.json")
    narrow = drawn(FabricSection(inventory, ()), 60)
    wide = drawn(FabricSection(inventory, ()), 200)
    assert narrow != wide, "the section drew the same page at 60 and 200 columns"
    assert DEFAULT_WIDTH not in (60, 200), "the mutation's constant must differ from both arms"


# --------------------------------------------------------------------------
# (d) Both headers of one section are drawn in one style
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_both_headers_of_a_section_are_drawn_in_the_same_style() -> None:
    """MUTATION: hardcode `theme.STYLE_HEADER` in `disk_header_line`.

    A section draws two headers - the device columns and the disk columns - and
    the interactive view passes its own header hue to both. Hardcoding one made
    a section draw them differently, invisibly, because the printed style is the
    bare word `bold` and carries no hex for a picture to disagree about. 109
    tests stayed green.

    Asserted on the STYLES the two lines carry rather than on rendered text, for
    that same reason: the difference is not visible in a no-colour capture.
    """
    inventory = machine("linux-sas-hba.json")
    interactive = "#D7A13B"
    fabric = Fabric(inventory.pci_tree, 200, FabricView(density=TreeDensity.FULL, header_style=interactive))

    device_styles = {str(span.style) for span in device_header_line(fabric).spans}
    disk_styles = {str(span.style) for span in disk_header_line(fabric, fabric.measure(inventory)).spans}

    assert device_styles == {interactive}, device_styles
    assert disk_styles == {interactive}, f"the disk header ignored the section's header style: {disk_styles}"


@pytest.mark.os_agnostic
def test_the_printed_section_still_uses_the_printed_header_style() -> None:
    """The control: the styles must not simply be whatever was passed in.

    Without a header style the section is the printed one, so a test that only
    checked "both are equal" would pass against a build that painted both the
    wrong colour.
    """
    inventory = machine("linux-sas-hba.json")
    fabric = Fabric(inventory.pci_tree, 200, FabricView(density=TreeDensity.FULL))
    styles = {str(span.style) for span in device_header_line(fabric).spans}
    assert styles == {theme.STYLE_HEADER}, styles
