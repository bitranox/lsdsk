"""An occupied slot whose occupant publishes no link capability draws a dash for what it needs.

A legacy PCI card behind a bridge has no PCIe capability, so the slot carries
``occupant_link = None``. The panel's ``needs`` cell then holds the not-read
dash whether or not the occupant is a chipset function the register-default
rule would otherwise mark: with no link there is nothing to mark.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render import detail, theme
from lsdsk.domain.diagnostics import register_default_links

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
EXCUSED_PORT = "0000:04:0d.0"
PLAIN_PORT = "0000:00:1c.7"


def _machine(host: str) -> Inventory:
    payload = cast("dict[str, Any]", json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8")))
    return build_from(payload)


def _needs(machine: Inventory, port: str) -> tuple[str, str]:
    slot = next(one for one in machine.slots if one.address == port)
    group = next(one for one in detail.slot_detail(slot, machine).groups if one.label is detail.OCCUPANT)
    return dict(group.values)["needs"]


def test_an_occupant_with_no_link_needs_the_not_read_dash_even_where_the_rule_would_mark_it() -> None:
    machine = _machine("windows-chipset-switch")
    slot = next(one for one in machine.slots if one.address == EXCUSED_PORT)
    assert slot.occupied
    assert slot.occupant_address in register_default_links(machine), "the control: this is a marked function"
    assert _needs(machine, EXCUSED_PORT) != (theme.NOT_READ, theme.STYLE_UNKNOWN), "the control: a link draws a figure"

    linkless = machine.with_changes(
        slots=tuple(
            one.with_changes(occupant_link=None) if one.address == EXCUSED_PORT else one for one in machine.slots
        )
    )

    assert _needs(linkless, EXCUSED_PORT) == (theme.NOT_READ, theme.STYLE_UNKNOWN)


def test_an_occupant_with_no_link_needs_the_not_read_dash_where_the_rule_marks_nothing() -> None:
    machine = _machine("linux-usb-ehci")
    slot = next(one for one in machine.slots if one.address == PLAIN_PORT)
    assert slot.occupied
    assert slot.occupant_address not in register_default_links(machine)
    linkless = machine.with_changes(
        slots=tuple(one.with_changes(occupant_link=None) if one.address == PLAIN_PORT else one for one in machine.slots)
    )

    assert _needs(linkless, PLAIN_PORT) == (theme.NOT_READ, theme.STYLE_UNKNOWN)
