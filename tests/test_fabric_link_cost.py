"""Grading the PCIe cards costs the machine once, not once per card.

Every view's exit code runs the rules, so a rule that walks the whole PCI tree
per link stalls every command on a large machine. The fabric-link rules did:
the title's carrying clause rebuilt the tree's parent-to-children map for every
link with both ends read - before it was even decided that the link earned a
finding - so a machine cost the PRODUCT of its links and its devices.

Measured at the seam the input arrives at, ``diagnose``, rather than at the
helper that was hot, so the linear work around it counts in both arms. Counted
rather than timed (see ``workcount``), so a loaded runner cannot move it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from workcount import work_to_run

from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import PciPortKind
from lsdsk.domain.fabric_links import fabric_links
from lsdsk.domain.models import Inventory, PcieLink, PcieSlot, PciNode

if TYPE_CHECKING:
    from collections.abc import Callable

#: How many times the doubled machine may cost the single one. A rebuild of the
#: tree per link multiplied the work by 3.1 to 3.8 on a doubling at these sizes;
#: a walk of each link's own subtree doubles it.
_ACCEPTABLE_GROWTH = 2.5

_PORT = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=8)
_CAPPED_CARD = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=16)
_SEATED_CARD = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=8)


def _cards(count: int, card: PcieLink) -> Inventory:
    """``count`` root ports, each holding one graphics card with ``card`` as its link."""
    tree = [PciNode(address="0000:00", name="root bus")]
    slots: list[PcieSlot] = []
    for index in range(count):
        port, address = f"{index:04x}:00:01.0", f"{index:04x}:01:00.0"
        tree.append(
            PciNode(
                address=port,
                name="root port",
                class_code=0x060400,
                parent_address="0000:00",
                port_kind=PciPortKind.ROOT,
                link=_PORT,
                pcie_capability_present=True,
            )
        )
        tree.append(
            PciNode(
                address=address,
                name="card",
                class_code=0x030000,
                parent_address=port,
                link=card,
                pcie_capability_present=True,
            )
        )
        slots.append(PcieSlot(address=port, link=_PORT, occupied=True, occupant_address=address))
    return Inventory(hostname="cards", pci_tree=tuple(tree), slots=tuple(slots))


def _capped(count: int) -> Inventory:
    return _cards(count, _CAPPED_CARD)


def _seated(count: int) -> Inventory:
    return _cards(count, _SEATED_CARD)


def _work_to_diagnose(machine: Inventory, *, capped: bool) -> int:
    findings, work = work_to_run(lambda: diagnose(machine))
    # The controls: every card is a graded link, and each arm raised exactly
    # the hints it is named for, so a machine the rules never reached cannot
    # pass for one they graded cheaply.
    cards = len(machine.slots)
    assert len(fabric_links(machine)) == cards
    hints = [finding for finding in findings if "is capped by its slot" in finding.title]
    assert len(hints) == (cards if capped else 0)
    assert work > 0, "no work was counted, so the counter is not wired"
    return work


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("build", "capped"),
    [(_capped, True), (_seated, False)],
    ids=["every-card-capped", "every-card-seated-in-full"],
)
def test_doubling_the_cards_does_not_quadruple_the_fabric_grading(
    build: Callable[[int], Inventory], *, capped: bool
) -> None:
    """Twice the cards costs about twice the work, whether or not a card earns a hint."""
    single = _work_to_diagnose(build(150), capped=capped)
    doubled = _work_to_diagnose(build(300), capped=capped)
    assert doubled / single <= _ACCEPTABLE_GROWTH, (
        f"doubling 150 cards took {doubled} steps against {single}, "
        f"{doubled / single:.2f} times the work: a rule is walking the whole tree per link"
    )
