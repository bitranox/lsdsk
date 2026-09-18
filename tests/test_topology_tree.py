"""What the topology tree says about a link, including when it says nothing.

The tree's own law is the one the whole tool is built on: an unread end is never
a capable end. A reading that was not taken has to LOOK different from one that
was taken and was fine, because a reader who cannot tell them apart will assume
the second. A blank is the worst possible rendering of "not measured", since it
is also how a page renders a value nobody thought worth mentioning.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import report
from lsdsk.domain.diagnostics import diagnose

if TYPE_CHECKING:
    from rich.console import RenderableType

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# A real AHCI controller whose capture carries no link keys at all, which is
# what the chipset publishes on this board.
_UNREAD_CONTROLLER = "0000:00:1f.2"


def _load(host: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return payload


def _rendered(renderable: RenderableType, width: int = 200) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _line_for(address: str, host: str = "linux-sas-hba") -> str:
    machine = build_from(_load(host))
    text = _rendered(report.render_tree(machine, diagnose(machine)))
    return next(line for line in text.splitlines() if address in line)


@pytest.mark.os_agnostic
def test_a_storage_controller_that_published_no_link_says_so() -> None:
    """Verify a controller whose link was never read is not rendered as though it were fine.

    This controller's capture carries no link keys whatever, so the tree printed
    its address, name and driver and then stopped, saying nothing about its
    link. Beside a controller reading ``PCIe 3.0 x8`` that reads as a device
    with nothing to report rather than one nothing could be read from.
    """
    machine = build_from(_load("linux-sas-hba"))
    controller = next(item for item in machine.controllers if item.address == _UNREAD_CONTROLLER)
    assert not controller.link.capability_is_known, "the fixture no longer has a controller with an unread link"
    assert controller.link.current_speed_gtps is None

    line = _line_for(_UNREAD_CONTROLLER)

    assert "PCIe" in line, f"the line says nothing about its link: {line!r}"
    assert "not read" in line, f"an unread link must say so rather than go blank: {line!r}"


@pytest.mark.os_agnostic
def test_a_controller_whose_link_was_read_is_unchanged() -> None:
    """Verify the control: a measured link still reads as it did, with no caveat."""
    line = _line_for("0000:03:00.0")

    assert "PCIe Gen3x8" in line
    assert "not read" not in line, f"a measured link must carry no caveat: {line!r}"


# --------------------------------------------------------------------------
# The same law, in the fabric view: a hop nobody could read is not a device
# without a link
# --------------------------------------------------------------------------


def _fabric_lines(host: str, width: int = 200) -> list[str]:
    """Every line of the fabric at FULL density, whatever the shipped default.

    These tests are about what a row SAYS, so they need every device drawn;
    the shipped default keeps the least, which would decide which rows exist
    and turn a wording test into a density test.
    """
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.enums import TreeDensity

    machine = build_from(_load(host))
    section = render_fabric(machine, diagnose(machine), width, FabricView(density=TreeDensity.FULL))
    return _rendered(section, width=width).splitlines()


def _fabric_line_for(address: str, host: str) -> str:
    return next(line for line in _fabric_lines(host) if address in line)


@pytest.mark.os_agnostic
def test_a_bridge_on_a_platform_that_publishes_no_registers_reads_as_unread() -> None:
    """Windows publishes no link registers for a bridge, so the hop is unread.

    ``legacy PCI`` is a MEASUREMENT: this device has no PCIe capability at all.
    Windows never told us either way about a bridge, so printing it there
    asserts a reading nobody took, which is the same blank-implies-fine the
    tree's own law forbids, wearing a more confident word.
    """
    from lsdsk.adapters.render import theme
    from lsdsk.adapters.render.tree import hop_cells

    machine = build_from(_load("windows-ahci"))
    bridges = [node for node in machine.pci_tree if node.is_bridge]
    assert bridges, "the Windows fixture no longer carries a bridge"

    for bridge in bridges:
        capable, running = hop_cells(bridge)
        assert capable[0] == theme.NOT_READ, f"{bridge.address} claims a reading: {capable}"
        assert running[0] == theme.NOT_READ, f"{bridge.address} claims a reading: {running}"
        assert theme.LEGACY not in (capable[0], running[0]), "nobody measured that it has no capability"


@pytest.mark.os_agnostic
def test_a_linux_device_with_no_pcie_capability_still_reads_as_legacy() -> None:
    """The control: where the platform DOES publish absence, say absence.

    Linux lists a PCIe device's link in sysfs, so a device carrying no link
    keys there has no PCIe capability, which is a different fact from an
    unreadable one and keeps its own words.
    """
    machine = build_from(_load("linux-sas-hba"))
    legacy = next(
        node
        for node in machine.pci_tree
        if not node.is_root and node.pcie_capability_present is False and node.link.max_speed_gtps is None
    )

    from lsdsk.adapters.render import theme
    from lsdsk.adapters.render.tree import hop_cells

    capable, running = hop_cells(legacy)

    assert capable[0] == theme.LEGACY, f"a measured absence keeps its own word: {capable}"
    assert running[0] == theme.LEGACY, f"a measured absence keeps its own word: {running}"
    assert theme.NOT_READ not in (capable[0], running[0]), "nothing here was left unread"
    assert legacy.address in _fabric_line_for(legacy.address, "linux-sas-hba"), "the device is drawn at all"


@pytest.mark.os_agnostic
def test_a_half_read_register_never_reads_as_a_device_without_a_capability() -> None:
    """A speed read without its width is not a device with no capability.

    ``hop_cells`` decided by re-reading its own rendered dash, so a link whose
    speeds were read and whose widths were not rendered as ``legacy PCI`` in
    both columns - a reading taken, reported as hardware that cannot be read.
    """
    from lsdsk.adapters.render.tree import hop_cells
    from lsdsk.domain.models import PcieLink, PciNode

    half = PciNode(
        "0000:00:1c.0",
        "a bridge whose widths were not read",
        class_code=0x060400,
        link=PcieLink(max_speed_gtps=8.0, current_speed_gtps=8.0),
        pcie_capability_present=True,
    )

    from lsdsk.adapters.render import theme

    capable, running = hop_cells(half)

    assert theme.LEGACY not in (capable[0], running[0]), f"a read register is not an absent one: {capable} {running}"
    assert capable[0] == theme.NOT_READ
    assert running[0] == theme.NOT_READ


@pytest.mark.os_agnostic
def test_an_unread_hop_is_dimmed_like_every_other_unread_figure() -> None:
    """Colour carries the same meaning here as in every other table.

    The fabric row appended bare strings, so ``not read`` rendered at the same
    weight as the measured figure beside it while ``theme.hop_link_cells``,
    which styles it, went unused.
    """
    from rich.style import Style

    from lsdsk.adapters.render import theme
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.enums import TreeDensity

    machine = build_from(_load("windows-ahci"))
    section = render_fabric(machine, diagnose(machine), 200, FabricView(density=TreeDensity.FULL))
    console = Console(file=io.StringIO(), width=200, color_system="truecolor")

    unread = [segment for segment in console.render(section) if segment.text.strip() == theme.NOT_READ]

    assert unread, "the Windows capture no longer draws an unread hop"
    assert all(segment.style == Style.parse(theme.STYLE_UNKNOWN) for segment in unread), (
        f"an unread hop is not dimmed: {[segment.style for segment in unread][:3]}"
    )


@pytest.mark.os_agnostic
def test_a_section_that_draws_a_symbol_says_what_it_means() -> None:
    """A dash is quiet, and quiet is only honest if the page says what it means.

    The hop columns carry three different facts in one place - a measured link,
    a register nobody published, and a device with no PCIe capability - so the
    two that are not figures are symbols, and a symbol nobody explains is the
    reader guessing. The legend names exactly the ones the section drew.
    """
    from lsdsk.adapters.render import theme

    windows = "\n".join(_fabric_lines("windows-ahci"))
    linux = "\n".join(_fabric_lines("linux-sas-hba"))

    assert f"{theme.NOT_READ} = not read" in windows, windows[:300]
    assert f"{theme.LEGACY} = no PCIe capability" not in windows, "no legacy device is drawn on this capture"
    assert f"{theme.LEGACY} = no PCIe capability" in linux, linux[:300]


@pytest.mark.os_agnostic
def test_a_section_whose_hops_were_all_read_explains_nothing() -> None:
    """The control: no symbol drawn, no legend printed.

    Without this the legend could be an unconditional line and the test above
    would pass just the same, which is the shape of a guard that asserts the
    feature exists rather than that it fires.
    """
    from lsdsk.adapters.hw import fabric as fabric_module
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.enums import PciPortKind, TreeDensity
    from lsdsk.domain.models import Inventory, PcieLink

    measured = PcieLink(8.0, 4, 8.0, 4)
    tree = fabric_module.assemble(
        [
            fabric_module.NodeSource(
                address="0000:00:01.0",
                name="a port whose link was read",
                class_code=0x060400,
                vendor=None,
                driver=None,
                link=measured,
                port_kind=PciPortKind.ROOT,
                connector_present=None,
                physical_slot_number=None,
                parent=None,
                pcie_capability_present=True,
            ),
            fabric_module.NodeSource(
                address="0000:01:00.0",
                name="a controller whose link was read",
                class_code=0x010802,
                vendor=None,
                driver=None,
                link=measured,
                port_kind=PciPortKind.UNKNOWN,
                connector_present=None,
                physical_slot_number=None,
                parent="0000:00:01.0",
                pcie_capability_present=True,
            ),
        ]
    )
    machine = Inventory("example", pci_tree=tree)
    text = _rendered(render_fabric(machine, (), 160, FabricView(density=TreeDensity.FULL)), width=160)

    assert "0000:01:00.0" in text, "the fixture drew nothing to judge"
    assert "= not read" not in text, text
    assert "= no PCIe capability" not in text, text
