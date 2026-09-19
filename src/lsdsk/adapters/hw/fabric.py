"""Assemble the PCI fabric tree both platforms map onto.

Each platform builder says only how a device's PARENT is known on that
platform: Linux derives it from the sysfs path, Windows from the recorded
``parent`` chain. Everything downstream of that is one job with invariants
that are easy to get subtly different in two places, so it lives here once:
one parent per node, children in address order, an unknown parent attaching
to its root bus rather than being dropped, and a cycle broken with the
device kept, because a capture is untrusted input.

The root of the tree is one synthetic node per ROOT BUS rather than the host
bridge: measured, a host bridge cannot be the root because bus ``0000:ff``
has dozens of devices and no class-``0x060000`` device at all, and rooting
there would orphan every one of them. Two root complexes stay two trees.

System Role:
    Adapter layer, the shared half of the platform mapping.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from ...domain.enums import PciPortKind
from ...domain.models import PciNode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...domain.models import PcieLink


# The PCIe capability's port type byte, for the values the spec names as
# ports. 0x0 through 0xA are defined; only these are legs of the fabric, and
# an endpoint value such as 0x9 (root-complex integrated endpoint) is not a
# port, so it reads as UNKNOWN rather than being mapped onto one.
_PORT_KINDS: dict[int, PciPortKind] = {
    4: PciPortKind.ROOT,
    5: PciPortKind.SWITCH_UPSTREAM,
    6: PciPortKind.SWITCH_DOWNSTREAM,
}


def port_kind_of(port_type: int | None) -> PciPortKind:
    """Map a PCIe capability port type onto a port kind.

    Values outside the named set fall to ``UNKNOWN`` rather than raising,
    because a capture is untrusted input and one odd register must not refuse
    a whole live run.

    Args:
        port_type: The capability's port type byte, or ``None`` when it was
            not read.

    Returns:
        The port kind.

    Example:
        >>> f"{port_kind_of(4)}"
        'root'
        >>> f"{port_kind_of(9)}"
        'unknown'
        >>> f"{port_kind_of(None)}"
        'unknown'
    """
    return _PORT_KINDS.get(port_type, PciPortKind.UNKNOWN) if port_type is not None else PciPortKind.UNKNOWN


class NodeSource(NamedTuple):
    """One PCI device as a platform builder hands it to the tree.

    Attributes:
        address: PCI address of the device, or the instance identifier the
            platform knows it by when it publishes no address.
        name: Readable name.
        class_code: The class triple, or ``None`` when the platform published
            none.
        vendor: PCI vendor identifier.
        driver: Bound driver name.
        link: The device's own link state and capability.
        port_kind: What kind of PCIe port this bridge is.
        connector_present: Whether this port ends in a physical slot.
        physical_slot_number: The board's own number for this connector.
        parent: Address of the node directly above, or ``None`` when it has
            none in the source data.
        pcie_capability_present: Whether this device has a PCIe capability at
            all, as far as the platform says: ``True`` measured present,
            ``False`` measured absent, ``None`` where the platform answers
            neither. Last and defaulted because only a platform that can tell
            the last two apart has anything to say here.
    """

    address: str
    name: str
    class_code: int | None
    vendor: int | None
    driver: str | None
    link: PcieLink
    port_kind: PciPortKind
    connector_present: bool | None
    physical_slot_number: int | None
    parent: str | None
    pcie_capability_present: bool | None = None


#: A PCI address, as either platform writes one. The domain is four digits or
#: more, because an Intel VMD re-enumerates its drives into domain 0x10000.
_PCI_ADDRESS_SHAPE = re.compile(r"^[0-9a-f]{4,}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]$")

#: Where a device whose identifier is not an address goes. Windows publishes no
#: address for some devices and the builder falls back to the instance
#: identifier, which carries no bus to derive: splitting one on its last colon
#: produced the EMPTY string, so every such device shared a root labelled with
#: nothing and devices from different buses were merged into it.
UNPLACED_ROOT = "unplaced"

#: What separates a duplicated address from the copy that already took it.
#: Chosen because it cannot occur in a PCI address, so a reader can never take
#: the result for one.
DUPLICATE_MARK = "#"


def _root_bus(address: str) -> str:
    """Return the root bus label a device address belongs to.

    Args:
        address: A PCI address, such as ``0000:03:00.0``, or whatever the
            platform knows an address-less device by.

    Returns:
        The bus segment, such as ``0000:03``, or the unplaced root for an
        identifier that is not an address at all.

    Example:
        >>> _root_bus("0000:03:00.0")
        '0000:03'
        >>> _root_bus("10000:e1:00.0")
        '10000:e1'
        >>> _root_bus("0000:03:00.0#2")
        '0000:03'
        >>> _root_bus(r"PCI\\VEN_1AF4&DEV_1000\\3&13c0b0c5&0&50")
        'unplaced'
    """
    base = address.partition(DUPLICATE_MARK)[0]
    if not _PCI_ADDRESS_SHAPE.match(base):
        return UNPLACED_ROOT
    return base.rpartition(":")[0]


def _keyed_by_address(sources: Sequence[NodeSource]) -> dict[str, NodeSource]:
    """Key every source by its address, keeping a duplicate rather than dropping it.

    Keying with a comprehension collapsed two devices at one address silently,
    last writer wins - and a capture is untrusted input, so the device that
    vanished took its disks' controller with it while the survivor was drawn
    under the other's name. The second keeps its place under a key carrying a
    mark no PCI address can hold, so nothing is lost and nothing reads as an
    address it is not.

    Args:
        sources: Every device the platform builder describes.

    Returns:
        The sources by address, in the order given.

    Example:
        >>> from lsdsk.domain.models import PcieLink
        >>> pair = [
        ...     NodeSource("0000:06:03.0", "first", None, None, None,
        ...                PcieLink(), PciPortKind.UNKNOWN, None, None, None),
        ...     NodeSource("0000:06:03.0", "second", None, None, None,
        ...                PcieLink(), PciPortKind.UNKNOWN, None, None, None),
        ... ]
        >>> [(key, source.name) for key, source in _keyed_by_address(pair).items()]
        [('0000:06:03.0', 'first'), ('0000:06:03.0#2', 'second')]
    """
    keyed: dict[str, NodeSource] = {}
    # The next free copy number per address, REMEMBERED rather than searched for
    # from 2 upward every time: a crafted capture naming one address N times
    # cost N-squared over 2 probes that way, and the 64 MB size guard admits
    # hundreds of thousands of entries, so the ceiling was hours. Only a Windows
    # capture can reach it - the Linux builder keys off sysfs dict keys, which
    # are unique by construction.
    copies: dict[str, int] = {}
    for source in sources:
        # The probe stays, because the counter alone would silently drop a
        # device: a source whose OWN address already carries the mark collides
        # with a copy key, and a capture is untrusted input. It is bounded now,
        # since the counter only ever moves forward.
        copy = copies.get(source.address, 1)
        address = source.address if copy == 1 else f"{source.address}{DUPLICATE_MARK}{copy}"
        while address in keyed:
            copy += 1
            address = f"{source.address}{DUPLICATE_MARK}{copy}"
        copies[source.address] = copy + 1
        keyed[address] = source if address == source.address else source._replace(address=address)
    return keyed


def assemble(sources: Sequence[NodeSource]) -> tuple[PciNode, ...]:
    """Assemble the whole PCI fabric as a root-down list of nodes.

    Every device becomes a node. A device whose parent is known in the source
    data hangs below it; one whose parent is absent or unknown attaches to
    the synthetic root of its own bus, so a device the source data fails to
    place still appears rather than silently vanishing, and ``parent_address
    is None`` remains reserved for the synthetic roots alone. A root bus
    exists exactly where a parentless device sits, so a secondary bus that a
    bridge feeds gets no root of its own and two root complexes stay two
    trees. Children are sorted by address for a stable rendering, and a
    parent cycle is broken at its highest-address member with every node in
    it kept, because a capture is untrusted input.

    Args:
        sources: Every PCI device on the machine, as the platform builders
            describe them.

    Returns:
        All nodes in address order: the synthetic roots first, then every
        device.

    Example:
        >>> from lsdsk.domain.models import PcieLink
        >>> tree = assemble([
        ...     NodeSource("0000:00:1c.0", "bridge", 0x060400, None, None,
        ...                PcieLink(), PciPortKind.ROOT, None, None, None),
        ...     NodeSource("0000:01:00.0", "NVMe", 0x010802, None, None,
        ...                PcieLink(), PciPortKind.UNKNOWN, None, None, "0000:00:1c.0"),
        ... ])
        >>> [(n.address, n.parent_address, n.children) for n in tree]
        [('0000:00', None, ('0000:00:1c.0',)), ('0000:00:1c.0', '0000:00', ('0000:01:00.0',)),
         ('0000:01:00.0', '0000:00:1c.0', ())]
        >>> cycle = assemble([
        ...     NodeSource("0000:02:00.0", "a", None, None, None,
        ...                PcieLink(), PciPortKind.UNKNOWN, None, None, "0000:02:00.1"),
        ...     NodeSource("0000:02:00.1", "b", None, None, None,
        ...                PcieLink(), PciPortKind.UNKNOWN, None, None, "0000:02:00.0"),
        ... ])
        >>> [(n.address, n.parent_address) for n in cycle if n.address != "0000:02"]
        [('0000:02:00.0', '0000:02:00.1'), ('0000:02:00.1', '0000:02')]
    """
    by_address = _keyed_by_address(sources)
    if not by_address:
        return ()

    # A parent the source data does not know is the same as none: the node
    # falls to its own bus's root rather than vanishing or dangling.
    raw: dict[str, str | None] = {
        address: source.parent if source.parent in by_address else None for address, source in by_address.items()
    }
    _break_cycles(raw)

    # A bus is a root bus exactly where some device on it attaches to the
    # synthetic root, which is also what keeps a bridged secondary bus from
    # growing a root of its own.
    root_buses = sorted({_root_bus(address) for address, parent in raw.items() if parent is None})
    for address, parent in raw.items():
        if parent is None:
            # Reserved for the synthetic roots alone; a real device hangs
            # below the root of its own bus.
            raw[address] = _root_bus(address)

    # Children after the promotion, so a root's own children include the
    # devices promoted to it.
    children: dict[str, list[str]] = {}
    for address, parent in raw.items():
        if parent is not None:
            children.setdefault(parent, []).append(address)

    nodes = [
        PciNode(address=bus, name=bus, parent_address=None, children=tuple(sorted(children.get(bus, ()))))
        for bus in root_buses
    ]
    nodes.extend(
        PciNode(
            address=source.address,
            name=source.name,
            class_code=source.class_code,
            vendor=source.vendor,
            driver=source.driver,
            link=source.link,
            pcie_capability_present=source.pcie_capability_present,
            port_kind=source.port_kind,
            connector_present=source.connector_present,
            physical_slot_number=source.physical_slot_number,
            parent_address=raw[source.address],
            children=tuple(sorted(children.get(source.address, ()))),
        )
        for source in sorted(by_address.values(), key=lambda item: item.address)
    )
    return tuple(nodes)


def _break_cycles(parents: dict[str, str | None]) -> None:
    """Sever every parent cycle in place, keeping every node in it.

    A cycle is cut at its highest-address member, which is deterministic
    however the walk enters it, and the cut node falls to its own bus's root.
    Every node stays in the output; only the impossible edge is dropped.
    """
    # Carried across the calls rather than rebuilt inside each. A node already
    # proven to reach a root still does after an edge elsewhere is cut, so
    # re-walking the cleared prefix on every cycle only costs time - quadratic
    # in the node count for a capture holding many small disjoint cycles, which
    # a replay file supplies for free.
    resolved: set[str] = set()
    while True:
        cycle = _first_cycle(parents, resolved)
        if cycle is None:
            return
        parents[max(cycle)] = None


def _first_cycle(parents: Mapping[str, str | None], resolved: set[str]) -> frozenset[str] | None:
    """Return the members of the first parent cycle, or ``None``.

    Args:
        parents: Each node's parent address, or ``None`` at a root.
        resolved: Nodes already proven to reach a root, added to as the walk
            proves more. Shared across calls so each node is walked once.

    Returns:
        The members of the first cycle found, or ``None`` when none remains.
    """
    for start in sorted(parents):
        if start in resolved:
            continue
        path: list[str] = []
        seen: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in parents and current not in resolved:
            if current in seen:
                return frozenset(path[seen[current] :])
            seen[current] = len(path)
            path.append(current)
            current = parents[current]
        resolved.update(path)


__all__ = [
    "DUPLICATE_MARK",
    "UNPLACED_ROOT",
    "NodeSource",
    "assemble",
    "port_kind_of",
]
