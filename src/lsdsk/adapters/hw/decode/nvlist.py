"""Decode a native-encoded nvlist, the format the ZFS control device answers in.

Pure bytes in, plain values out, so it is tested on every runner. The layout is
OpenZFS's own (module/nvpair/nvpair.c, NV_ENCODE_NATIVE, as of 2.4.4): a 4-byte
stream header, the top list's version and flags, then pairs. Each pair is a
16-byte header (total size, name length, reserved, element count, type), the
NUL-terminated name, and the value at the next 8-byte boundary. An embedded list
keeps only its header inside the pair; its own pairs FOLLOW the pair in the
stream, each list ending in four zero bytes.

Only the types a pool's stats need are decoded; any other pair is stepped over
by its own size. Every length is checked before it is followed, because the
buffer comes from a kernel interface whose layout this code cannot verify.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import NamedTuple, TypeAlias

#: Deepest nesting accepted. A pool config nests a handful of levels; the
#: kernel's own encoder stops at 20.
MAX_DEPTH = 32
#: Most pairs one buffer may hold, across every level.
MAX_PAIRS = 100_000
#: Longest string value accepted.
MAX_STRING_BYTES = 65_536

_STREAM_HEADER = 4
_TOP_HEADER = 8
_PAIR_HEADER = struct.Struct("<ihhii")
_END_MARKER = 4
_NATIVE, _LITTLE_ENDIAN = 0, 1
_UINT64, _STRING, _UINT64_ARRAY, _NVLIST, _NVLIST_ARRAY = 8, 9, 16, 19, 20
_VS_ALLOC, _VS_SPACE = 3, 4

NvValue: TypeAlias = int | str | tuple[int, ...] | Mapping[str, "NvValue"] | tuple[Mapping[str, "NvValue"], ...]
NvList: TypeAlias = Mapping[str, NvValue]


class PoolSpace(NamedTuple):
    """A pool's size and allocation, as ``zpool list`` prints them.

    Attributes:
        size_bytes: The root vdev's total capacity.
        alloc_bytes: What is allocated on it.
    """

    size_bytes: int
    alloc_bytes: int


def _align(size: int) -> int:
    return (size + 7) & ~7


class _Reader:
    """A cursor over one buffer, counting pairs across every level."""

    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.pairs = 0

    def need(self, start: int, length: int, what: str, limit: int | None = None) -> None:
        """Refuse a read of ``length`` bytes at ``start`` that crosses ``limit`` (default: the buffer end)."""
        bound = len(self.blob) if limit is None else limit
        if length < 0 or start + length > bound:
            crossed = "the end of the buffer" if limit is None else "its pair"
            msg = f"{what} runs past {crossed}"
            raise ValueError(msg)

    def pair_list(self, start: int, depth: int) -> tuple[dict[str, NvValue], int]:
        if depth > MAX_DEPTH:
            msg = f"nvlist nested deeper than {MAX_DEPTH} levels"
            raise ValueError(msg)
        out: dict[str, NvValue] = {}
        position = start
        while True:
            self.need(position, _END_MARKER, "a pair size")
            (size,) = struct.unpack_from("<i", self.blob, position)
            if size == 0:
                return out, position + _END_MARKER
            name, value, kind, count, end = self._pair_head(position, size)
            decoded = self._value(kind, value, count, end, depth)
            if decoded is None:
                position = end
                continue
            out[name], position = decoded

    def _pair_head(self, position: int, size: int) -> tuple[str, int, int, int, int]:
        self.pairs += 1
        if self.pairs > MAX_PAIRS:
            msg = f"nvlist holds more than {MAX_PAIRS} pairs"
            raise ValueError(msg)
        if size < _PAIR_HEADER.size:
            msg = f"a pair declares {size} bytes, less than its own header"
            raise ValueError(msg)
        self.need(position, size, "a pair")
        _size, name_size, _reserved, count, kind = _PAIR_HEADER.unpack_from(self.blob, position)
        name_start = position + _PAIR_HEADER.size
        if name_size < 1 or _PAIR_HEADER.size + name_size > size:
            msg = "a pair's name runs past the pair"
            raise ValueError(msg)
        name = self.blob[name_start : name_start + name_size - 1].decode("utf-8", errors="replace")
        value = position + _align(_PAIR_HEADER.size + name_size)
        return name, value, kind, count, position + size

    def _value(self, kind: int, value: int, count: int, end: int, depth: int) -> tuple[NvValue, int] | None:
        """Decode one pair's value and say where the stream goes on, or None for a kind not read.

        The one place that decides which kinds are decoded: a kind with no
        branch here is stepped over by its own size, never read as another kind.
        """
        if kind == _UINT64:
            self.need(value, 8, "a uint64", end)
            return struct.unpack_from("<Q", self.blob, value)[0], end
        if kind == _STRING:
            return self._string(value, end), end
        if kind == _UINT64_ARRAY:
            self.need(value, 8 * count, "a uint64 array", end)
            return struct.unpack_from(f"<{count}Q", self.blob, value), end
        if kind == _NVLIST:
            return self.pair_list(end, depth + 1)
        if kind == _NVLIST_ARRAY:
            return self._list_array(count, end, depth)
        return None

    def _string(self, value: int, end: int) -> str:
        terminator = self.blob.find(b"\0", value, end)
        if terminator < 0:
            msg = "a string runs past its pair"
            raise ValueError(msg)
        if terminator - value > MAX_STRING_BYTES:
            msg = f"a string longer than {MAX_STRING_BYTES} bytes"
            raise ValueError(msg)
        return self.blob[value:terminator].decode("utf-8", errors="replace")

    def _list_array(self, count: int, end: int, depth: int) -> tuple[tuple[NvList, ...], int]:
        if count < 0 or count > MAX_PAIRS:
            msg = f"a list array declares {count} members"
            raise ValueError(msg)
        members: list[NvList] = []
        position = end
        for _ in range(count):
            member, position = self.pair_list(position, depth + 1)
            members.append(member)
        return tuple(members), position


def decode_nvlist(blob: bytes) -> dict[str, NvValue]:
    """Decode one native little-endian nvlist.

    Args:
        blob: The bytes the ZFS control device wrote into the destination buffer.

    Returns:
        The top-level list, nested lists as mappings and list arrays as tuples.

    Raises:
        ValueError: When the buffer is too short, not native little-endian, or
            any length in it runs past the buffer or past a bound.

    Example:
        >>> decode_nvlist(bytes([0, 1, 0, 0]) + bytes(8) + bytes(4))
        {}
    """
    if len(blob) < _STREAM_HEADER + _TOP_HEADER + _END_MARKER:
        msg = f"nvlist buffer too short ({len(blob)} bytes)"
        raise ValueError(msg)
    if blob[0] != _NATIVE or blob[1] != _LITTLE_ENDIAN:
        msg = f"not a native little-endian nvlist (header {blob[:4].hex()})"
        raise ValueError(msg)
    decoded, _end = _Reader(blob).pair_list(_STREAM_HEADER + _TOP_HEADER, depth=0)
    return decoded


def _integers(value: NvValue | None) -> tuple[int, ...] | None:
    """Return ``value`` as a tuple of integers, or ``None`` when it is not one."""
    if not isinstance(value, tuple):
        return None
    numbers = tuple(item for item in value if isinstance(item, int))
    return numbers if len(numbers) == len(value) else None


def pool_space(config: NvList) -> PoolSpace:
    """Read a pool's size and allocation from its stats config.

    Args:
        config: What :func:`decode_nvlist` returned for ZFS_IOC_POOL_STATS.

    Returns:
        The root vdev's capacity and allocation.

    Raises:
        ValueError: When the config carries no usable root-vdev stats.
    """
    tree = config.get("vdev_tree")
    if not isinstance(tree, Mapping):
        msg = "pool config has no vdev_tree"
        raise ValueError(msg)
    stats = _integers(tree.get("vdev_stats"))
    if stats is None:
        msg = "pool vdev_tree has no vdev_stats"
        raise ValueError(msg)
    if len(stats) <= _VS_SPACE:
        msg = f"pool vdev_stats too short ({len(stats)} values)"
        raise ValueError(msg)
    size, alloc = stats[_VS_SPACE], stats[_VS_ALLOC]
    if alloc > size:
        msg = f"pool reports more allocated than its size ({alloc} > {size})"
        raise ValueError(msg)
    return PoolSpace(size_bytes=size, alloc_bytes=alloc)


#: The highest fragmentation ZFS reports; anything above is ZFS_FRAG_INVALID ("-" in zpool list).
_MAX_FRAGMENTATION = 100


def pool_fragmentation(props: NvList) -> int | None:
    """A pool's FRAG, from a ``ZFS_IOC_POOL_GET_PROPS`` reply.

    Args:
        props: The decoded reply: each property an nvlist carrying ``value``.

    Returns:
        The percentage, or ``None`` when ZFS publishes no figure for the pool.

    Raises:
        ValueError: The reply carries no fragmentation property or no number in it.
    """
    entry = props.get("fragmentation")
    if not isinstance(entry, Mapping):
        msg = "the pool's properties carry no fragmentation"
        raise ValueError(msg)
    value = entry.get("value")
    if not isinstance(value, int):
        msg = "the fragmentation property carries no number"
        raise ValueError(msg)
    return value if value <= _MAX_FRAGMENTATION else None


__all__ = [
    "MAX_DEPTH",
    "MAX_PAIRS",
    "MAX_STRING_BYTES",
    "NvList",
    "NvValue",
    "PoolSpace",
    "decode_nvlist",
    "pool_fragmentation",
    "pool_space",
]
