"""The native nvlist decoder, against buffers built from the OpenZFS 2.4.4 layout."""

from __future__ import annotations

import struct

import pytest

from lsdsk.adapters.hw.decode.nvlist import MAX_DEPTH, MAX_PAIRS, NvValue, PoolSpace, decode_nvlist, pool_space

_UINT64, _STRING, _UINT64_ARRAY, _NVLIST, _NVLIST_ARRAY = 8, 9, 16, 19, 20


def _align(size: int) -> int:
    return (size + 7) & ~7


def _pair(name: str, kind: int, value: bytes, nelem: int = 1) -> bytes:
    raw_name = name.encode() + b"\0"
    head = 16 + len(raw_name)
    body = raw_name.ljust(_align(head) - 16, b"\0") + value
    size = _align(16 + len(body))
    return struct.pack("<ihhii", size, len(raw_name), 0, nelem, kind) + body.ljust(size - 16, b"\0")


def _pairs(items: list[bytes]) -> bytes:
    return b"".join(items) + b"\0\0\0\0"


def _nvlist_header() -> bytes:
    return struct.pack("<iIQIi", 0, 1, 0, 0, 0)


def _u64(name: str, value: int) -> bytes:
    return _pair(name, _UINT64, struct.pack("<Q", value))


def _text(name: str, value: str) -> bytes:
    return _pair(name, _STRING, value.encode() + b"\0")


def _u64_array(name: str, values: list[int]) -> bytes:
    return _pair(name, _UINT64_ARRAY, struct.pack(f"<{len(values)}Q", *values), nelem=len(values))


def _embedded(name: str, inner: list[bytes]) -> bytes:
    return _pair(name, _NVLIST, _nvlist_header()) + _pairs(inner)


def _embedded_array(name: str, lists: list[list[bytes]]) -> bytes:
    value = b"\0" * (8 * len(lists)) + _nvlist_header() * len(lists)
    return _pair(name, _NVLIST_ARRAY, value, nelem=len(lists)) + b"".join(_pairs(inner) for inner in lists)


def _stream(items: list[bytes]) -> bytes:
    return bytes([0, 1, 0, 0]) + struct.pack("<iI", 0, 1) + _pairs(items)


def _stats(alloc: int, space: int) -> list[int]:
    return [0, 7, 0, alloc, space, space, 0, 0, *([0] * 40)]


def test_a_pool_config_decodes_to_its_size_and_allocation() -> None:
    blob = _stream(
        [
            _text("name", "tank"),
            _u64("pool_guid", 42),
            _embedded(
                "vdev_tree",
                [_text("type", "root"), _u64_array("vdev_stats", _stats(alloc=300, space=1000))],
            ),
        ]
    )
    config = decode_nvlist(blob)
    assert config["name"] == "tank"
    assert pool_space(config) == PoolSpace(size_bytes=1000, alloc_bytes=300)


def test_pairs_after_an_embedded_list_belong_to_the_outer_list() -> None:
    blob = _stream([_embedded("inner", [_u64("a", 1)]), _u64("after", 2)])
    assert decode_nvlist(blob) == {"inner": {"a": 1}, "after": 2}


def test_a_list_array_decodes_every_member_in_order() -> None:
    blob = _stream([_embedded_array("children", [[_u64("id", 0)], [_u64("id", 1)]]), _u64("tail", 9)])
    decoded = decode_nvlist(blob)
    assert decoded["children"] == ({"id": 0}, {"id": 1})
    assert decoded["tail"] == 9


def test_an_unknown_pair_type_is_skipped_by_its_size() -> None:
    boolean_value = 21
    blob = _stream([_pair("flag", boolean_value, struct.pack("<i", 1)), _u64("kept", 5)])
    decoded = decode_nvlist(blob)
    assert decoded["kept"] == 5
    assert "flag" not in decoded


def _nested(depth: int) -> bytes:
    inner: list[bytes] = [_u64("leaf", 1)]
    for level in range(depth):
        inner = [_embedded(f"l{level}", inner)]
    return _stream(inner)


def _with_name_size(pair: bytes, name_size: int) -> bytes:
    patched = bytearray(pair)
    struct.pack_into("<h", patched, 4, name_size)
    return bytes(patched)


def test_a_list_nested_exactly_to_the_depth_limit_still_decodes() -> None:
    # The top list is depth 0 and each embedded list is one deeper, so _nested(n)
    # reaches depth n and the check refuses only depth > MAX_DEPTH.
    assert "l31" in decode_nvlist(_nested(MAX_DEPTH))


@pytest.mark.parametrize(
    ("blob", "reason"),
    [
        (b"", "too short"),
        (bytes([1, 1, 0, 0]) + b"\0" * 12, "not a native"),
        (bytes([0, 0, 0, 0]) + b"\0" * 12, "not a native"),
        (_stream([_u64("a", 1)])[:-6], "runs past"),
        (_stream([_pair("x", _UINT64, b"")]), "runs past"),
        (_nested(MAX_DEPTH + 1), "nested deeper"),
        (_stream([_text("s", "x" * 70_000)]), "string longer"),
        (_stream([_pair("x", _UINT64, b""), _u64("next", 1)]), "runs past"),
        (_stream([_pair("a", _UINT64_ARRAY, struct.pack("<Q", 1), nelem=3), _u64("next", 1)]), "runs past"),
        (_stream([_u64("a", 1)] * (MAX_PAIRS + 1)), "more than"),
        (_stream([struct.pack("<i", 8) + bytes(28)]), "less than its own header"),
        (_stream([_with_name_size(_u64("a", 1), 200)]), "name runs past"),
        (_stream([_pair("s", _STRING, b"abcdefgh")]), "string runs past"),
        (_stream([_pair("c", _NVLIST_ARRAY, b"", nelem=MAX_PAIRS + 1)]), "list array declares"),
        (_stream([_pair("c", _NVLIST_ARRAY, b"", nelem=-1)]), "list array declares"),
    ],
)
def test_a_malformed_buffer_is_refused_with_its_reason(blob: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        decode_nvlist(blob)


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        ({}, "no vdev_tree"),
        ({"vdev_tree": {}}, "no vdev_stats"),
        ({"vdev_tree": {"vdev_stats": ({"a": 1},) * 6}}, "no vdev_stats"),
        ({"vdev_tree": {"vdev_stats": (1, 2, 3)}}, "too short"),
        ({"vdev_tree": {"vdev_stats": (0, 0, 0, 5, 4)}}, "more allocated than"),
    ],
)
def test_a_config_without_usable_stats_is_refused(config: dict[str, NvValue], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        pool_space(config)
