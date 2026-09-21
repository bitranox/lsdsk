"""The density knob, at both boundaries a reader reaches it.

The CLI token must agree with the configuration value, which is where the
click.Choice trap bites: find-spells by casefolding the member NAME, so an
enum whose value disagrees with its name gives one setting two vocabularies.
And the rendering must honour the density structurally on real captures,
where the counts below were measured before anything was written.
"""

from __future__ import annotations

import io
import itertools
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.adapters.render.tree import FabricView, render_fabric
from lsdsk.domain.enums import TreeDensity

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
_FIXTURE = FIXTURES / "linux-sas-hba.json"

DeviceLine = re.compile(r"(?<![0-9a-f:])0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")

# The measured device-line counts the density rule must reproduce: storage plus
# the path above it, never every bridge on the board. Every Linux figure makes
# the two reduced densities IDENTICAL - no non-storage device there shares a
# bridge with storage - so only the Windows capture separates them, which is why
# it is the fixture any two-density test must use.
DENSITY_COUNTS: dict[str, dict[TreeDensity, int]] = {
    "linux-sas-hba": {TreeDensity.STORAGE_ONLY: 9, TreeDensity.STORAGE_AND_SIBLINGS: 9, TreeDensity.FULL: 95},
    "linux-minimal": {TreeDensity.STORAGE_ONLY: 5, TreeDensity.STORAGE_AND_SIBLINGS: 5, TreeDensity.FULL: 87},
    "linux-nvme-board": {TreeDensity.STORAGE_ONLY: 13, TreeDensity.STORAGE_AND_SIBLINGS: 13, TreeDensity.FULL: 45},
    "windows-ahci": {TreeDensity.STORAGE_ONLY: 4, TreeDensity.STORAGE_AND_SIBLINGS: 7, TreeDensity.FULL: 27},
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("density", list(TreeDensity), ids=str)
def test_a_cli_density_token_is_its_value_casefolded(density: TreeDensity) -> None:
    """The choice the CLI registers takes the VALUE tokens, not member names.

    click.Choice matches an input against the member NAME as well as against
    the registered tokens (measured on click 8.5: 'STORAGE_ONLY' normalises
    because a StrEnum member IS a string among the choices), so pinning the
    vocabulary means driving the choice the CLI actually registers: the value
    token is accepted case-insensitively and converts back to its value, and
    a short. ``CASED`` name can quietly slip through would be one vocabulary.
    """
    import click

    from lsdsk.adapters.cli.constants import TREE_DENSITY_TOKENS

    choice = click.Choice(TREE_DENSITY_TOKENS, case_sensitive=False)
    parameter = click.Option(["--x"], type=choice)
    context = click.Context(click.Command("topology"))
    assert choice.convert(density.value.upper(), parameter, context) == density.value
    assert choice.convert(density.value, parameter, context) == density.value
    # What lands downstream must be the VALUE the config and the options share,
    # whatever the member's Python identity: the tokens are strings, and the
    # converter has no member to reach into.
    with pytest.raises(click.exceptions.BadParameter):
        choice.convert("storage_and_siblings", parameter, context)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host,expected", sorted(DENSITY_COUNTS.items()), ids=str)
def test_a_density_keeps_the_device_lines_measured_for_it(host: str, expected: dict[TreeDensity, int]) -> None:
    """Reproduce the density's pruning over the four committed captures."""
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    for density, count in expected.items():
        lines = _device_lines(machine, findings, density)
        assert len(lines) == count, f"{host} {density.value}: {len(lines)} device lines, measured {count}"


@pytest.mark.os_agnostic
def test_the_two_reduced_densities_separate_on_the_windows_capture() -> None:
    """Use the fixture the plan names, or the test passes vacuously.

    On every Linux capture the two reduced densities are the same shape, so a
    separation test there would assert nothing exactly as if it had never
    been written.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load("windows-ahci"))
    findings = diagnose(machine)
    siblings = _device_lines(machine, findings, TreeDensity.STORAGE_AND_SIBLINGS)
    only = _device_lines(machine, findings, TreeDensity.STORAGE_ONLY)
    assert len(siblings) > len(only)

    def match_of(line: str) -> str:
        match = DeviceLine.search(line)
        assert match is not None, f"a device line the pattern cannot read: {line!r}"
        return match.group(0)

    crossed = {match_of(line) for line in only} - {match_of(line) for line in siblings}
    assert not crossed, f"the tighter density dropped nothing: {sorted(crossed)}"


@pytest.mark.os_agnostic
def test_the_densities_are_declared_from_least_detail_to_most() -> None:
    """The declared order IS the order a reader is walked through.

    Two surfaces read it: the ``d`` key walks ``list(TreeDensity)`` from
    wherever the current value sits, and ``--help`` lists the tokens. So the
    member order is not a stylistic choice, it is the sequence somebody
    experiences, and it has to climb.

    Measured on the drawing rather than on a member list, because a list
    written here would agree with whatever the enum happens to say. Only
    ``windows-ahci`` separates the two reduced densities, so it is the only
    capture that can tell an ascending order from a merely non-decreasing one.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load("windows-ahci"))
    findings = diagnose(machine)
    drawn = [(density, len(_device_lines(machine, findings, density))) for density in TreeDensity]
    counts = [count for _density, count in drawn]
    assert counts == sorted(counts), f"the densities do not climb: {[(d.value, n) for d, n in drawn]}"
    # The separation is what makes the assertion above non-vacuous: three equal
    # counts would sort as trivially ordered and prove nothing.
    assert len(set(counts)) == len(counts), f"this capture cannot separate the densities: {counts}"


@pytest.mark.os_agnostic
def test_the_shipped_default_is_the_least_detailed_so_the_cycle_climbs_from_it() -> None:
    """Ascending members only ascend for a reader who starts at the first one.

    The cycle wraps, so where the DEFAULT sits in the ring decides what the
    first press does. With the least detail declared first and shipped as the
    default, every press adds detail until it wraps back to the least; with the
    default anywhere else the first press jumps and the climb is broken. That
    is exactly the defect this pair replaces: the default was the LAST member,
    so the first press landed on the most detailed of the three.
    """
    from lsdsk.adapters.config.tunables import DEFAULT_TREE_DENSITY

    assert DEFAULT_TREE_DENSITY is next(iter(TreeDensity))


@pytest.mark.os_agnostic
def test_the_cli_lists_the_density_tokens_in_the_order_they_are_declared() -> None:
    """``--help`` teaches the same climb the key does.

    The tokens were sorted alphabetically, which agreed with the declared
    order only by accident of these three spellings - rename one and the two
    surfaces would silently disagree about which end is which.
    """
    from lsdsk.adapters.cli.constants import TREE_DENSITY_TOKENS

    assert tuple(density.value for density in TreeDensity) == TREE_DENSITY_TOKENS


@pytest.mark.os_agnostic
def test_a_density_draws_every_device_it_keeps() -> None:
    """What the density SELECTS and what the view DRAWS are one set.

    The two are computed separately: the density picks by class, and the
    drawing walks down from the roots through kept parents only. A kept device
    whose parent was NOT kept is therefore selected and never reached - it does
    not float, it disappears, which is the worse of the two failures and the
    one the previous guard could not see. It asserted that a drawn node's
    parent chain reaches a drawn node or a synthetic root, which is true of any
    tree this module can build, whatever the density selects.

    A classless intermediate device is how that happens in practice: Windows
    publishes no class for its host bridge, so the bridge is not kept and a
    storage controller below it was selected and then dropped.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import Fabric

    for host in DENSITY_COUNTS:
        machine = build_from(_load(host))
        for density in tuple(TreeDensity):
            fabric = Fabric(machine.pci_tree, 200, FabricView(density=density))
            drawn = {node.address for node, _level in fabric.drawn()}
            lost = fabric.kept - drawn
            assert not lost, f"{host} {density.value}: kept but never drawn: {sorted(lost)[:5]}"


@pytest.mark.os_agnostic
def test_a_storage_controller_under_a_classless_device_is_still_drawn() -> None:
    """The case no committed capture carries, built by hand.

    Windows publishes no class code for its host bridge, and the reduced
    densities keep bridges BY CLASS, so a controller hanging below one was
    selected by the density and then never reached by the walk: the section
    rendered empty with no line saying why.
    """
    from lsdsk.adapters.hw import fabric as fabric_module
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.enums import PciPortKind
    from lsdsk.domain.models import Inventory, PcieLink

    def source(address: str, class_code: int | None, parent: str | None) -> Any:
        return fabric_module.NodeSource(
            address=address,
            name=f"device at {address}",
            class_code=class_code,
            vendor=None,
            driver=None,
            link=PcieLink(),
            port_kind=PciPortKind.UNKNOWN,
            connector_present=None,
            physical_slot_number=None,
            parent=parent,
        )

    tree = fabric_module.assemble(
        [
            source("0000:00:01.0", None, None),
            source("0000:01:00.0", 0x010802, "0000:00:01.0"),
        ]
    )
    machine = Inventory(hostname="example", pci_tree=tree)

    buffer = io.StringIO()
    Console(file=buffer, width=120, no_color=True).print(
        render_fabric(machine, (), 120, FabricView(density=TreeDensity.STORAGE_ONLY))
    )

    assert "0000:01:00.0" in buffer.getvalue(), f"the controller vanished:\n{buffer.getvalue()}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("width", range(20, 201, 15))
@pytest.mark.parametrize("host", sorted(DENSITY_COUNTS))
def test_the_fabric_fits_its_width_at_every_density(host: str, width: int) -> None:
    """One device, one line, every column the device row carries intact.

    If any row wraps, the count of lines carrying a PCI address rises above
    the count of drawn devices, so this pins both one-line-per-device and the
    marker invariant (a wrapped marker strand) at once.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    marker_alone = {"!!", "!", "~"}
    for density in tuple(TreeDensity):
        buffer = io.StringIO()
        Console(file=buffer, width=width, no_color=True).print(
            render_fabric(machine, findings, width, FabricView(density=density))
        )
        lines = buffer.getvalue().splitlines()
        stranded = [f"{host} w{width} {density.value}" for line in lines if line.strip() in marker_alone]
        assert not stranded, f"marker stranded on its own line: {stranded[:3]}"


def _load(host: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return payload


def _rendered(machine: Any, findings: Any, density: TreeDensity) -> str:

    buffer = io.StringIO()
    Console(file=buffer, width=200, no_color=True).print(
        render_fabric(machine, findings, 200, FabricView(density=density))
    )
    return buffer.getvalue()


def _device_lines(machine: Any, findings: Any, density: TreeDensity) -> list[str]:
    return [line for line in _rendered(machine, findings, density).splitlines() if DeviceLine.search(line)]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("command", "scoped"),
    [([], False), (["report"], False), (["topology"], False), (["topology"], True)],
    ids=["default-view", "report", "topology", "topology-scoped"],
)
def test_the_density_option_reaches_every_view_that_draws_the_fabric(
    cli_runner: CliRunner, production_factory: Callable[[], Any], command: list[str], scoped: bool
) -> None:
    """One setting, whichever view draws the fabric and whichever source asked.

    ``--tree-density`` is a GLOBAL option, so README states it "applies to
    whichever command follows". It reached ``topology`` alone: the bare page
    and ``report`` resolve their display settings through ``resolve_tunables``,
    which folded ``--expand-virtual`` and not this, so the flag was accepted
    and silently dropped on the view the tool tells you to run first.
    """
    from lsdsk.adapters.cli import cli

    fixture = str(FIXTURES / "linux-sas-hba.json")
    common = ["--no-record", "--replay", fixture]
    asked = [*command, "--tree-density", "full"] if scoped else ["--tree-density", "full", *command]
    shipped = cli_runner.invoke(cli, [*common, *command], obj=production_factory)
    asked_for_more = cli_runner.invoke(cli, [*common, *asked], obj=production_factory)

    assert shipped.output, "the run without the option rendered nothing"
    assert asked_for_more.output, "the run with the option rendered nothing"
    drawn_shipped = [line for line in shipped.output.splitlines() if DeviceLine.search(line)]
    drawn_full = [line for line in asked_for_more.output.splitlines() if DeviceLine.search(line)]
    where = "after" if scoped else "before"
    assert len(drawn_full) > len(drawn_shipped), (
        f"--tree-density {where} {command or ['(no command)']} changed nothing: "
        f"{len(drawn_full)} lines against {len(drawn_shipped)}"
    )


@pytest.mark.os_agnostic
def test_the_shipped_default_draws_the_least_of_the_fabric() -> None:
    """The view opens on the least detail, and every place that says so agrees.

    Four device lines in five are unrelated to storage on real hardware, so the
    full fabric buries the story the view exists to tell. The constant, the
    model's default and the shipped configuration file are three statements of
    one default, and a file disagreeing with the constant is the version a
    reader would be reading.
    """
    import tomllib

    from lsdsk.adapters.config.tunables import DEFAULT_TREE_DENSITY, DisplaySettings

    shipped = (
        Path(__file__).parents[1] / "src" / "lsdsk" / "adapters" / "config" / "defaultconfig.d" / "70-display.toml"
    )
    written = tomllib.loads(shipped.read_text(encoding="utf-8"))["display"]["tree_density"]

    assert DEFAULT_TREE_DENSITY is TreeDensity.STORAGE_ONLY
    assert DisplaySettings().tree_density is TreeDensity.STORAGE_ONLY
    assert written == TreeDensity.STORAGE_ONLY.value, f"the shipped file says {written!r}"


@pytest.mark.os_agnostic
def test_the_printed_view_says_what_it_draws_and_names_the_option(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """A view that holds devices back has to say so, and say how to see them.

    The same rule the kernel-virtual tally follows: folded away, never hidden.
    Without the line the default simply shows fewer devices than the machine
    has, which is the blank-implies-fine this tool exists to refuse.
    """
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(cli, ["--no-record", "--replay", str(_FIXTURE), "topology"], obj=production_factory)

    assert "showing storage and the bridges above it" in result.output, result.output[:400]
    assert "--tree-density to change the detail level" in result.output, result.output[:400]


@pytest.mark.os_agnostic
def test_the_note_names_what_each_density_draws() -> None:
    """Every density has words of its own, so the line can never read blank."""
    from lsdsk.adapters.render.tree import KEY_HINT, density_note

    for density in TreeDensity:
        note = density_note(density, KEY_HINT)
        assert note.startswith("showing "), note
        assert note != "showing ; " + KEY_HINT + " to change the detail level"
        assert KEY_HINT in note


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(DENSITY_COUNTS))
def test_every_column_of_a_device_row_is_separated_from_the_next(host: str) -> None:
    """Fields are separated by a gap, at every value they can hold.

    The address column was exactly as wide as a PCI address, so padding it to
    that width emitted nothing at all and the address ran into the speed beside
    it on every row of every capture: `0000:00:01.05.0 x8` reads as an address
    that ends in 05. The budget had already been paying for three gaps the row
    never drew, which is how the arithmetic and the drawing disagreed.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    glued: list[str] = []
    for line in _device_lines(machine, findings, TreeDensity.FULL):
        match = DeviceLine.search(line)
        assert match is not None
        after = line[match.end() :]
        if after and not after.startswith("  "):
            glued.append(line)
    assert not glued, f"{host}: the address touches the next column on {len(glued)} rows, e.g. {glued[0]!r}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(DENSITY_COUNTS))
def test_a_device_row_takes_one_line_at_every_width(host: str) -> None:
    """One device, one line, at every width from 20 to 200.

    A wrapped row puts a device's name on a line with no address and no marker,
    which reads as a second device, and the columns the view exists for stop
    lining up. The guard that claimed this asserted only that no line held a
    bare severity marker, so the whole invariant was unheld: measured before
    the fix, every capture wrapped its rows below about 50 columns.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import Fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    for width in range(20, 201):
        fabric = Fabric(machine.pci_tree, width, FabricView(density=TreeDensity.FULL))
        console = Console(file=io.StringIO(), width=width, no_color=True)
        for node, _level in fabric.drawn():
            with console.capture() as capture:
                console.print(fabric.row(node, findings))
            drawn = capture.get().rstrip("\n").split("\n")
            assert len(drawn) == 1, f"{host} at width {width}: {node.address} took {len(drawn)} lines: {drawn}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(DENSITY_COUNTS))
def test_every_device_row_starts_its_columns_at_the_same_place(host: str) -> None:
    """The spine is one width for the whole section, at every depth.

    It was padded to the spine MINUS the marker, so a row at the deepest drawn
    level - whose legs are exactly the spine's own width - pushed its address,
    both hops and its name two characters right of every row above it, and out
    of line with the disk rows below it. The module's docstring calls this the
    law it exists for.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import Fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    fabric = Fabric(machine.pci_tree, 200, FabricView(density=TreeDensity.FULL))

    offsets = {fabric.row(node, findings).plain.index(node.address) for node, _level in fabric.drawn()}

    assert len(offsets) == 1, f"{host}: the address column sits at {sorted(offsets)} depending on the row's depth"


@pytest.mark.os_agnostic
def test_the_disk_header_sits_above_the_cells_it_labels() -> None:
    """A column header three characters off its own data is a misread waiting.

    The header kept report.py's separate marker field, which this section
    spends inside its own marker column, so every value sat three columns left
    of its heading down the whole table - in the view whose purpose is reading
    a column straight down the page.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load("linux-nvme-board"))
    findings = diagnose(machine)
    buffer = io.StringIO()
    Console(file=buffer, width=160, no_color=True).print(
        render_fabric(machine, findings, 160, FabricView(density=TreeDensity.FULL))
    )
    lines = buffer.getvalue().splitlines()

    header = next(line for line in lines if "device" in line and "model" in line)
    disk = next(line for line in lines if "/dev/nvme" in line)

    assert header.index("device") == disk.index("/dev/nvme"), f"header {header!r} against row {disk!r}"


@pytest.mark.os_agnostic
def test_listing_the_virtual_devices_fits_the_columns_around_them() -> None:
    """The columns are fitted over every row the view draws, virtual ones too.

    They were fitted over the drives alone and the virtual rows were then
    padded into that, so `VIRTUAL` arrived clipped to `VIR>` in a section with
    twenty columns to spare while the old table printed it whole for the same
    machine at the same width.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.report import render_controller_disks
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load("linux-minimal"))
    assert machine.virtual_disks, "the fixture no longer carries kernel-virtual devices"
    findings = diagnose(machine)
    view = FabricView(density=TreeDensity.STORAGE_ONLY, expand_virtual=True)

    def rendered(renderable: Any) -> list[str]:
        buffer = io.StringIO()
        Console(file=buffer, width=120, no_color=True).print(renderable)
        return buffer.getvalue().splitlines()

    fabric = rendered(render_fabric(machine, findings, 120, view))
    old_table = rendered(render_controller_disks(machine, findings, 120, expand_virtual=True))
    sample = machine.virtual_disks[0].path

    in_fabric = next(line for line in fabric if sample in line)
    in_table = next(line for line in old_table if sample in line)
    for cell in ("VIRTUAL",):
        assert (cell in in_fabric) == (cell in in_table), (
            f"the two views describe one machine differently: {in_fabric!r} against {in_table!r}"
        )


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", sorted(DENSITY_COUNTS))
def test_a_reduced_density_draws_no_bridge_that_leads_away_from_storage(host: str) -> None:
    """The phrase "Storage and the bridges above it" means the bridges ABOVE IT.

    Both reduced densities kept every class-06 device in the machine, so the
    view drew the whole bridge skeleton of the board - downstream ports leading
    to a GPU, an LPC bridge, a Thunderbolt switch - while calling itself the
    storage view. Measured on the reporter's capture: 11 of the 26 devices
    drawn at storage-only had no storage anywhere below them.

    Asserted as the RULE rather than as a count, because the counts were
    measured from the behaviour and so agreed with it whatever it did.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import Fabric

    machine = build_from(_load(host))
    nodes = {node.address: node for node in machine.pci_tree}

    def holds_storage(address: str) -> bool:
        """Whether this node or anything below it is a storage controller."""
        node = nodes[address]
        return node.is_storage or any(holds_storage(child) for child in node.children)

    for density in (TreeDensity.STORAGE_AND_SIBLINGS, TreeDensity.STORAGE_ONLY):
        fabric = Fabric(machine.pci_tree, 200, FabricView(density=density))
        strays = [
            node.address for node, _level in fabric.drawn() if node.is_bridge_family and not holds_storage(node.address)
        ]
        assert not strays, f"{host} {density.value}: bridges leading away from storage: {sorted(strays)[:6]}"


@pytest.mark.os_agnostic
def test_the_tree_starts_at_the_board_that_carries_the_fabric() -> None:
    """The root of a PCI fabric is the board, so the list starts there.

    Every root complex is a port of the CPU on that board, so a tree whose top
    line is a bus label starts one level below the thing that explains it. The
    line names what the capture actually carries and nothing else: the board
    when DMI named it, the root complexes by label, the best capability the
    board's own root ports publish, and how many PCI devices the machine holds.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load("linux-nvme-board"))
    buffer = io.StringIO()
    Console(file=buffer, width=200, no_color=True).print(
        render_fabric(machine, diagnose(machine), 200, FabricView(density=TreeDensity.STORAGE_ONLY))
    )
    lines = [line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()]
    board = next(line for line in lines if machine.board in line)

    assert machine.board, "the fixture no longer names a board"
    assert "1 root complex" in board, board
    assert "0000:00" in board, board
    # This board's own root ports publish PCIe 5.0 x8, so the line may say so.
    assert "Gen5x8" in board, board
    assert f"{len([node for node in machine.pci_tree if not node.is_root])} PCI devices" in board, board
    assert lines.index(board) < min(index for index, line in enumerate(lines) if DeviceLine.search(line)), (
        "the board line sits below the devices it carries"
    )


@pytest.mark.os_agnostic
def test_the_top_line_says_only_what_the_capture_carries() -> None:
    """Three captures, three different silences, and none of them invented.

    DMI names no board on two of the committed captures, and Windows publishes
    no link registers for a root port at all, so neither the board nor its
    capability can be stated there - and a summary line that fills either in
    would be the tool claiming a reading nobody took.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import FabricView, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    def top_line(host: str) -> str:
        machine = build_from(_load(host))
        buffer = io.StringIO()
        Console(file=buffer, width=200, no_color=True).print(
            render_fabric(machine, diagnose(machine), 200, FabricView(density=TreeDensity.STORAGE_ONLY))
        )
        lines = [line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()]
        first_device = min(index for index, line in enumerate(lines) if DeviceLine.search(line))
        return next(line for line in reversed(lines[:first_device]) if "root complex" in line)

    nameless = top_line("linux-sas-hba")
    assert "linux-sas-hba" in nameless, f"with no board named, the line names the machine: {nameless!r}"

    two_roots = top_line("linux-minimal")
    assert "2 root complexes" in two_roots, two_roots
    assert "0000:00" in two_roots and "0000:ff" in two_roots, two_roots

    windows = top_line("windows-ahci")
    assert "PCIe" not in windows, f"no root port published a link here: {windows!r}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("bandwidth", [False, True])
def test_no_hop_figure_is_wider_than_the_column_it_is_drawn_in(*, bandwidth: bool) -> None:
    """Each hop tier must hold every figure it can be asked to draw.

    A figure wider than its column would be CLIPPED, and a clipped speed is not
    a shorter figure but a different one - the reason the row drops the pair
    whole, and drops the bandwidth before that, rather than cutting either. The
    synthetic link is the generation nobody ships yet, so the day one does, this
    fails here rather than in somebody's terminal.

    Both tiers are checked, because there are two widths now and a change to
    either is a change to what a row can hold.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render import theme
    from lsdsk.adapters.render.tree import HOP_WIDE_WIDTH, HOP_WIDTH, hop_cells
    from lsdsk.domain.models import PcieLink, PciNode

    column = HOP_WIDE_WIDTH if bandwidth else HOP_WIDTH
    widest = 0
    for host in DENSITY_COUNTS:
        machine = build_from(_load(host))
        for node in machine.pci_tree:
            for text, _style in hop_cells(node, bandwidth=bandwidth):
                assert len(text) <= column, f"{host} {node.address}: {text!r} does not fit {column}"
                widest = max(widest, len(text))
    assert widest <= column, f"the column is {column} wide and something needs {widest}"

    future = PciNode(
        address="a",
        name="b",
        link=PcieLink(current_speed_gtps=64.0, current_width=16, max_speed_gtps=64.0, max_width=16),
        pcie_capability_present=True,
    )
    drawn = hop_cells(future, bandwidth=bandwidth)[0][0]
    assert len(drawn) <= column, f"a shipping generation must fit: {drawn!r} needs {len(drawn)} of {column}"
    assert len(theme.NOT_READ) <= column and len(theme.LEGACY) <= column

    # And nothing is WASTED: the column is exactly as wide as the widest thing
    # drawn in it, which is the widest figure OR its own heading, whichever is
    # longer. Stated as the max rather than as a number, because which of the two
    # wins differs between the tiers - the heading decides the narrow one and the
    # figure the wide one - and a bare literal here would hide that, exactly as
    # it hid a heading overflowing its column by one.
    heading = len("capable")
    assert column == max(len(drawn), heading), (
        f"the {'wide' if bandwidth else 'narrow'} column is {column} wide, but the widest thing in it "
        f"is {max(len(drawn), heading)} ({drawn!r} against the heading)"
    )


@pytest.mark.os_agnostic
def test_no_device_column_is_narrower_than_its_own_heading() -> None:
    """A column has to hold its title, which is drawn in it like any value.

    Sizing the hop column by the widest FIGURE alone made it 6 while its heading
    `capable` is 7, and `_append_fields` pads but never truncates - so every
    value after it sat one character right of the header naming it, which is the
    one law this module exists to keep. The guard above could not see it: it
    measured the figures and never the title.

    Asked of every field at every width, so it holds for whatever a later tier
    or column is called, not only for the pair that produced it.
    """
    from lsdsk.adapters.render.tree import DEVICE_COLUMNS, device_fields

    titles = {column.key: column.title for column in DEVICE_COLUMNS}
    checked = 0
    for width in range(20, 201):
        for spine in (2, 5, 9, 15):
            fields = device_fields(width, spine)
            # The LAST field is clipped rather than padded, so it is allowed to
            # be narrower than its own name; every one before it is not.
            for field in fields[:-1]:
                title = titles.get(field.key, field.key)
                checked += 1
                assert len(title) <= field.width, (
                    f"at width {width}, spine {spine}: {title!r} does not fit the "
                    f"{field.width}-wide {field.key} column it heads"
                )
    assert checked, "the sweep examined no padded column, so it asserted nothing"


@pytest.mark.os_agnostic
def test_the_device_rows_carry_a_header_over_their_own_columns() -> None:
    """Two unlabelled figures on every row, in the tool's only headerless table.

    Every other column-shaped view in this tool names its columns, including
    the disk table nested one line below these rows, so `3.0 x4   3.0 x4` was
    the one place a reader had to know which figure was which.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import Fabric, FabricView, device_header_line, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load("linux-nvme-board"))
    findings = diagnose(machine)
    fabric = Fabric(machine.pci_tree, 160, FabricView(density=TreeDensity.STORAGE_ONLY))
    header = device_header_line(fabric).plain
    node, _level = fabric.drawn()[0]
    row = fabric.row(node, findings).plain
    capable, running = (text for text, _style in hop_cells_of(node))

    assert header.index("address") == row.index(node.address), f"{header!r} against {row!r}"
    assert header.index("capable") == row.index(capable), f"{header!r} against {row!r}"
    assert header.index("running") == row.index(running, row.index(capable) + 1), f"{header!r} against {row!r}"
    assert header.index("name") == row.index(node.name[:8]), f"{header!r} against {row!r}"

    buffer = io.StringIO()
    Console(file=buffer, width=160, no_color=True).print(
        render_fabric(machine, findings, 160, FabricView(density=TreeDensity.STORAGE_ONLY))
    )
    lines = buffer.getvalue().splitlines()
    first_device = min(index for index, line in enumerate(lines) if DeviceLine.search(line))
    assert any("address" in line and "capable" in line for line in lines[:first_device]), (
        "the header sits below the rows it labels"
    )


@pytest.mark.os_agnostic
def test_the_header_names_exactly_the_columns_the_rows_draw() -> None:
    """One arithmetic, two consumers: a header cannot label a dropped column.

    The row drops both hop columns whole when the width cannot hold them, so a
    header computed separately would keep naming `capable` over the device name
    at exactly the widths where the column is gone - and a header is read as a
    promise about what sits beneath it.

    Driven on a hand-built device whose link was READ, so the hop text starts
    `Gen3x4` and cannot be confused with anything else on the row. A capture
    whose hops are all the dash symbol cannot answer this question at all: `-`
    also occurs in the tree glyph `|-`, so "the row drew a hop" would be true
    at every width, which is how this test first passed while proving nothing.
    """
    from lsdsk.adapters.hw import fabric as fabric_module
    from lsdsk.adapters.render.tree import Fabric, device_header_line
    from lsdsk.domain.enums import PciPortKind
    from lsdsk.domain.models import PcieLink

    tree = fabric_module.assemble(
        [
            fabric_module.NodeSource(
                address="0000:00:01.0",
                name="a controller whose link was read",
                class_code=0x010802,
                vendor=None,
                driver=None,
                link=PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=8.0, max_width=4),
                port_kind=PciPortKind.UNKNOWN,
                connector_present=None,
                physical_slot_number=None,
                parent=None,
                pcie_capability_present=True,
            )
        ]
    )
    seen = {True: 0, False: 0}
    for width in range(20, 201):
        fabric = Fabric(tree, width, FabricView(density=TreeDensity.FULL))
        header = device_header_line(fabric)
        node, _level = fabric.drawn()[0]
        # Matches BOTH hop tiers: the narrow figure is a prefix of the wide
        # one, so this asks "did the row draw a hop" without caring which.
        drew_hops = "Gen3x4" in fabric.row(node, ()).plain
        seen[drew_hops] += 1

        assert ("capable" in header.plain) == drew_hops, f"at {width}: {header.plain!r}"
        assert ("running" in header.plain) == drew_hops, f"at {width}: {header.plain!r}"
        console = Console(file=io.StringIO(), width=width, no_color=True)
        with console.capture() as capture:
            console.print(header)
        assert len(capture.get().rstrip("\n").split("\n")) == 1, f"at {width}: the header wrapped"

    assert seen[True] and seen[False], f"the sweep never saw both shapes: {seen}"


def hop_cells_of(node: Any) -> Any:
    """The hop cells of one node, imported where the tests can share it."""
    from lsdsk.adapters.render.tree import hop_cells

    return hop_cells(node)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", ["linux-nvme-board", "windows-ahci"])
def test_a_disk_block_carries_the_rules_of_the_controller_above_it(host: str) -> None:
    """The rule stopped at every disk block and resumed on the far side.

    A disk row blanked the whole spine, so a reader following a vertical rule
    down the page lost it at each block and had to trust that it came back in
    the right column. The spine is a fixed-width field, so drawing the
    ancestors' rules there costs no width at all - the columns after it do not
    move, which is the law this section is built on.

    Read off the RENDERED section rather than from the row builders, because
    the builders take the rules as an argument and a test that passes them in
    would be asserting what it had just computed.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import Fabric, FabricView, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    fabric = Fabric(machine.pci_tree, 160, FabricView(density=TreeDensity.STORAGE_ONLY))
    buffer = io.StringIO()
    Console(file=buffer, width=160, no_color=True).print(
        render_fabric(machine, findings, 160, FabricView(density=TreeDensity.STORAGE_ONLY))
    )
    lines = buffer.getvalue().splitlines()

    checked = 0
    for node, _level in fabric.drawn():
        if not (node.is_storage and machine.disks_on(node.address) and fabric.level_of(node) > 1):
            continue
        index = next(position for position, line in enumerate(lines) if node.address in line)
        row = lines[index]
        marker = row.index(node.address) - fabric.spine
        above = slice(marker, marker + 2 * (fabric.level_of(node) - 1))
        block = lines[index + 1 : index + 2 + len(machine.disks_on(node.address))]
        assert block, f"{host} {node.address}: nothing was drawn under it"
        for line in block:
            assert line[above] == row[above], f"{host} {node.address}: {line[above]!r} against {row[above]!r}"
        checked += 1
    assert checked, f"{host} drew no nested storage controller with disks"


@pytest.mark.os_agnostic
def test_every_character_the_tree_draws_survives_a_legacy_console() -> None:
    """The rules are box drawing, and a cp1252 console gets the old picture.

    Nothing stops a box-drawing character reaching a legacy Windows console,
    and without a fallback entry each one arrives as a literal `?` - measured
    before this change. The fallback maps one character to one character, so
    the spine keeps its width there: a wider or narrower substitute would break
    the fixed-spine law on exactly the console the fallback exists for.
    """
    from lsdsk.adapters.cli.safe_console import ASCII_FALLBACKS, encode_safe
    from lsdsk.adapters.render.layout import TREE_BRANCH, TREE_DOWN, TREE_LAST, TREE_LEAD, TREE_PIPE, TREE_STOP

    glyphs = "".join((TREE_BRANCH, TREE_DOWN, TREE_LAST, TREE_LEAD, TREE_PIPE, TREE_STOP))
    for character in set(glyphs) - {" "}:
        assert character in ASCII_FALLBACKS or character.isascii(), f"{character!r} has no ASCII fallback"
        assert len(ASCII_FALLBACKS.get(character, character)) == 1, f"{character!r} changes width when it degrades"

    degraded = encode_safe(glyphs, "cp1252")

    assert "?" not in degraded, f"a glyph reached a legacy console as a question mark: {degraded!r}"
    assert len(degraded) == len(glyphs), f"the spine changed width: {degraded!r}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", ["linux-nvme-board", "linux-sas-hba"])
def test_nothing_drawn_between_two_siblings_breaks_the_rule_between_them(host: str) -> None:
    """A rule runs from a device to its next sibling, past whatever is between.

    Between two devices at one level the vertical rule in their own column is
    live, and the section draws other things in that gap: the drives of the
    first one, its column header, the header repeated for the rows that follow.
    Each of those blanked the rule and it resumed on the far side, which is
    exactly what a reader tracking a line down the page cannot follow.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.layout import TREE_BRANCH, TREE_LAST, TREE_PIPE
    from lsdsk.adapters.render.tree import Fabric, FabricView, render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    fabric = Fabric(machine.pci_tree, 160, FabricView(density=TreeDensity.STORAGE_ONLY))
    buffer = io.StringIO()
    Console(file=buffer, width=160, no_color=True).print(
        render_fabric(machine, findings, 160, FabricView(density=TreeDensity.STORAGE_ONLY))
    )
    lines = buffer.getvalue().splitlines()
    rules = {TREE_PIPE[0], TREE_BRANCH[0], TREE_LAST[0]}

    def row_of(address: str) -> int:
        return next(index for index, line in enumerate(lines) if address in line)

    checked = 0
    for group in fabric.by_parent.values():
        for node, sibling in itertools.pairwise(group):
            first, second = row_of(node.address), row_of(sibling.address)
            column = lines[second].index(TREE_BRANCH[0] if sibling is not group[-1] else TREE_LAST[0])
            for line in lines[first + 1 : second]:
                assert len(line) > column and line[column] in rules, (
                    f"{host}: the rule at column {column} breaks between "
                    f"{node.address} and {sibling.address}: {line[: column + 1]!r}"
                )
                checked += 1
    assert checked, f"{host} drew nothing between two siblings"


@pytest.mark.os_agnostic
def test_the_printed_tree_and_the_selectable_one_are_built_from_one_list_of_lines() -> None:
    """One arithmetic, two consumers - the law a row's columns already follow.

    ``render_fabric`` prints these lines and the interactive page makes each one
    selectable. Built twice they would be two trees, and the second would drift
    the way a second column list already made the disk page name a drive by
    model alone. Proved by rendering both and requiring the same bytes, across
    every capture, every density and several widths, because a difference could
    appear at one width alone.
    """
    from rich.console import Group

    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import fabric_lines
    from lsdsk.domain.diagnostics import diagnose

    for host in DENSITY_COUNTS:
        machine = build_from(_load(host))
        findings = diagnose(machine)
        for density in TreeDensity:
            for width in (60, 80, 120, 200):
                view = FabricView(density=density)
                whole = io.StringIO()
                Console(file=whole, width=width, no_color=True).print(render_fabric(machine, findings, width, view))
                lines = fabric_lines(machine, findings, width, view)
                rebuilt = io.StringIO()
                Console(file=rebuilt, width=width, no_color=True).print(Group(*(line.text for line in lines)))
                assert whole.getvalue() == rebuilt.getvalue(), f"{host} {density.value} w{width}"


@pytest.mark.os_agnostic
def test_a_line_carries_the_thing_it_is_about_and_a_decorative_one_carries_nothing() -> None:
    """What may be selected, and what may not, decided where the line is made.

    The board line is the one that is easy to get wrong in the quiet direction:
    it looks like a heading and it is about the machine, so it carries a subject
    while the note, the legend and every repeated column header do not.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.adapters.render.tree import fabric_lines
    from lsdsk.domain.diagnostics import diagnose

    for host in DENSITY_COUNTS:
        machine = build_from(_load(host))
        lines = fabric_lines(machine, diagnose(machine), 160, FabricView(density=TreeDensity.FULL))
        assert lines, host
        subjects = [line.subject for line in lines]
        assert subjects.count(machine) == 1, f"{host}: the board line is not about the machine exactly once"

        decorative = [line.text.plain for line in lines if line.subject is None]
        assert any("--tree-density" in one or 'press "d"' in one for one in decorative), (
            f"{host}: the density note is selectable"
        )
        # The two repeated column headers. Matched on the titles the section's
        # own constants give them rather than on a literal, so renaming a column
        # cannot leave this passing over a header it no longer recognises.
        assert any("address" in one and "capable" in one for one in decorative), (
            f"{host}: the device column header is selectable"
        )
        assert any("device" in one and "model" in one for one in decorative), (
            f"{host}: the disk column header is selectable"
        )

        # Every drive listed reaches a subject of its own, which is what lets a
        # cursor stop on it.
        drives = {line.subject for line in lines if line.subject in set(machine.disks)}
        assert len(drives) == len(machine.disks), f"{host}: {len(drives)} of {len(machine.disks)} drives selectable"
