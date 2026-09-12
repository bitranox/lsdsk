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

from typing import TYPE_CHECKING, NamedTuple

from ...domain.enums import PciPortKind
from ...domain.models import PciNode

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

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


def _root_bus(address: str) -> str:
    """Return the root bus label a device address belongs to.

    Args:
        address: A PCI address, such as ``0000:03:00.0``.

    Returns:
        The bus segment, such as ``0000:03``.

    Example:
        >>> _root_bus("0000:03:00.0")
        '0000:03'
    """
    return address.rpartition(":")[0]


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
    by_address = {source.address: source for source in sources}
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
    while True:
        cycle = _first_cycle(parents)
        if cycle is None:
            return
        parents[max(cycle)] = None


def _first_cycle(parents: Mapping[str, str | None]) -> frozenset[str] | None:
    """Return the members of the first parent cycle, or ``None``."""
    resolved: set[str] = set()
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


def ports_of(nodes: Sequence[PciNode]) -> dict[str, PciNode]:
    """Return every node that is a PCIe port, keyed by address.

    Args:
        nodes: The assembled tree.

    Returns:
        The port nodes of the fabric.
    """
    return {node.address: node for node in nodes if node.is_port}


def subtree_of(nodes: Sequence[PciNode], address: str) -> tuple[PciNode, ...]:
    """Return a node and every node below it.

    Args:
        nodes: The assembled tree.

    Returns:
        The subtree in address order, or empty when the address is not in
        the tree.
    """
    by_address = {node.address: node for node in nodes}
    start = by_address.get(address)
    if start is None:
        return ()
    found: dict[str, PciNode] = {address: start}
    pending = [address]
    while pending:
        for child in by_address[pending.pop()].children:
            if child in by_address and child not in found:
                found[child] = by_address[child]
                pending.append(child)
    return tuple(found[key] for key in sorted(found))


def contains_storage(nodes: Sequence[PciNode], address: str) -> bool:
    """Whether a node's subtree holds a storage controller.

    Args:
        nodes: The assembled tree.
        address: The node to judge.

    Returns:
        True when the node itself or anything below it is storage. False for
        an address not in the tree, because an unknown node claims nothing.
    """
    return any(node.is_storage for node in subtree_of(nodes, address))


def descendant_count(nodes: Sequence[PciNode], address: str) -> int:
    """How many devices sit at or below a node, excluding the node itself.

    Args:
        nodes: The assembled tree.
        address: The node to count from.

    Returns:
        The count of descendants, zero for a leaf or an unknown address.
    """
    return max(len(subtree_of(nodes, address)) - 1, 0)


def walk(nodes: Sequence[PciNode]) -> Iterable[PciNode]:
    """Yield every node of the tree in address order.

    Args:
        nodes: The assembled tree.

    Returns:
        Every node, roots included.
    """
    yield from nodes


__all__ = [
    "NodeSource",
    "assemble",
    "contains_storage",
    "descendant_count",
    "port_kind_of",
    "ports_of",
    "subtree_of",
    "walk",
]
