"""A render grades its rows from one index, never by rescanning the findings.

A row that found its marker by walking EVERY finding for the one whose subject
matched makes a page cost the product of its rows and its findings. The arms
below count the work rather than time it: how many times the findings are
walked while a page renders, which is a fixed number for an indexed render and
one more per row for a rescanning one. A wall-clock ratio is what flakes on a
loaded runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import theme
from lsdsk.adapters.render.full import render_full
from lsdsk.adapters.render.report import severity_index
from lsdsk.adapters.render.tree import render_fabric
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import Severity
from lsdsk.domain.models import Finding, Inventory

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
#: The committed captures the rules raise something on. windows-ahci raises
#: nothing, so an equivalence over it would compare two empty answers.
CAPTURES = ("linux-sas-hba", "linux-nvme-board", "linux-minimal", "linux-usb-ehci", "windows-usb-uas")

#: Drives in the smaller arm. Small enough that the whole page renders in a
#: blink, large enough that one walk per row cannot hide inside the fixed
#: number of walks the page's other sections make.
_DRIVES = 12


def _worst_by_rescanning(findings: Sequence[Finding], subject: str) -> Severity | None:
    """The oracle: one subject graded by a full walk, most urgent first.

    Kept here rather than in the package because nothing should render this
    way; it is the plain statement of the precedence the index must reproduce.
    """
    matching = {finding.severity for finding in findings if finding.subject == subject}
    return next((s for s in (Severity.CRITICAL, Severity.WARNING, Severity.HINT) if s in matching), None)


class _CountedFindings(tuple[Finding, ...]):
    """The findings, counting how many times anything walks them.

    A tuple subclass so every consumer that takes a ``Sequence[Finding]`` takes
    this unchanged. Slicing hands back a plain tuple, which is fine: a slice is
    taken once per section, never once per row.
    """

    walks = 0

    def __iter__(self) -> Iterator[Finding]:
        type(self).walks += 1
        return super().__iter__()


def _machine(host: str) -> Inventory:
    return build_from(json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8")))


def _scaled(count: int, *, with_fabric: bool) -> tuple[Inventory, _CountedFindings]:
    """A machine of ``count`` drives on the SAS capture, one warning on each."""
    base = _machine("linux-sas-hba")
    template = base.disks[0]
    disks = tuple(template.with_changes(path=f"/dev/sdz{i}", node=f"sdz{i}") for i in range(count))
    machine = base.with_changes(disks=disks, pci_tree=base.pci_tree if with_fabric else ())
    findings = _CountedFindings(
        Finding(severity=Severity.WARNING, subject=disk.path, title="scaled warning") for disk in disks
    )
    return machine, findings


def _walks_to_render(count: int, *, with_fabric: bool) -> int:
    machine, findings = _scaled(count, with_fabric=with_fabric)
    console = Console(width=160, no_color=True)
    _CountedFindings.walks = 0
    with console.capture():
        console.print(render_full(machine, findings, 160))
    walks = _CountedFindings.walks
    # The control: every drive's warning reached a row of the TOPOLOGY section,
    # which is the tree this arm is named for - the fabric, or the
    # disk-and-controller tree a capture with no PCI reading gets. Without it an
    # arm that drew no markers at all would walk the findings the same number of
    # times at any size and pass for the wrong reason. Counted over the section
    # alone, because the disks and health tables further down the page mark
    # every drive too, and a count over the whole page stayed satisfied by them
    # with every marker gone from the tree.
    with console.capture() as section:
        console.print(render_fabric(machine, tuple(findings), 160))
    marked = [
        line for line in section.get().splitlines() if "/dev/sdz" in line and theme.marker_for(Severity.WARNING) in line
    ]
    assert len(marked) == count, f"only {len(marked)} of {count} drives carried their marker in the tree"
    return walks


@pytest.mark.parametrize("with_fabric", [True, False], ids=["fabric", "no-pci-fallback"])
def test_doubling_the_drives_does_not_add_a_walk_of_the_findings_per_row(with_fabric: bool) -> None:
    """The page walks its findings a fixed number of times, whatever it draws."""
    single = _walks_to_render(_DRIVES, with_fabric=with_fabric)
    doubled = _walks_to_render(2 * _DRIVES, with_fabric=with_fabric)
    assert single > 0, "the findings were never walked, so the counter is not wired"
    assert doubled == single, (
        f"rendering {2 * _DRIVES} drives walked the findings {doubled} times against {single} "
        f"for {_DRIVES}: every added row is rescanning them"
    )


@pytest.mark.parametrize("host", CAPTURES)
def test_the_index_grades_every_subject_exactly_as_the_single_subject_lookup_does(host: str) -> None:
    """The index agrees with a full walk for every subject the rules raised."""
    findings = diagnose(_machine(host))
    index = severity_index(findings)
    subjects = {finding.subject for finding in findings}
    assert subjects, f"{host} raised no finding, so this compares nothing"
    assert set(index) == subjects
    for subject in subjects:
        assert index[subject] is _worst_by_rescanning(findings, subject)


@pytest.mark.parametrize(
    "order",
    [
        (Severity.HINT, Severity.WARNING, Severity.CRITICAL),
        (Severity.CRITICAL, Severity.WARNING, Severity.HINT),
        (Severity.WARNING, Severity.CRITICAL, Severity.HINT),
        (Severity.HINT, Severity.CRITICAL),
        (Severity.WARNING, Severity.HINT),
    ],
)
def test_the_worst_severity_wins_whatever_order_the_findings_arrive_in(order: tuple[Severity, ...]) -> None:
    """Critical over warning over hint, never whichever came first or last."""
    findings = [Finding(severity=severity, subject="/dev/sda", title=f"a {severity}") for severity in order]
    expected = next(s for s in (Severity.CRITICAL, Severity.WARNING, Severity.HINT) if s in order)
    assert severity_index(findings)["/dev/sda"] is expected


def test_a_subject_nobody_raised_anything_about_is_absent_from_the_index() -> None:
    """Absent, so a row reads ``None`` from it exactly as a clean subject always did."""
    index = severity_index([Finding(severity=Severity.HINT, subject="/dev/sda", title="a hint")])
    assert index.get("/dev/sdb") is None
