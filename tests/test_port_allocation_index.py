"""A starved drive finds its swap partner without walking every candidate.

The swap search used to scan every drive holding more port than it can use, for
every drive starved by its own port, so a machine cost the PRODUCT of the two -
exactly (n/2)^2 candidate reads for n drives split evenly and no swap to find.
Two properties are held here, and the second is the one that makes the first
safe: the work grows linearly with the machine, and the swaps proposed are
IDENTICAL to what the first-match scan proposed, partner for partner, in the
same order. The scan is kept below as the oracle for that, because it is the
plainest statement of which partner the rule means.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.domain.diagnostics import diagnose_port_allocation
from lsdsk.domain.enums import Severity
from lsdsk.domain.models import Disk, Finding, InterfaceLink, Inventory

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CAPTURES = ("linux-sas-hba", "linux-nvme-board", "linux-minimal", "windows-ahci", "windows-usb-uas")

#: Drives in the smaller arm of the doubling. Large enough that a quadratic's
#: (n/2)^2 term dwarfs the handful of linear reads every drive costs anyway.
_DRIVES = 400

#: How many times the doubled machine may cost the single one, in candidate
#: reads. A scan of every candidate per starved drive quadruples on a doubling
#: and measured 3.8 here; an indexed search doubles. Three sits between the
#: two, and reads are counted rather than timed, so a loaded runner cannot
#: move it.
_ACCEPTABLE_GROWTH = 3.0


# --------------------------------------------------------------------------
# The oracle: the first-match scan, as the rule states it
# --------------------------------------------------------------------------


def _scan_for_partner(holders: Sequence[Disk], *, needs: float, offers: float, taken: set[str]) -> Disk | None:
    for candidate in holders:
        link = candidate.link
        if candidate.node in taken or link.port_max_gbps is None or link.drive_max_gbps is None:
            continue
        if link.port_max_gbps >= needs and link.drive_max_gbps <= offers:
            return candidate
    return None


def _diagnose_by_scanning(inventory: Inventory) -> list[Finding]:
    """What the rule proposes, found by scanning every candidate per starved drive."""
    starved = [disk for disk in inventory.disks if disk.link.is_port_limited]
    holders = tuple(
        disk
        for disk in inventory.disks
        if disk.link.drive_max_gbps is not None
        and disk.link.port_max_gbps is not None
        and disk.link.drive_max_gbps < disk.link.port_max_gbps
    )
    findings: list[Finding] = []
    taken: set[str] = set()
    for disk in starved:
        port_max, drive_max = disk.link.port_max_gbps, disk.link.drive_max_gbps
        if port_max is None or drive_max is None:
            continue
        partner = _scan_for_partner(holders, needs=drive_max, offers=port_max, taken=taken)
        if partner is None:
            continue
        taken.add(partner.node)
        findings.append(
            Finding(
                severity=Severity.WARNING,
                subject=disk.path,
                title=f"{disk.model} and {partner.model} are in the wrong ports",
                detail=(
                    f"{disk.path} can do {drive_max:g} Gb/s but sits on a {port_max:g} Gb/s port, while "
                    f"{partner.path} tops out at {partner.link.drive_max_gbps:g} Gb/s and is holding a "
                    f"{partner.link.port_max_gbps:g} Gb/s one it cannot use."
                ),
                action=f"Swap the two drives over. {partner.path} loses nothing and {disk.path} gains.",
            )
        )
    return findings


# --------------------------------------------------------------------------
# Identical answers
# --------------------------------------------------------------------------

#: The rates real hardware publishes, drawn most of the time so pairs collide
#: the way they do on a real machine, with an arbitrary figure - including an
#: infinity and NaN, which a crafted capture can carry - drawn the rest.
_RATE = st.one_of(
    st.none(),
    st.sampled_from([1.5, 3.0, 6.0, 12.0, 22.5]),
    st.floats(min_value=-1.0, max_value=64.0),
    st.sampled_from([math.inf, -math.inf, math.nan]),
)

#: Nodes are usually unique, but a capture is untrusted and two entries can
#: carry one name; the scan promises a NODE, so a second drive of that name is
#: taken with the first, and the index has to do the same.
_NODE = st.one_of(st.sampled_from(["sda", "sdb", "sdc"]), st.uuids().map(str))


@st.composite
def _machines(draw: st.DrawFn) -> Inventory:
    count = draw(st.integers(min_value=0, max_value=40))
    disks = tuple(
        Disk(
            node=draw(_NODE),
            path=f"/dev/d{index}",
            model=f"model {index}",
            link=InterfaceLink(drive_max_gbps=draw(_RATE), port_max_gbps=draw(_RATE)),
        )
        for index in range(count)
    )
    return Inventory(hostname="generated", disks=disks)


@pytest.mark.os_agnostic
@given(machine=_machines())
@settings(max_examples=400, deadline=None)
def test_the_indexed_search_proposes_exactly_the_swaps_the_scan_does(machine: Inventory) -> None:
    """Same partners, same order, same text, over arbitrary machines."""
    assert diagnose_port_allocation(machine) == _diagnose_by_scanning(machine)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("host", CAPTURES)
def test_every_committed_capture_gets_the_swaps_the_scan_gives_it(host: str) -> None:
    """The real machines agree too, including the ones with nothing to swap."""
    payload: dict[str, Any] = json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))
    machine = build_from(payload)
    assert diagnose_port_allocation(machine) == _diagnose_by_scanning(machine)


@pytest.mark.os_agnostic
def test_the_equivalence_can_tell_two_partners_apart() -> None:
    """The control: the scan's answer depends on WHICH partner is taken first.

    Two holders fit the first starved drive and only the second fits the second
    one, so first-match gives the first holder to the first drive and a swap to
    each - a search that returned any fitting partner rather than the first
    could hand the second holder over and leave the second drive with nothing.
    The generated machines are only a test of that choice if a case like this
    one is reachable, so it is pinned here by hand.
    """
    machine = Inventory(
        hostname="h",
        disks=(
            Disk(node="a", path="/dev/a", model="A", link=InterfaceLink(drive_max_gbps=6.0, port_max_gbps=3.0)),
            Disk(node="b", path="/dev/b", model="B", link=InterfaceLink(drive_max_gbps=12.0, port_max_gbps=3.0)),
            Disk(node="c", path="/dev/c", model="C", link=InterfaceLink(drive_max_gbps=1.5, port_max_gbps=6.0)),
            Disk(node="d", path="/dev/d", model="D", link=InterfaceLink(drive_max_gbps=1.5, port_max_gbps=12.0)),
        ),
    )
    findings = diagnose_port_allocation(machine)
    assert findings == _diagnose_by_scanning(machine)
    assert [(finding.subject, finding.title) for finding in findings] == [
        ("/dev/a", "A and C are in the wrong ports"),
        ("/dev/b", "B and D are in the wrong ports"),
    ]


# --------------------------------------------------------------------------
# Linear work
# --------------------------------------------------------------------------


class _CountingDisk(Disk, frozen=True):
    """A drive that counts how often anything reads its link.

    Every candidate the search considers is judged by its link, so this counts
    the search's work through the input alone, with nothing inside the rule
    patched. The inventory keeps the instance as given rather than rebuilding
    it, which ``_reads_to_diagnose`` asserts rather than assumes.
    """

    reads: ClassVar[int] = 0

    def __getattribute__(self, name: str) -> Any:
        if name == "link":
            _CountingDisk.reads += 1
        return super().__getattribute__(name)


def _drive(name: str, *, drive: float, port: float) -> _CountingDisk:
    return _CountingDisk(
        node=name, path=f"/dev/{name}", model=name, link=InterfaceLink(drive_max_gbps=drive, port_max_gbps=port)
    )


def _reads_to_diagnose(count: int, *, partner_port: float) -> tuple[int, int]:
    """Candidate reads for ``count`` drives, half starved and half holding spare port.

    ``partner_port`` decides whether a swap exists: at 6 every holder fits every
    starved drive, so each takes the next one; at 3 none does, so each searches
    all of them and finds nothing.
    """
    half = count // 2
    starved = [_drive(f"s{index}", drive=6.0, port=3.0) for index in range(half)]
    holders = [_drive(f"h{index}", drive=1.5, port=partner_port) for index in range(half)]
    machine = Inventory(hostname="scaled", disks=(*starved, *holders))
    assert all(type(disk) is _CountingDisk for disk in machine.disks), "the inventory rebuilt the drives"
    _CountingDisk.reads = 0
    swaps = len(diagnose_port_allocation(machine))
    return _CountingDisk.reads, swaps


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("partner_port", "swaps_per_starved_drive"),
    [(6.0, 1), (3.0, 0)],
    ids=["every-holder-fits", "no-holder-fits"],
)
def test_doubling_the_drives_does_not_quadruple_the_swap_search(
    partner_port: float, swaps_per_starved_drive: int
) -> None:
    """Twice the drives costs about twice the reads, with or without a swap to find."""
    single, single_swaps = _reads_to_diagnose(_DRIVES, partner_port=partner_port)
    doubled, doubled_swaps = _reads_to_diagnose(2 * _DRIVES, partner_port=partner_port)
    # The control: the arm really is the case it names, so a machine the rule
    # never searched cannot pass for one it searched cheaply.
    assert single_swaps == swaps_per_starved_drive * _DRIVES // 2
    assert doubled_swaps == swaps_per_starved_drive * _DRIVES
    assert single > 0, "no link was read, so the counter is not wired"
    assert doubled / single <= _ACCEPTABLE_GROWTH, (
        f"doubling {_DRIVES} drives read {doubled} links against {single}, "
        f"{doubled / single:.2f} times the work: the search is scanning every candidate per drive"
    )
