"""One order for PCI addresses, the one a reader counts in.

Every view that lists controllers, slots or the fabric draws them in this
order. A plain string compare agrees with it for an ordinary address and is
wrong in two places a capture really holds: a five-digit Intel VMD domain, and
the duplicate mark a second device at one address carries.
"""

from __future__ import annotations

from lsdsk.domain.pci_address import pci_address_order


def _ordered(*addresses: str) -> list[str]:
    return sorted(addresses, key=pci_address_order)


def test_bus_device_and_function_are_compared_as_numbers() -> None:
    assert _ordered("0000:02:00.0", "0000:00:17.0", "0000:00:0e.0", "0000:00:06.0") == [
        "0000:00:06.0",
        "0000:00:0e.0",
        "0000:00:17.0",
        "0000:02:00.0",
    ]


def test_a_vmd_domain_of_five_digits_sorts_after_every_four_digit_domain() -> None:
    # As text "10000:" sorts before "ffff:", which put the drives an Intel VMD
    # re-enumerates ahead of the root complex they hang off.
    assert _ordered("10000:e1:00.0", "ffff:00:00.0", "0000:00:00.0") == [
        "0000:00:00.0",
        "ffff:00:00.0",
        "10000:e1:00.0",
    ]


def test_a_duplicate_follows_its_original_and_duplicates_count_as_numbers() -> None:
    assert _ordered("0000:03:00.0#10", "0000:03:00.0#2", "0000:03:00.1", "0000:03:00.0") == [
        "0000:03:00.0",
        "0000:03:00.0#2",
        "0000:03:00.0#10",
        "0000:03:00.1",
    ]


def test_a_root_bus_label_sorts_ahead_of_the_devices_on_it() -> None:
    assert _ordered("0000:01:00.0", "0000:01", "0000:00:1f.2", "0000:00") == [
        "0000:00",
        "0000:00:1f.2",
        "0000:01",
        "0000:01:00.0",
    ]


def test_a_domain_of_fewer_than_four_digits_is_not_an_address() -> None:
    # sysfs pads a domain to four digits at least, so a shorter one names no
    # device; read as an address it would sort among real ones as domain 1.
    for short in ("1:03:00.0", "000:03:00.0", "1:03"):
        assert pci_address_order(short).not_an_address, short
    assert not pci_address_order("0000:03:00.0").not_an_address


def test_an_identifier_that_is_not_an_address_sorts_after_every_address() -> None:
    instance = r"PCI\VEN_1AF4&DEV_1000\3&13c0b0c5&0&50"
    assert _ordered(instance, "unplaced", "10000:e1:00.0", "0000:00:00.0") == [
        "0000:00:00.0",
        "10000:e1:00.0",
        instance,
        "unplaced",
    ]


def test_distinct_identifiers_never_share_a_key() -> None:
    # Two entries with one key would come out in whatever order they went in,
    # which is the arbitrary order this key exists to replace.
    identifiers = ["0000:00:1f.2", "0000:00:1f.2#3", "0000:00", "00000:00:1f.2", "unplaced", "PCI\\A", "PCI\\B", ""]
    assert len({pci_address_order(item) for item in identifiers}) == len(identifiers)
