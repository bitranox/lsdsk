"""The one order PCI addresses are listed in, on every platform and in every view.

A plain string compare agrees with the numeric order for an ordinary address
and is wrong in two places a capture really holds: an Intel VMD re-enumerates
its drives into the five-digit domain ``10000``, which sorts before ``ffff`` as
text, and a second device at one address carries a ``#n`` mark, so ``#10``
sorted before ``#2``.

System Role:
    Domain layer. Pure text to a sort key.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: A PCI address, as either platform writes one. The domain is four digits or
#: more, because an Intel VMD re-enumerates its drives into domain 0x10000.
PCI_ADDRESS_SHAPE = re.compile(r"^[0-9a-f]{4,}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]$")

#: What separates a duplicated address from the copy that already took it.
#: Chosen because it cannot occur in a PCI address, so a reader can never take
#: the result for one.
DUPLICATE_MARK = "#"

#: A root bus as the fabric labels one: domain and bus, no device.
_BUS_LABEL_SHAPE = re.compile(r"^([0-9a-f]{4,}):([0-9a-f]{2})$")

#: Where a bus label sits among the devices of its own bus: ahead of all of them.
_BEFORE_ANY_DEVICE = -1


class PciAddressKey(NamedTuple):
    """Sort key for one PCI address, bus label or other device identifier.

    Every field is compared in order. ``not_an_address`` puts whatever is not
    an address (a Windows instance identifier, the unplaced root) after every
    address, ordered among themselves by ``text``; ``text`` also keeps two
    spellings of one number apart, so distinct identifiers never share a key.
    """

    not_an_address: bool
    domain: int
    bus: int
    device: int
    function: int
    duplicate: int
    text: str


def pci_address_order(address: str) -> PciAddressKey:
    """Return the key that lists PCI addresses in the order a reader counts them.

    Args:
        address: A PCI address such as ``0000:03:00.0``, possibly carrying a
            ``#n`` duplicate mark, a root bus label such as ``0000:03``, or any
            other identifier a platform knows a device by.

    Returns:
        A key comparing domain, bus, device, function and duplicate as numbers.

    Example:
        >>> sorted(["10000:e1:00.0", "0000:02:00.0", "0000:00:1f.2#10", "0000:00:1f.2#2"], key=pci_address_order)
        ['0000:00:1f.2#2', '0000:00:1f.2#10', '0000:02:00.0', '10000:e1:00.0']
        >>> pci_address_order("0000:00") < pci_address_order("0000:00:00.0") < pci_address_order("unplaced")
        True
    """
    base, _, mark = address.partition(DUPLICATE_MARK)
    if PCI_ADDRESS_SHAPE.match(base) and (not mark or mark.isdigit()):
        domain, bus, slot = base.split(":")
        device, function = slot.split(".")
        return PciAddressKey(
            not_an_address=False,
            domain=int(domain, 16),
            bus=int(bus, 16),
            device=int(device, 16),
            function=int(function, 16),
            duplicate=int(mark) if mark else 1,
            text=address,
        )
    bus_label = _BUS_LABEL_SHAPE.match(address)
    if bus_label is not None:
        return PciAddressKey(
            not_an_address=False,
            domain=int(bus_label.group(1), 16),
            bus=int(bus_label.group(2), 16),
            device=_BEFORE_ANY_DEVICE,
            function=_BEFORE_ANY_DEVICE,
            duplicate=0,
            text=address,
        )
    return PciAddressKey(not_an_address=True, domain=0, bus=0, device=0, function=0, duplicate=0, text=address)


__all__ = ["DUPLICATE_MARK", "PCI_ADDRESS_SHAPE", "PciAddressKey", "pci_address_order"]
