"""Boundaries of the Linux decoders that a loosened comparison would cross unseen.

Each case sits exactly on a bound: a temperature one degree outside the range a
drive can report, and a PCI identifier one bit wider than the register that
holds it. They are pinned by literal numbers, not by the module's own constants,
because a test that reads the bound from the code it checks moves with a wrong one.
"""

from __future__ import annotations

from typing import Any

import pytest

from lsdsk.adapters.hw.decode.captured import plausible_celsius
from lsdsk.adapters.hw.snapshot import build_from

pytestmark = pytest.mark.os_agnostic

_SEVENTEEN_BITS = "0x10000"


@pytest.mark.parametrize("per_degree", [1, 1000])
@pytest.mark.parametrize(("celsius", "kept"), [(-61, False), (-60, True), (200, True), (201, False)])
def test_the_plausible_temperature_range_ends_exactly_at_minus_60_and_200(
    celsius: int, kept: bool, per_degree: int
) -> None:
    assert (plausible_celsius(celsius * per_degree, per_degree=per_degree) == celsius) is kept


def _fabric(vendor: str, device: str) -> dict[str, Any]:
    """A bridge holding one SAS controller, both carrying the given ids."""
    bridge, endpoint = "0000:00:03.0", "0000:03:00.0"
    base = "/sys/devices/pci0000:00"
    return {
        "schema": 2,
        "platform": "linux",
        "hostname": "ids",
        "kernel": "6.8.0",
        "pci": {
            bridge: {
                "class": "0x060400",
                "vendor": vendor,
                "device": device,
                "children": [endpoint],
                "path": f"{base}/{bridge}",
            },
            endpoint: {
                "class": "0x010700",
                "vendor": vendor,
                "device": device,
                "path": f"{base}/{bridge}/{endpoint}",
            },
        },
    }


def test_a_seventeen_bit_vendor_is_unread_at_every_site_that_reads_one() -> None:
    inventory = build_from(_fabric(_SEVENTEEN_BITS, "0x0097"))

    (controller,) = inventory.controllers
    (slot,) = inventory.slots
    devices = [node for node in inventory.pci_tree if node.address in {"0000:00:03.0", "0000:03:00.0"}]
    assert controller.vendor is None
    assert (slot.vendor, slot.occupant_vendor) == (None, None)
    assert len(devices) == 2
    assert {node.vendor for node in devices} == {None}


@pytest.mark.parametrize(("vendor", "device"), [("0x1000", _SEVENTEEN_BITS), (_SEVENTEEN_BITS, "0x0097")])
def test_a_seventeen_bit_id_of_either_kind_leaves_a_controller_unnamed(vendor: str, device: str) -> None:
    (controller,) = build_from(_fabric(vendor, device)).controllers

    assert controller.name == "Unknown controller"


def test_sixteen_bit_ids_are_still_read_at_every_site() -> None:
    """The control: the bound sits at 0xFFFF, so the widest real id is kept everywhere."""
    inventory = build_from(_fabric("0xffff", "0xffff"))

    (controller,) = inventory.controllers
    (slot,) = inventory.slots
    assert controller.vendor == 0xFFFF
    assert (slot.vendor, slot.occupant_vendor) == (0xFFFF, 0xFFFF)
    assert controller.name != "Unknown controller"
    assert {node.vendor for node in inventory.pci_tree if node.address.count(":") == 2} == {0xFFFF}
