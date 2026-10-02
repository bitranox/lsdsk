"""The free-slot search is one search, whatever kind of card asks it."""

from __future__ import annotations

import pytest

from lsdsk.domain.models import Controller, Inventory, PcieLink, PcieSlot
from lsdsk.domain.placement import Seat, achievable_pcie, free_slot_for, seat_of

CARD = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=16)
NARROW = PcieLink(current_speed_gtps=8.0, current_width=8, max_speed_gtps=8.0, max_width=8)
WIDE = PcieLink(max_speed_gtps=8.0, max_width=16)


def _inventory(*, connector: bool | None) -> Inventory:
    return Inventory(
        hostname="h",
        slots=(
            PcieSlot(address="0000:00:02.0", link=NARROW, occupied=True, connector_present=True),
            PcieSlot(address="0000:00:03.0", link=WIDE, occupied=False, connector_present=connector),
        ),
    )


@pytest.mark.os_agnostic
def test_a_seat_is_what_both_ends_can_give() -> None:
    assert achievable_pcie(Seat(link=CARD, port=NARROW, port_address="0000:00:02.0")) == (8.0, 8)


@pytest.mark.os_agnostic
def test_an_unread_port_gives_no_seat_figure() -> None:
    assert achievable_pcie(Seat(link=CARD, port=PcieLink(), port_address="0000:00:02.0")) == (None, None)


@pytest.mark.os_agnostic
def test_a_free_slot_with_a_read_connector_is_found() -> None:
    slot = free_slot_for(Seat(link=CARD, port=NARROW, port_address="0000:00:02.0"), _inventory(connector=True))
    assert slot is not None and slot.address == "0000:00:03.0"


@pytest.mark.os_agnostic
def test_an_unread_connector_is_never_a_move_target() -> None:
    assert free_slot_for(Seat(link=CARD, port=NARROW, port_address="0000:00:02.0"), _inventory(connector=None)) is None


@pytest.mark.os_agnostic
def test_a_controller_seat_carries_its_link_its_port_and_the_port_address() -> None:
    controller = Controller(
        address="0000:01:00.0", name="hba", link=CARD, upstream=NARROW, upstream_address="0000:00:02.0"
    )
    assert seat_of(controller) == Seat(link=CARD, port=NARROW, port_address="0000:00:02.0")
