"""The native nvlist decoder, against buffers built from the OpenZFS 2.4.4 layout."""

from __future__ import annotations

import struct

import pytest
from nvlist_builders import (
    NVLIST_ARRAY,
    STRING,
    UINT64,
    UINT64_ARRAY,
    embedded,
    embedded_array,
    pair,
    stream,
    text,
    u64,
    u64_array,
)

from lsdsk.adapters.hw.decode.nvlist import MAX_DEPTH, MAX_PAIRS, NvValue, PoolSpace, decode_nvlist, pool_space


def _stats(alloc: int, space: int) -> list[int]:
    return [0, 7, 0, alloc, space, space, 0, 0, *([0] * 40)]


def test_a_pool_config_decodes_to_its_size_and_allocation() -> None:
    blob = stream(
        [
            text("name", "tank"),
            u64("pool_guid", 42),
            embedded(
                "vdev_tree",
                [text("type", "root"), u64_array("vdev_stats", _stats(alloc=300, space=1000))],
            ),
        ]
    )
    config = decode_nvlist(blob)
    assert config["name"] == "tank"
    assert pool_space(config) == PoolSpace(size_bytes=1000, alloc_bytes=300)


def test_pairs_after_an_embedded_list_belong_to_the_outer_list() -> None:
    blob = stream([embedded("inner", [u64("a", 1)]), u64("after", 2)])
    assert decode_nvlist(blob) == {"inner": {"a": 1}, "after": 2}


def test_a_list_array_decodes_every_member_in_order() -> None:
    blob = stream([embedded_array("children", [[u64("id", 0)], [u64("id", 1)]]), u64("tail", 9)])
    decoded = decode_nvlist(blob)
    assert decoded["children"] == ({"id": 0}, {"id": 1})
    assert decoded["tail"] == 9


def test_an_unknown_pair_type_is_skipped_by_its_size() -> None:
    boolean_value = 21
    blob = stream([pair("flag", boolean_value, struct.pack("<i", 1)), u64("kept", 5)])
    decoded = decode_nvlist(blob)
    assert decoded["kept"] == 5
    assert "flag" not in decoded


def _nested(depth: int) -> bytes:
    inner: list[bytes] = [u64("leaf", 1)]
    for level in range(depth):
        inner = [embedded(f"l{level}", inner)]
    return stream(inner)


def _with_name_size(pair: bytes, name_size: int) -> bytes:
    patched = bytearray(pair)
    struct.pack_into("<h", patched, 4, name_size)
    return bytes(patched)


def test_a_list_nested_exactly_to_the_depth_limit_still_decodes() -> None:
    # The top list is depth 0 and each embedded list is one deeper, so _nested(n)
    # reaches depth n and the check refuses only depth > MAX_DEPTH.
    assert "l31" in decode_nvlist(_nested(MAX_DEPTH))


# Every case carries an explicit id: pytest otherwise spells the whole buffer into
# the node id, and two of these run to megabytes, past what Windows lets
# PYTEST_CURRENT_TEST hold.
@pytest.mark.parametrize(
    ("blob", "reason"),
    [
        pytest.param(b"", "too short", id="empty"),
        pytest.param(bytes([1, 1, 0, 0]) + b"\0" * 12, "not a native", id="xdr-encoding"),
        pytest.param(bytes([0, 0, 0, 0]) + b"\0" * 12, "not a native", id="encoding-zero"),
        pytest.param(stream([u64("a", 1)])[:-6], "runs past", id="truncated-stream"),
        pytest.param(stream([pair("x", UINT64, b"")]), "runs past", id="value-missing"),
        pytest.param(_nested(MAX_DEPTH + 1), "nested deeper", id="too-deep"),
        pytest.param(stream([text("s", "x" * 70_000)]), "string longer", id="string-too-long"),
        pytest.param(stream([pair("x", UINT64, b""), u64("next", 1)]), "runs past", id="value-missing-before-next"),
        pytest.param(
            stream([pair("a", UINT64_ARRAY, struct.pack("<Q", 1), nelem=3), u64("next", 1)]),
            "runs past",
            id="array-short-of-nelem",
        ),
        pytest.param(stream([u64("a", 1)] * (MAX_PAIRS + 1)), "more than", id="too-many-pairs"),
        pytest.param(stream([struct.pack("<i", 8) + bytes(28)]), "less than its own header", id="size-below-header"),
        pytest.param(stream([_with_name_size(u64("a", 1), 200)]), "name runs past", id="name-past-pair"),
        pytest.param(stream([pair("s", STRING, b"abcdefgh")]), "string runs past", id="string-unterminated"),
        pytest.param(
            stream([pair("c", NVLIST_ARRAY, b"", nelem=MAX_PAIRS + 1)]),
            "list array declares",
            id="list-array-too-many",
        ),
        pytest.param(stream([pair("c", NVLIST_ARRAY, b"", nelem=-1)]), "list array declares", id="list-array-negative"),
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
