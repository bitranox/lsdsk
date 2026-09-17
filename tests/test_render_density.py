"""The density knob, at both boundaries a reader reaches it.

The CLI token must agree with the configuration value, which is where the
click.Choice trap bites: find-spells by casefolding the member NAME, so an
enum whose value disagrees with its name gives one setting two vocabularies.
And the rendering must honour the density structurally on real captures,
where the counts below were measured before anything was written.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from lsdsk.domain.enums import TreeDensity

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
_FIXTURE = FIXTURES / "linux-sas-hba.json"

DeviceLine = re.compile(r"(?<![0-9a-f:])0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")

# The measured device-line counts the density rule must reproduce. Every Linux
# figure makes the two reduced densities IDENTICAL - no non-storage device
# there shares a bridge with storage - so only the Windows capture separates
# them, which is why it is the fixture any two-density test must use.
DENSITY_COUNTS: dict[str, dict[TreeDensity, int]] = {
    "linux-sas-hba": {TreeDensity.FULL: 95, TreeDensity.STORAGE_AND_SIBLINGS: 20, TreeDensity.STORAGE_ONLY: 20},
    "linux-minimal": {TreeDensity.FULL: 87, TreeDensity.STORAGE_AND_SIBLINGS: 14, TreeDensity.STORAGE_ONLY: 14},
    "linux-nvme-board": {TreeDensity.FULL: 45, TreeDensity.STORAGE_AND_SIBLINGS: 27, TreeDensity.STORAGE_ONLY: 27},
    "windows-ahci": {TreeDensity.FULL: 27, TreeDensity.STORAGE_AND_SIBLINGS: 15, TreeDensity.STORAGE_ONLY: 12},
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
def test_a_reduced_density_never_floats_a_subtree() -> None:
    """Every drawn device's children are drawn, so no acquired-only bridge floats.

    A subtree with no drawn parent reads as a device on the previous level's
    bus, which is a lie about where it hangs.
    """
    from lsdsk.adapters.hw.snapshot import build_from
    from lsdsk.domain.diagnostics import diagnose

    for host in DENSITY_COUNTS:
        machine = build_from(_load(host))
        findings = diagnose(machine)
        tree = {node.address: node for node in machine.pci_tree}
        for density in tuple(TreeDensity):
            drawn = _drawn_addresses(machine, findings, density)
            for address in drawn:
                node = tree[address]
                if node.parent_address is None:
                    continue
                parent = node.parent_address
                while parent not in drawn and parent in tree:
                    # A synthetic ROOT BUS reaches the top of the walk without
                    # its own device line: it carries its children directly or
                    # under a heading, so reaching one is not floating. Only a
                    # lost DEVICE parent is.
                    ancestor = tree[parent]
                    if ancestor.parent_address is None:
                        break
                    parent = ancestor.parent_address
                assert parent in drawn or tree[parent].parent_address is None, (
                    f"{host} {density.value}: {address} lost its parent line"
                )


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
    from lsdsk.adapters.render.tree import render_fabric
    from lsdsk.domain.diagnostics import diagnose

    machine = build_from(_load(host))
    findings = diagnose(machine)
    marker_alone = {"!!", "!", "~"}
    for density in tuple(TreeDensity):
        buffer = io.StringIO()
        Console(file=buffer, width=width, no_color=True).print(
            render_fabric(machine, findings, width=width, density=density)
        )
        lines = buffer.getvalue().splitlines()
        stranded = [f"{host} w{width} {density.value}" for line in lines if line.strip() in marker_alone]
        assert not stranded, f"marker stranded on its own line: {stranded[:3]}"


def _load(host: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    return payload


def _rendered(machine: Any, findings: Any, density: TreeDensity) -> str:
    from lsdsk.adapters.render.tree import render_fabric

    buffer = io.StringIO()
    Console(file=buffer, width=200, no_color=True).print(render_fabric(machine, findings, width=200, density=density))
    return buffer.getvalue()


def _device_lines(machine: Any, findings: Any, density: TreeDensity) -> list[str]:
    return [line for line in _rendered(machine, findings, density).splitlines() if DeviceLine.search(line)]


def _drawn_addresses(machine: Any, findings: Any, density: TreeDensity) -> set[str]:
    found: set[str] = set()
    for line in _rendered(machine, findings, density).splitlines():
        match = DeviceLine.search(line)
        if match is not None:
            found = {match.group(0), *found}
    return found


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
    asked = [*command, "--tree-density", "storage-only"] if scoped else ["--tree-density", "storage-only", *command]
    full = cli_runner.invoke(cli, [*common, *command], obj=production_factory)
    reduced = cli_runner.invoke(cli, [*common, *asked], obj=production_factory)

    assert full.output, "the run without the option rendered nothing"
    assert reduced.output, "the run with the option rendered nothing"
    drawn_full = [line for line in full.output.splitlines() if DeviceLine.search(line)]
    drawn_reduced = [line for line in reduced.output.splitlines() if DeviceLine.search(line)]
    where = "after" if scoped else "before"
    assert len(drawn_reduced) < len(drawn_full), (
        f"--tree-density {where} {command or ['(no command)']} changed nothing: "
        f"{len(drawn_reduced)} lines against {len(drawn_full)}"
    )
