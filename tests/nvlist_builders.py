"""Builders for native nvlist buffers, shared by the decoder and reader tests."""

from __future__ import annotations

import struct

UINT64, STRING, UINT64_ARRAY, NVLIST, NVLIST_ARRAY = 8, 9, 16, 19, 20


def align(size: int) -> int:
    """Round up to the 8-byte boundary nvlist pairs sit on."""
    return (size + 7) & ~7


def pair(name: str, kind: int, value: bytes, nelem: int = 1) -> bytes:
    """One nvpair: header, NUL-terminated name, then the value, padded."""
    raw_name = name.encode() + b"\0"
    head = 16 + len(raw_name)
    body = raw_name.ljust(align(head) - 16, b"\0") + value
    size = align(16 + len(body))
    return struct.pack("<ihhii", size, len(raw_name), 0, nelem, kind) + body.ljust(size - 16, b"\0")


def pairs(items: list[bytes]) -> bytes:
    """Concatenated pairs closed by the zero terminator."""
    return b"".join(items) + b"\0\0\0\0"


def nvlist_header() -> bytes:
    """An embedded list header (version, flag, priv, flags, pad)."""
    return struct.pack("<iIQIi", 0, 1, 0, 0, 0)


def u64(name: str, value: int) -> bytes:
    """A uint64 pair."""
    return pair(name, UINT64, struct.pack("<Q", value))


def text(name: str, value: str) -> bytes:
    """A string pair."""
    return pair(name, STRING, value.encode() + b"\0")


def u64_array(name: str, values: list[int]) -> bytes:
    """A uint64 array pair."""
    return pair(name, UINT64_ARRAY, struct.pack(f"<{len(values)}Q", *values), nelem=len(values))


def embedded(name: str, inner: list[bytes]) -> bytes:
    """An embedded list pair followed by its own pairs."""
    return pair(name, NVLIST, nvlist_header()) + pairs(inner)


def embedded_array(name: str, lists: list[list[bytes]]) -> bytes:
    """A list-array pair followed by every member list."""
    value = b"\0" * (8 * len(lists)) + nvlist_header() * len(lists)
    return pair(name, NVLIST_ARRAY, value, nelem=len(lists)) + b"".join(pairs(inner) for inner in lists)


def stream(items: list[bytes]) -> bytes:
    """A native-encoded nvlist stream: header, nvlist header and the given pairs.

    Args:
        items: Already-encoded pairs.

    Returns:
        The stream bytes.
    """
    return bytes([0, 1, 0, 0]) + struct.pack("<iI", 0, 1) + pairs(items)


def pool_config(*, alloc: int, space: int, padding: int = 0) -> bytes:
    """A POOL_STATS reply: name, an optional padding string, and the root vdev's stats.

    Args:
        alloc: Bytes allocated, written to ``vdev_stats[3]``.
        space: Capacity, written to ``vdev_stats[4]``.
        padding: Approximate extra bytes of strings, to force a large reply.

    Returns:
        The native nvlist stream.
    """
    items = [text("name", "pool")]
    if padding:
        items.append(text("pad", "x" * min(padding, 60_000)))
        items.extend(text(f"pad{n}", "x" * 60_000) for n in range(padding // 60_000))
    stats = [0, 7, 0, alloc, space, space, *([0] * 42)]
    items.append(embedded("vdev_tree", [text("type", "root"), u64_array("vdev_stats", stats)]))
    return stream(items)


def pool_names(names: list[str]) -> bytes:
    """A POOL_CONFIGS reply: one embedded config per pool, keyed by its name.

    Args:
        names: The pool names to list.

    Returns:
        The native nvlist stream.
    """
    return stream([embedded(name, [text("name", name)]) for name in names])


def pool_props(*, fragmentation: int | None) -> bytes:
    """A ZFS_IOC_POOL_GET_PROPS reply: each property an nvlist of value and source.

    Args:
        fragmentation: The FRAG value to publish, or ``None`` to leave the property out.

    Returns:
        The native nvlist stream.
    """
    items = [embedded("size", [u64("value", 100), u64("source", 0)])]
    if fragmentation is not None:
        items.append(embedded("fragmentation", [u64("value", fragmentation), u64("source", 0)]))
    return stream(items)
