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
    pool_props,
    stream,
    text,
    u64,
    u64_array,
)

from lsdsk.adapters.hw.decode.nvlist import (
    MAX_DEPTH,
    MAX_PAIRS,
    MAX_STRING_BYTES,
    NvValue,
    PoolSpace,
    decode_nvlist,
    pool_fragmentation,
    pool_space,
)


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


def test_a_pool_reports_its_fragmentation_property() -> None:
    assert pool_fragmentation(decode_nvlist(pool_props(fragmentation=22))) == 22


def test_a_pool_that_publishes_no_figure_reads_none() -> None:
    # ZFS_FRAG_INVALID is UINT64_MAX; zpool list prints it as "-".
    assert pool_fragmentation(decode_nvlist(pool_props(fragmentation=2**64 - 1))) is None


def test_properties_without_fragmentation_are_refused() -> None:
    with pytest.raises(ValueError, match="no fragmentation"):
        pool_fragmentation(decode_nvlist(pool_props(fragmentation=None)))


@pytest.mark.parametrize("name_size", [0, -1])
def test_a_pair_whose_name_has_no_room_for_its_terminator_is_refused(name_size: int) -> None:
    # A name size counts the NUL, so anything under one cannot be a name.
    with pytest.raises(ValueError, match="a pair's name runs past the pair"):
        decode_nvlist(stream([_with_name_size(u64("a", 1), name_size)]))


def test_a_string_of_exactly_the_longest_length_decodes_and_one_more_is_refused() -> None:
    longest = "x" * MAX_STRING_BYTES
    assert decode_nvlist(stream([text("s", longest)])) == {"s": longest}
    with pytest.raises(ValueError, match=f"a string longer than {MAX_STRING_BYTES} bytes"):
        decode_nvlist(stream([text("s", longest + "x")]))


def test_a_uint64_array_declaring_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError, match="a uint64 array runs past its pair"):
        decode_nvlist(stream([pair("a", UINT64_ARRAY, bytes(8), nelem=-1)]))


def test_stats_one_value_short_of_the_space_slot_are_refused() -> None:
    # vdev_stats[4] is the space; four values end at the allocation.
    with pytest.raises(ValueError, match=r"pool vdev_stats too short \(4 values\)"):
        pool_space({"vdev_tree": {"vdev_stats": (0, 7, 0, 1)}})


@pytest.mark.parametrize(("value", "read"), [(100, 100), (101, None)])
def test_a_fragmentation_of_100_is_a_figure_and_anything_above_is_none(value: int, read: int | None) -> None:
    # zpool list prints 100 as 100%; only ZFS_FRAG_INVALID (and anything else past 100) has no figure.
    assert pool_fragmentation(decode_nvlist(pool_props(fragmentation=value))) == read
