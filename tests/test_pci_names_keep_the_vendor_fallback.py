"""A capture records the best name each PCI device had, not only the fully resolved ones.

``resolve_names`` kept only devices whose own id was in ``pci.ids``, so a
device the database knew the vendor of but not the id of was recorded as
nothing, and a replay drew it as ``Device 8086:7abb`` where the live run said
``Intel Corporation device 7abb``.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode import pciids


@pytest.mark.os_agnostic
def test_a_device_known_by_vendor_only_is_recorded_with_its_vendor() -> None:
    known = pciids.Database({0x8086: "Intel Corporation"}, {})
    entries = {"0000:00:1f.0": {"vendor": "0x8086", "device": "0x7abb"}}
    recorded = pciids.resolve_names(entries, known)
    assert recorded == {"8086:7abb": "Intel Corporation device 7abb"}
    # What a replay draws, from the capture alone (no database of its own).
    replay = pciids.database_from_names(recorded)
    assert pciids.describe(0x8086, 0x7ABB, replay) == "Intel Corporation device 7abb"


@pytest.mark.os_agnostic
def test_a_device_known_by_nothing_is_recorded_as_the_hex_name_it_was_drawn_with() -> None:
    entries = {"0000:00:1f.0": {"vendor": "0xfffe", "device": "0xfffe"}}
    assert pciids.resolve_names(entries, pciids.Database({}, {})) == {"fffe:fffe": "Device fffe:fffe"}
