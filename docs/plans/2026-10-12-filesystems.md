# Filesystems Section Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use bitranox:process-agents-subagent-driven-development (recommended) or
> bitranox:process-plan-executor to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** A new `lsdsk filesystems` section, command, TUI page and JSON list that shows how full every
filesystem, ZFS pool and Windows volume on an inventory disk is, with inode usage where the table is
fixed, and configurable hint/warning findings when one is nearly full.

**Architecture:** Readers capture raw figures (statvfs per Linux mount, the ZFS pool-stats ioctl decoded at
once to name/size/alloc, Win32 space calls per volume); pure builders join them to inventory disks
through the same resolution `used by` uses; a domain rule judges fill against `[thresholds]`; one table
builder feeds the printed section and the TUI page. Design and every measurement behind it:
`docs/plans/2026-10-12-filesystems-design.md` (read it first).

**Tech Stack:** Python 3.11+, pydantic 2 (frozen `DomainModel` / `CaptureModel`), ctypes/fcntl, rich,
rich-click, textual 8.2.8, pytest, bmk (`make test`).

## Global Constraints

- Work in the worktree `.claude/worktrees/filesystems` on branch `filesystems`; never in the main checkout.
- Gate: `make test` (bmk) before every push; also `pytest -m "not local_only"` (CI's selection) from the
  repo root with NO path argument (an explicit `tests` path drops ~329 src doctests).
- No subprocesses, no network, no new runtime dependency.
- No new capture field is required: every one is optional, `None` means "not read", and every committed
  fixture must still validate unchanged. `SCHEMA_VERSION` stays 2.
- Every capture `str` is `DeviceText`; every capture `int` carries `Field(ge=..., le=...)`
  (`tests/test_input_bounds.py::_capture_models` fails on an unbounded int).
- The capture never holds a ZFS pool's config nvlist, only pool name, size and alloc (it carries vdev
  paths with drive serials).
- A refused reading is a `RefusedReading`, never a null; subjects: `statvfs`, `zfs-pools`,
  `volume-space`.
- Only mounts whose device resolves to an inventory disk become rows; NFS, FUSE, tmpfs, autofs are never
  stat-ed.
- Markers: `theme.NOT_READ` (`-`) unread, `theme.NOT_APPLICABLE` (`n/a`) inodes on a dynamic-table
  filesystem; a legend names only markers drawn.
- Findings say "at or above"; a warning exits 1, a hint exits 0; a read-only mount raises none.
- Threshold defaults (user-approved starting values): `filesystem_full_hint_percent = 85`,
  `filesystem_full_warning_percent = 95`, `inodes_full_hint_percent = 85`,
  `inodes_full_warning_percent = 95`.
- TUI keys (user decision 2026-10-12): page 9 = `9,f9` (filesystems), rescan = `r,f10`, quit =
  `q,escape`. Keys `1`-`8` unchanged.
- ZFS ioctls (OpenZFS 2.4.4 source, probe-verified): `ZFS_IOC_POOL_CONFIGS = 0x5A04`,
  `ZFS_IOC_POOL_STATS = 0x5A05`, passed BARE (no `_IOWR`); `zfs_cmd_t` offsets: `zc_name` 0 (4096 bytes),
  `zc_nvlist_dst` 4112, `zc_nvlist_dst_size` 4120; command buffer 32 KiB zeroed; `ENOMEM` (12) means grow
  the destination to the size written back. Reply: native little-endian nvlist; size =
  `vdev_tree.vdev_stats[4]`, alloc = `vdev_tree.vdev_stats[3]`.
- Tests: drive real seams (inject the `ioctl`/`statvfs`/`kernel32` callable the code takes as a parameter);
  never monkeypatch lsdsk internals. A test asserting a refusal asserts its MESSAGE. Mark an `os_*`
  constraint only when the test has one; do not add `os_agnostic`.
- `__all__` on every new module; a constant a test reaches as `module.NAME` goes into that module's
  `__all__` (`tests/test_data_architecture.py`).
- `tests/*.py` is already exempt from PLC0415: no `# noqa: PLC0415` in tests (RUF100 fails it).
- No em-dashes or typographic tells in any file or commit message; commit messages via `git commit -F`.
- `skills/lsdsk/SKILL.md` changes only through `bitranox:meta-skill-writer`.
- A twinned English doc (COMMANDS, CONFIG, FINDINGS, PAGES, README, REPORT, ...) changes together with its
  `de/` twin and `python scripts/translation_manifest.py --refresh <doc>`.

---

### Task 1: Native nvlist decoder (pure)

**Files:**
- Create: `src/lsdsk/adapters/hw/decode/nvlist.py`
- Test: `tests/test_decode_nvlist.py`

**Interfaces:**
- Produces: `NvValue = int | str | tuple[int, ...] | "NvList" | tuple["NvList", ...]`,
  `NvList = Mapping[str, NvValue]`, `decode_nvlist(blob: bytes) -> dict[str, NvValue]`,
  `PoolSpace(NamedTuple): size_bytes: int; alloc_bytes: int`, `pool_space(config: Mapping[str, NvValue]) ->
  PoolSpace`. Both raise `ValueError` with a message on malformed input.
- Constants exported: `MAX_DEPTH = 32`, `MAX_PAIRS = 100_000`, `MAX_STRING_BYTES = 65_536`.

**Out of scope:**
- `src/lsdsk/adapters/hw/decode/usb.py` - the closest walker; copy its bounds discipline, do not change it.
- Any ioctl code - Task 2.

**STOP conditions:**
- a step's verification fails twice after one reasonable fix attempt;
- the native layout in the design doc ("Feasibility probes") disagrees with what a test built from it
  decodes.

- [ ] **Step 1: Write the failing tests**

The test builds buffers with a small encoder that follows the layout in the design doc, so the decoder is
tested against the format rather than against itself.

```python
"""The native nvlist decoder, against buffers built from the OpenZFS 2.4.4 layout."""

from __future__ import annotations

import struct

import pytest

from lsdsk.adapters.hw.decode.nvlist import MAX_DEPTH, PoolSpace, decode_nvlist, pool_space

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
    assert decode_nvlist(blob)["kept"] == 5


def _nested(depth: int) -> bytes:
    inner: list[bytes] = [_u64("leaf", 1)]
    for level in range(depth):
        inner = [_embedded(f"l{level}", inner)]
    return _stream(inner)


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
        ({"vdev_tree": {"vdev_stats": (1, 2, 3)}}, "too short"),
        ({"vdev_tree": {"vdev_stats": (0, 0, 0, 5, 4)}}, "more allocated than"),
    ],
)
def test_a_config_without_usable_stats_is_refused(config: dict[str, NvValue], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        pool_space(config)
```

(import `NvValue` beside `PoolSpace`; no suppression lands.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decode_nvlist.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'lsdsk.adapters.hw.decode.nvlist'`.

- [ ] **Step 3: Write the decoder**

```python
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
_NVLIST_T = 24
_END_MARKER = 4
_NATIVE, _LITTLE_ENDIAN = 0, 1
_UINT64, _STRING, _UINT64_ARRAY, _NVLIST, _NVLIST_ARRAY = 8, 9, 16, 19, 20
_VS_ALLOC, _VS_SPACE = 3, 4

NvValue: TypeAlias = "int | str | tuple[int, ...] | Mapping[str, NvValue] | tuple[Mapping[str, NvValue], ...]"


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

    def need(self, start: int, length: int, what: str) -> None:
        if length < 0 or start + length > len(self.blob):
            msg = f"{what} runs past the end of the buffer"
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
            out[name], position = self._value(kind, value, count, end, depth)

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

    def _value(self, kind: int, value: int, count: int, end: int, depth: int) -> tuple[NvValue, int]:
        if kind == _UINT64:
            self.need(value, 8, "a uint64")
            return struct.unpack_from("<Q", self.blob, value)[0], end
        if kind == _STRING:
            return self._string(value, end), end
        if kind == _UINT64_ARRAY:
            self.need(value, 8 * count, "a uint64 array")
            return struct.unpack_from(f"<{count}Q", self.blob, value), end
        if kind == _NVLIST:
            return self.pair_list(end, depth + 1)
        if kind == _NVLIST_ARRAY:
            return self._list_array(count, end, depth)
        return 0, end

    def _string(self, value: int, end: int) -> str:
        terminator = self.blob.find(b"\0", value, end)
        if terminator < 0:
            msg = "a string runs past its pair"
            raise ValueError(msg)
        if terminator - value > MAX_STRING_BYTES:
            msg = f"a string longer than {MAX_STRING_BYTES} bytes"
            raise ValueError(msg)
        return self.blob[value:terminator].decode("utf-8", errors="replace")

    def _list_array(self, count: int, end: int, depth: int) -> tuple[tuple[Mapping[str, NvValue], ...], int]:
        if count < 0 or count > MAX_PAIRS:
            msg = f"a list array declares {count} members"
            raise ValueError(msg)
        members: list[Mapping[str, NvValue]] = []
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


def pool_space(config: Mapping[str, NvValue]) -> PoolSpace:
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
    stats = tree.get("vdev_stats")
    if not isinstance(stats, tuple) or not all(isinstance(item, int) for item in stats):
        msg = "pool vdev_tree has no vdev_stats"
        raise ValueError(msg)
    if len(stats) <= _VS_SPACE:
        msg = f"pool vdev_stats too short ({len(stats)} values)"
        raise ValueError(msg)
    size, alloc = int(stats[_VS_SPACE]), int(stats[_VS_ALLOC])
    if alloc > size:
        msg = f"pool reports more allocated than its size ({alloc} > {size})"
        raise ValueError(msg)
    return PoolSpace(size_bytes=size, alloc_bytes=alloc)


__all__ = ["MAX_DEPTH", "MAX_PAIRS", "MAX_STRING_BYTES", "NvValue", "PoolSpace", "decode_nvlist", "pool_space"]
```

If pyright strict rejects the string `TypeAlias`, use the form pinned by memory
`reference-the-recursive-typealias-form-pyright-accepts-and-why-its-containers-are-abcs`: an UNQUOTED PEP
604 union with QUOTED self-references, `Mapping`/`tuple` containers only.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_decode_nvlist.py -q` then
`.venv/bin/python -m pyright src/lsdsk/adapters/hw/decode/nvlist.py tests/test_decode_nvlist.py`
Expected: all pass; pyright 0 errors, `filesAnalyzed` 2.

- [ ] **Step 5: Mutation check the bounds**

Delete the `if depth > MAX_DEPTH` block and re-run: the `nested deeper` arm must FAIL. Restore from a copy
taken first (never `git checkout --` with uncommitted work). Repeat for the `need` call in `_value`'s uint64
branch (the `runs past` arm with `_pair("x", _UINT64, b"")` must fail).

- [ ] **Step 6: Commit**

```bash
git add src/lsdsk/adapters/hw/decode/nvlist.py tests/test_decode_nvlist.py
git commit -F <msgfile>   # feat(decode): native nvlist decoder for the ZFS pool stats
```

---

### Task 2: ZFS pool reader and its capture fields (Linux)

**Files:**
- Create: `src/lsdsk/adapters/hw/linux/zfs.py`
- Modify: `src/lsdsk/adapters/hw/linux/capture.py` (new `PoolEntry`, two `LinuxCapture` fields, `__all__`)
- Modify: `src/lsdsk/adapters/hw/linux/reader.py` (`read_system`, near the `"signatures"` key)
- Test: `tests/test_linux_zfs_reader.py`

**Interfaces:**
- Consumes: `decode_nvlist`, `pool_space`, `PoolSpace` (Task 1).
- Produces:
  - `zfs.ZFS_DEVICE = Path("/dev/zfs")`, `zfs.ZFS_IOC_POOL_CONFIGS = 0x5A04`, `zfs.ZFS_IOC_POOL_STATS =
    0x5A05`, `zfs.MAX_REPLY_BYTES = 16 * 1024 * 1024`.
  - `zfs.Ioctl = Callable[[int, int, bytearray], int]` (the `fcntl.ioctl` shape with a mutable buffer).
  - `zfs.read_pools(device: Path = ZFS_DEVICE, *, ioctl: Ioctl | None = None) -> tuple[dict[str, dict[str,
    int]] | None, str | None]`: `(None, None)` when the device is absent; `(None, reason)` when it exists
    but the read failed; `({pool: {"size": int, "alloc": int}}, None)` on success. A pool whose stats fail
    to decode is left out and its reason joins the returned reason text (`"pool <name>: <reason>"`), with
    the others still returned.
  - capture: `PoolEntry(CaptureModel)`: `size: int = Field(ge=0, le=2**64 - 1)`, `alloc: int = Field(ge=0,
    le=2**64 - 1)`. `LinuxCapture.zfs_pools: EntryMap[DeviceText, PoolEntry] | None = None`,
    `LinuxCapture.zfs_pools_error: DeviceText | None = None`.

**Out of scope:**
- `src/lsdsk/adapters/hw/linux/usbfs.py` `_iowr` - ZFS on Linux takes the BARE request number; do not
  encode it.
- `src/lsdsk/adapters/hw/linux/usage.py` - Task 6.

**STOP conditions:**
- `read_system` is not structured as the explorer described (a plain dict with `"signatures"` built from
  `mounts.read_signatures(devnums)`);
- an existing test enumerating `LinuxCapture` fields fails for a reason other than a missing bound.

- [ ] **Step 1: Write the failing tests**

The fake writes a reply into the destination buffer the code put in the command, exactly as the kernel does,
through the pointer at offset 4112 (pattern: `tests/test_linux_sat_passthrough.py::_fake_fcntl`, which uses
`ctypes.memmove`).

```python
"""The ZFS pool reader, driven through its ioctl seam with a fake control device."""

from __future__ import annotations

import ctypes
import errno
import struct
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.linux import zfs
from nvlist_builders import pool_config, pool_names  # Step 1b; bare module name, never tests.<module>

if TYPE_CHECKING:
    from pathlib import Path

_DST, _DST_SIZE = 4112, 4120


class FakeDevice:
    """Answers the two pool ioctls from canned nvlist bytes."""

    def __init__(self, replies: dict[tuple[int, str], bytes], *, fail: int | None = None) -> None:
        self.replies = replies
        self.fail = fail
        self.calls: list[tuple[int, str]] = []

    def __call__(self, _fd: int, request: int, command: bytearray) -> int:
        name = bytes(command[:4096]).split(b"\0", 1)[0].decode()
        self.calls.append((request, name))
        if self.fail is not None:
            raise OSError(self.fail, "refused")
        reply = self.replies[(request, name)]
        pointer, room = struct.unpack_from("<QQ", command, _DST)
        struct.pack_into("<Q", command, _DST_SIZE, len(reply))
        if len(reply) > room:
            raise OSError(errno.ENOMEM, "too small")
        ctypes.memmove(pointer, reply, len(reply))
        return 0


def _device(tmp_path: Path) -> Path:
    node = tmp_path / "zfs"
    node.write_bytes(b"")
    return node


def test_every_pool_is_read_with_its_size_and_allocation(tmp_path: Path) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["rpool", "tank"]),
            (zfs.ZFS_IOC_POOL_STATS, "rpool"): pool_config(alloc=25, space=100),
            (zfs.ZFS_IOC_POOL_STATS, "tank"): pool_config(alloc=0, space=7),
        }
    )
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert error is None
    assert pools == {"rpool": {"size": 100, "alloc": 25}, "tank": {"size": 7, "alloc": 0}}


def test_a_reply_larger_than_the_first_buffer_is_read_after_growing_it(tmp_path: Path) -> None:
    big = pool_config(alloc=1, space=2, padding=300_000)
    fake = FakeDevice({(zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["p"]), (zfs.ZFS_IOC_POOL_STATS, "p"): big})
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert (pools, error) == ({"p": {"size": 2, "alloc": 1}}, None)
    assert fake.calls.count((zfs.ZFS_IOC_POOL_STATS, "p")) == 2


def test_an_absent_control_device_is_not_a_refusal(tmp_path: Path) -> None:
    assert zfs.read_pools(tmp_path / "missing", ioctl=FakeDevice({})) == (None, None)


def test_a_refused_ioctl_is_recorded_with_its_reason(tmp_path: Path) -> None:
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=FakeDevice({}, fail=errno.EPERM))
    assert pools is None
    assert error is not None
    assert "listing the pools" in error
    assert "refused" in error


def test_one_undecodable_pool_does_not_hide_the_others(tmp_path: Path) -> None:
    fake = FakeDevice(
        {
            (zfs.ZFS_IOC_POOL_CONFIGS, ""): pool_names(["good", "bad"]),
            (zfs.ZFS_IOC_POOL_STATS, "good"): pool_config(alloc=1, space=4),
            (zfs.ZFS_IOC_POOL_STATS, "bad"): b"\x00\x01\x00\x00" + b"\x00" * 4,
        }
    )
    pools, error = zfs.read_pools(_device(tmp_path), ioctl=fake)
    assert pools == {"good": {"size": 4, "alloc": 1}}
    assert error is not None
    assert error.startswith("pool bad: ")


def test_a_reply_that_keeps_growing_is_refused_rather_than_followed(tmp_path: Path) -> None:
    class Greedy(FakeDevice):
        def __call__(self, _fd: int, request: int, command: bytearray) -> int:
            struct.pack_into("<Q", command, _DST_SIZE, zfs.MAX_REPLY_BYTES + 1)
            raise OSError(errno.ENOMEM, "too small")

    pools, error = zfs.read_pools(_device(tmp_path), ioctl=Greedy({}))
    assert pools is None
    assert error is not None
    assert "larger than" in error
```

- [ ] **Step 1b: Shared nvlist builders**

Move the `_pair`/`_pairs`/`_nvlist_header`/`_u64`/`_text`/`_u64_array`/`_embedded`/`_stream` helpers out of
`tests/test_decode_nvlist.py` into `tests/nvlist_builders.py` (bare module name - `from tests.<module>`
passes under `python -m pytest` and FAILS under bare pytest in CI), import them from both test files, and add:

```python
def pool_config(*, alloc: int, space: int, padding: int = 0) -> bytes:
    """A POOL_STATS reply: name, an optional padding string, and the root vdev's stats."""
    items = [_text("name", "pool")]
    if padding:
        items.append(_text("pad", "x" * min(padding, 60_000)))
        items.extend(_text(f"pad{n}", "x" * 60_000) for n in range(padding // 60_000))
    stats = [0, 7, 0, alloc, space, space, *([0] * 42)]
    items.append(_embedded("vdev_tree", [_text("type", "root"), _u64_array("vdev_stats", stats)]))
    return _stream(items)


def pool_names(names: list[str]) -> bytes:
    """A POOL_CONFIGS reply: one embedded config per pool, keyed by its name."""
    return _stream([_embedded(name, [_text("name", name)]) for name in names])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_linux_zfs_reader.py -q`
Expected: collection ERROR, `cannot import name 'zfs'`.

- [ ] **Step 3: Write the reader**

```python
"""Read every imported ZFS pool's size and allocation from the control device.

Two ioctls, both answered to an unprivileged caller (OpenZFS 2.4.4:
ZFS_IOC_POOL_CONFIGS has policy zfs_secpolicy_none, ZFS_IOC_POOL_STATS has
zfs_secpolicy_read, which asks only for the global zone or a visible pool).
Linux passes the request number through as it is, with no _IOWR encoding.

Only the pool name, size and allocation leave this module. The config a pool
answers with also names every vdev by path, often a by-id name that embeds a
drive serial, so it is decoded here and dropped.
"""

from __future__ import annotations

import ctypes
import errno
import os
import struct
from collections.abc import Callable
from pathlib import Path

from lsdsk.adapters.hw.decode.nvlist import decode_nvlist, pool_space

ZFS_DEVICE = Path("/dev/zfs")
ZFS_IOC_POOL_CONFIGS = 0x5A04
ZFS_IOC_POOL_STATS = 0x5A05
#: Largest reply followed. A config grows with the vdev count; this is far past
#: any real pool and stops a kernel answer from sizing our allocation.
MAX_REPLY_BYTES = 16 * 1024 * 1024

#: Larger than sizeof(zfs_cmd_t), which the kernel copies in and out whole.
_COMMAND_BYTES = 32 * 1024
_NAME_BYTES = 4096
_DST, _DST_SIZE = 4112, 4120
_FIRST_REPLY_BYTES = 256 * 1024
_ATTEMPTS = 6

Ioctl = Callable[[int, int, bytearray], int]


def _system_ioctl(fd: int, request: int, command: bytearray) -> int:
    import fcntl  # noqa: PLC0415 - POSIX-only module; this file must still import on Windows

    return fcntl.ioctl(fd, request, command, True)


def _ask(fd: int, request: int, name: str, ioctl: Ioctl) -> bytes:
    """Issue one ioctl, growing the reply buffer while the kernel asks for more."""
    room = _FIRST_REPLY_BYTES
    for _ in range(_ATTEMPTS):
        command = bytearray(_COMMAND_BYTES)
        encoded = name.encode()[: _NAME_BYTES - 1]
        command[: len(encoded)] = encoded
        reply = ctypes.create_string_buffer(room)
        struct.pack_into("<QQ", command, _DST, ctypes.addressof(reply), room)
        try:
            ioctl(fd, request, command)
        except OSError as error:
            if error.errno != errno.ENOMEM:
                raise
            room = struct.unpack_from("<Q", command, _DST_SIZE)[0]
            if room > MAX_REPLY_BYTES:
                msg = f"the reply is larger than {MAX_REPLY_BYTES} bytes ({room})"
                raise ValueError(msg) from error
            continue
        written = struct.unpack_from("<Q", command, _DST_SIZE)[0]
        return reply.raw[: min(written, room)]
    msg = "the reply kept growing"
    raise ValueError(msg)


def _pool_names(fd: int, ioctl: Ioctl) -> list[str]:
    return sorted(decode_nvlist(_ask(fd, ZFS_IOC_POOL_CONFIGS, "", ioctl)))


def _space_of(fd: int, pool: str, ioctl: Ioctl) -> dict[str, int]:
    space = pool_space(decode_nvlist(_ask(fd, ZFS_IOC_POOL_STATS, pool, ioctl)))
    return {"size": space.size_bytes, "alloc": space.alloc_bytes}


def read_pools(
    device: Path = ZFS_DEVICE, *, ioctl: Ioctl | None = None
) -> tuple[dict[str, dict[str, int]] | None, str | None]:
    """Read every imported pool's size and allocation.

    Args:
        device: The ZFS control device.
        ioctl: The call that reaches it; the system's ``fcntl.ioctl`` by default.

    Returns:
        ``(None, None)`` when there is no control device (no ZFS here, or a
        container that was not given it); ``(None, reason)`` when the pools
        could not be listed; otherwise the pools by name, with the reason any
        single pool could not be read.
    """
    if not device.exists():
        return None, None
    call = ioctl or _system_ioctl
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError as error:
        return None, f"could not open {device} ({error.strerror})"
    try:
        try:
            names = _pool_names(fd, call)
        except (OSError, ValueError) as error:
            return None, f"listing the pools failed: {error}"
        pools: dict[str, dict[str, int]] = {}
        problems: list[str] = []
        for name in names:
            try:
                pools[name] = _space_of(fd, name, call)
            except (OSError, ValueError) as error:
                problems.append(f"pool {name}: {error}")
        return pools, "; ".join(problems) or None
    finally:
        os.close(fd)


__all__ = [
    "MAX_REPLY_BYTES",
    "ZFS_DEVICE",
    "ZFS_IOC_POOL_CONFIGS",
    "ZFS_IOC_POOL_STATS",
    "Ioctl",
    "read_pools",
]
```

- [ ] **Step 4: Capture fields**

In `src/lsdsk/adapters/hw/linux/capture.py`, after `FilesystemSignature`:

```python
class PoolEntry(CaptureModel, frozen=True):
    """One imported ZFS pool's size and allocation, as ``zpool list`` prints them.

    Attributes:
        size: The root vdev's capacity in bytes.
        alloc: Bytes allocated on it.
    """

    size: int = Field(ge=0, le=_UINT64_MAX)
    alloc: int = Field(ge=0, le=_UINT64_MAX)
```

(use the module's existing uint64 ceiling constant if one exists; otherwise define
`_UINT64_MAX = 2**64 - 1` beside the other bound constants), and on `LinuxCapture`, after `signatures`:

```python
    zfs_pools: EntryMap[DeviceText, PoolEntry] | None = None
    zfs_pools_error: DeviceText | None = None
```

with docstring Attributes lines: "`zfs_pools`: every imported pool by name. `None` when there was no ZFS
control device, or in a capture taken before this field existed; an empty map is a reading that found no
pool." / "`zfs_pools_error`: why the pools could not be read, or why one of them could not." Add
`PoolEntry` to `__all__`.

- [ ] **Step 5: Wire into `read_system`**

In `reader.py` `read_system()`, beside the `"signatures"` entry:

```python
    pools, pools_error = zfs.read_pools()
    reading["zfs_pools"] = pools
    if pools_error is not None:
        reading["zfs_pools_error"] = pools_error
```

(adapt the variable name to whatever dict `read_system` builds; `from lsdsk.adapters.hw.linux import zfs` at
module top).

- [ ] **Step 6: Run the tests and the fixture guards**

Run: `.venv/bin/python -m pytest tests/test_linux_zfs_reader.py tests/test_decode_nvlist.py
tests/test_input_bounds.py tests/test_linux_mounts_reader.py -q`
Expected: all pass (every committed fixture still validates with the new fields `None`).

- [ ] **Step 7: Commit** - `feat(linux): read every ZFS pool's size and allocation from /dev/zfs`

---

### Task 3: Linux statvfs reader and mount read-only flag

**Files:**
- Modify: `src/lsdsk/adapters/hw/linux/mounts.py` (`_parse_mountinfo_line`, new `read_space`)
- Modify: `src/lsdsk/adapters/hw/linux/capture.py` (`MountEntry.read_only`, new `SpaceEntry`,
  `LinuxCapture.filesystem_space`)
- Modify: `src/lsdsk/adapters/hw/linux/reader.py` (`read_system`)
- Test: `tests/test_linux_space_reader.py`; extend `tests/test_linux_mounts_reader.py`

**Interfaces:**
- Produces:
  - `MountEntry.read_only: bool | None = None` - True when mountinfo field 6 (per-mount options) OR the
    super options after the separator start with `ro` (`ro` or `ro,...`).
  - `mounts.StatvfsResult(Protocol)`: `f_frsize`, `f_blocks`, `f_bfree`, `f_bavail`, `f_files`,
    `f_ffree`, all `int`.
  - `mounts.read_space(rows: Sequence[Mapping[str, str]], *, statvfs: Callable[[str],
    StatvfsResult] = os.statvfs) -> dict[str, dict[str, int | str]]` - one entry per `dev`, keyed by `dev`,
    for every row whose `fstype` is not `zfs`; stat-s the FIRST mountpoint seen for that `dev`. An
    `OSError` becomes `{"error": "<strerror>"}`.
  - capture: `SpaceEntry(CaptureModel)`: `frsize`, `blocks`, `bfree`, `bavail`, `files`, `ffree`, each
    `int | None = Field(default=None, ge=0, le=_UINT64_MAX)`, plus `error: DeviceText | None = None`.
    `LinuxCapture.filesystem_space: EntryMap[DeviceText, SpaceEntry] | None = None`.

**Out of scope:**
- `read_mounts`' filtering of major-0 rows without a source device - it is what keeps NFS/FUSE/tmpfs out,
  and therefore unstat-ed.
- The mountinfo `root` field - deliberately not read (a btrfs `/@` root is a real mount).

**STOP conditions:**
- `read_mounts` rows no longer carry `dev`/`mountpoint`/`fstype`;
- statvfs is called on a row the test marks as NFS/tmpfs.

- [ ] **Step 1: Failing tests**

`tests/test_linux_space_reader.py`:

```python
"""statvfs readings per mounted filesystem, through the reader's statvfs seam."""

from __future__ import annotations

from dataclasses import dataclass

from lsdsk.adapters.hw.linux.mounts import read_space


@dataclass(frozen=True)
class Stat:
    f_frsize: int = 4096
    f_blocks: int = 1000
    f_bfree: int = 400
    f_bavail: int = 350
    f_files: int = 64
    f_ffree: int = 16


def test_one_reading_per_filesystem_from_its_first_mountpoint() -> None:
    asked: list[str] = []

    def statvfs(path: str) -> Stat:
        asked.append(path)
        return Stat()

    rows = [
        {"dev": "8:1", "mountpoint": "/", "fstype": "ext4"},
        {"dev": "8:1", "mountpoint": "/srv/bind", "fstype": "ext4"},
        {"dev": "8:2", "mountpoint": "/boot/efi", "fstype": "vfat"},
    ]
    space = read_space(rows, statvfs=statvfs)
    assert asked == ["/", "/boot/efi"]
    assert space["8:1"] == {"frsize": 4096, "blocks": 1000, "bfree": 400, "bavail": 350, "files": 64, "ffree": 16}


def test_a_zfs_dataset_is_never_stat_ed() -> None:
    def statvfs(path: str) -> Stat:
        raise AssertionError(path)

    assert read_space([{"dev": "0:51", "mountpoint": "/", "fstype": "zfs", "source": "rpool"}], statvfs=statvfs) == {}


def test_a_refused_statvfs_is_recorded_with_its_reason() -> None:
    def statvfs(_path: str) -> Stat:
        raise PermissionError(13, "Permission denied")

    assert read_space([{"dev": "8:3", "mountpoint": "/root/x", "fstype": "xfs"}], statvfs=statvfs) == {
        "8:3": {"error": "Permission denied"}
    }
```

In `tests/test_linux_mounts_reader.py`, add (reuse its `_Resolver` and `read_mounts` path pattern):

```python
def test_a_read_only_mount_is_flagged_from_either_option_field(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "30 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
        "31 1 7:0 / /snap/core ro,nodev - squashfs /dev/loop0 ro\n"
        "32 1 8:2 / /mnt/ro rw,relatime - ext4 /dev/sda2 ro,errors=remount-ro\n",
        encoding="utf-8",
    )
    rows = read_mounts(mountinfo, device_number=_Resolver({"/dev/sda1": "8:1", "/dev/loop0": "7:0", "/dev/sda2": "8:2"}))
    assert [row.get("read_only") for row in rows] == ["false", "true", "true"]
    typed = [MountEntry.model_validate(row) for row in rows]
    assert [mount.read_only for mount in typed] == [False, True, True]
```

(`read_mounts` rows are `dict[str, str]`, so the flag travels as `"true"`/`"false"`, which pydantic validates
into `bool`. Adjust `_Resolver`'s constructor to its real signature.)

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_linux_space_reader.py
  tests/test_linux_mounts_reader.py -q` - expect ImportError / assertion failures.

- [ ] **Step 3: Implement**

In `_parse_mountinfo_line`, after `row` is built:

```python
    # Field 6 carries the per-mount options and the field after the source the
    # superblock's; either one being read-only makes this mount read-only.
    per_mount = left_fields[5] if len(left_fields) > _OPTIONS_FIELD else ""
    super_options = right_fields[2] if len(right_fields) > _SUPER_OPTIONS_FIELD else ""
    row["read_only"] = "true" if _starts_read_only(per_mount) or _starts_read_only(super_options) else "false"
```

with module constants `_OPTIONS_FIELD = 5`, `_SUPER_OPTIONS_FIELD = 2` and

```python
def _starts_read_only(options: str) -> bool:
    """Whether a comma-separated option list says ``ro``."""
    return options.split(",", 1)[0] == "ro"
```

`read_space`:

```python
class StatvfsResult(Protocol):
    """The six figures read from ``os.statvfs``."""

    @property
    def f_frsize(self) -> int: ...
    @property
    def f_blocks(self) -> int: ...
    @property
    def f_bfree(self) -> int: ...
    @property
    def f_bavail(self) -> int: ...
    @property
    def f_files(self) -> int: ...
    @property
    def f_ffree(self) -> int: ...


def read_space(
    rows: Sequence[Mapping[str, str]], *, statvfs: Callable[[str], StatvfsResult] = os.statvfs
) -> dict[str, dict[str, int | str]]:
    """Read each mounted filesystem's size, free space and inodes once.

    Only the rows ``read_mounts`` kept are asked, which already excludes every
    mount no disk can sit behind (tmpfs, NFS, FUSE), so a hung network mount is
    never stat-ed. ZFS datasets are skipped: a dataset's statvfs reports its own
    data plus the pool's free space, never the pool's fill.

    Args:
        rows: What :func:`read_mounts` returned.
        statvfs: The call that reads one mountpoint.

    Returns:
        One reading per filesystem device, keyed by its ``maj:min``.
    """
    readings: dict[str, dict[str, int | str]] = {}
    for row in rows:
        dev = row.get("dev", "")
        if not dev or dev in readings or row.get("fstype") == _ZFS:
            continue
        try:
            result = statvfs(row["mountpoint"])
        except OSError as error:
            readings[dev] = {"error": error.strerror or str(error)}
            continue
        readings[dev] = {
            "frsize": result.f_frsize,
            "blocks": result.f_blocks,
            "bfree": result.f_bfree,
            "bavail": result.f_bavail,
            "files": result.f_files,
            "ffree": result.f_ffree,
        }
    return readings
```

On Windows `os.statvfs` does not exist: default the parameter to a module-level `_system_statvfs` that
calls `os.statvfs` inside, so the module still imports there. Add `StatvfsResult` and `read_space` to
`__all__`. Capture: `SpaceEntry` as in Interfaces; `MountEntry.read_only: bool | None = None` with its
docstring line. `read_system`: `reading["filesystem_space"] = None if mounts_rows is None else
mounts.read_space(mounts_rows)`.

- [ ] **Step 4: Run** the two test files plus `tests/test_input_bounds.py`; expect all pass.
- [ ] **Step 5: Commit** - `feat(linux): read statvfs per mounted filesystem and the read-only flag`

---

### Task 4: Windows volume space reading

**Files:**
- Modify: `src/lsdsk/adapters/hw/windows/winapi.py` (bindings after `GetDriveTypeW` in
  `configure_prototypes`; constant `FILE_READ_ONLY_VOLUME = 0x00080000` in the constants block and
  `__all__`)
- Modify: `src/lsdsk/adapters/hw/windows/volumes.py` (`_read_one_volume`, new `_volume_space`)
- Modify: `src/lsdsk/adapters/hw/windows/capture.py` (`VolumeEntry` fields)
- Test: extend `tests/test_windows_volume_reader.py` (`FakeVolume`, `FakeVolumeKernel`)

**Interfaces:**
- Produces: `VolumeEntry.total_bytes`, `free_bytes`, `available_bytes`: `int | None = Field(default=None,
  ge=0, le=_UINT64_MAX)`; `VolumeEntry.fs_type: DeviceText | None = None`; `VolumeEntry.read_only: bool |
  None = None`; `VolumeEntry.space_error: DeviceText | None = None`.
- `volumes._volume_space(kernel32: api.WinLibrary, path: str, *, last_error: Callable[[], int]) ->
  dict[str, Any]`.

**Out of scope:**
- `windows/reader.py` `read_volumes_section` ordinal keys - unchanged.
- `ctypes.wintypes` integer types - never use them in `winapi.py` (see CLAUDE.md Code Quality); the out
  parameters here are `c_uint64` and `api.DWORD`.

**STOP conditions:**
- `FILE_READ_ONLY_VOLUME` differs from 0x00080000 in the Windows SDK `winnt.h` (check it; pin it in
  `tests/test_winapi_layout.py` or a sibling SDK-constant test, written from the SDK);
- the per-volume error text cannot take the injected `last_error` without widening `_read_one_volume`'s
  signature beyond two parameters plus keywords.

- [ ] **Step 1: Failing tests** - add to `FakeVolume`: `space: tuple[int, int, int] | None = (100, 40, 30)`
  (total, free, available; `None` = the call fails), `fs_type: str | None = "NTFS"`, `flags: int = 0`.
  Add `GetDiskFreeSpaceExW(path, available, total, free)` and `GetVolumeInformationW(path, _name, _name_len,
  _serial, _max_component, flags, fs_name, fs_len)` to `FakeVolumeKernel`, writing the out parameters with
  the file's existing `getattr(out, "_obj").value = ...` idiom and setting `self.error` on failure. Tests:

```python
def test_a_volume_with_a_path_carries_its_space_type_and_read_only_flag() -> None:
    fake = FakeVolumeKernel(volumes={"\\\\?\\Volume{a}\\": FakeVolume(paths=("C:\\",), flags=0x00080000)})
    entry = _read(fake)["\\\\?\\Volume{a}\\"]
    assert (entry["total_bytes"], entry["free_bytes"], entry["available_bytes"]) == (100, 40, 30)
    assert entry["fs_type"] == "NTFS"
    assert entry["read_only"] is True


def test_a_refused_space_call_is_recorded_with_its_error() -> None:
    fake = FakeVolumeKernel(volumes={"\\\\?\\Volume{a}\\": FakeVolume(paths=("D:\\",), space=None)}, error=21)
    entry = _read(fake)["\\\\?\\Volume{a}\\"]
    assert entry["space_error"] == "could not read the volume's free space (error 21)"
    assert "total_bytes" not in entry


def test_a_volume_without_a_path_is_never_asked_for_its_space() -> None:
    fake = FakeVolumeKernel(volumes={"\\\\?\\Volume{a}\\": FakeVolume(paths=())})
    entry = _read(fake)["\\\\?\\Volume{a}\\"]
    assert "space_error" not in entry
    assert "total_bytes" not in entry
```

(adapt `FakeVolumeKernel`'s constructor to its real fields; the error arm must read the INJECTED
`last_error`, so pass it through `_read_one_volume` - memory
`reference-a-fake-win32-library-cannot-answer-getlasterror-inject-the-getter`.)

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_windows_volume_reader.py -q` - expect failures.
- [ ] **Step 3: Implement**

`winapi.configure_prototypes`, after `GetDriveTypeW`:

```python
    kernel32.GetDiskFreeSpaceExW.restype = BOOL
    kernel32.GetDiskFreeSpaceExW.argtypes = (LPCWSTR, POINTER(c_uint64), POINTER(c_uint64), POINTER(c_uint64))
    kernel32.GetVolumeInformationW.restype = BOOL
    kernel32.GetVolumeInformationW.argtypes = (
        LPCWSTR, LPWSTR, DWORD, POINTER(DWORD), POINTER(DWORD), POINTER(DWORD), LPWSTR, DWORD,
    )
```

`volumes.py`:

```python
_FS_NAME_CHARS = 64


def _volume_space(kernel32: api.WinLibrary, path: str, *, last_error: Callable[[], int]) -> dict[str, Any]:
    """Read one volume's capacity, free space, filesystem name and read-only flag.

    Asked of the volume's first mount path, the same rule as GetDriveTypeW:
    measured unelevated on Windows 11, both calls answer there.
    """
    available, total, free = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
    entry: dict[str, Any] = {}
    if kernel32.GetDiskFreeSpaceExW(path, ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
        entry.update(total_bytes=total.value, free_bytes=free.value, available_bytes=available.value)
    else:
        entry["space_error"] = f"could not read the volume's free space (error {last_error()})"
    flags = api.DWORD()
    fs_name = ctypes.create_unicode_buffer(_FS_NAME_CHARS)
    if kernel32.GetVolumeInformationW(path, None, 0, None, None, ctypes.byref(flags), fs_name, _FS_NAME_CHARS):
        entry["fs_type"] = fs_name.value
        entry["read_only"] = bool(flags.value & api.FILE_READ_ONLY_VOLUME)
    return entry
```

In `_read_one_volume(kernel32, volume, *, last_error=api.last_error)`, inside `if paths:` after
`drive_type`: `entry.update(_volume_space(kernel32, paths[0], last_error=last_error))`; pass `last_error`
from `read_volumes`. Capture fields as in Interfaces, each with a docstring line saying absent from a
capture taken before this field existed.

- [ ] **Step 4: Run** the volume reader tests, `tests/test_windows_usage.py`, `tests/test_input_bounds.py`,
  `tests/test_winapi_layout.py` - all pass.
- [ ] **Step 5: Commit** - `feat(windows): read each volume's space, filesystem name and read-only flag`

---

### Task 5: Domain model

**Files:**
- Modify: `src/lsdsk/domain/enums.py` (`FilesystemKind`, `InodeTable`, `__all__`)
- Modify: `src/lsdsk/domain/models.py` (`Filesystem`, `Inventory.filesystems`,
  `Inventory.unread_filesystems`, `__all__`)
- Test: `tests/test_filesystem_model.py`

**Interfaces:**
- Produces:
  - `FilesystemKind(StrEnum)`: `MOUNT = "mount"`, `POOL = "pool"`, `VOLUME = "volume"`.
  - `InodeTable(StrEnum)`: `FIXED = "fixed"`, `DYNAMIC = "dynamic"`.
  - `models.FIXED_INODE_TABLE_TYPES: frozenset[str] = frozenset({"ext2", "ext3", "ext4"})`.
  - `Filesystem(DomainModel, frozen=True)`: `kind: FilesystemKind`, `name: DeviceText`, `other_mounts:
    tuple[DeviceText, ...] = ()`, `fs_type: DeviceText = ""`, `disks: tuple[DeviceText, ...] = ()`,
    `size_bytes: int | None = None`, `used_bytes: int | None = None`, `free_bytes: int | None = None`,
    `inodes_total: int | None = None`, `inodes_free: int | None = None`, `read_only: bool = False`,
    `readings_refused: tuple[RefusedReading, ...] = ()`; properties `inode_table -> InodeTable`
    (`FIXED` iff `fs_type in FIXED_INODE_TABLE_TYPES`), `used_percent -> int | None`,
    `inodes_used_percent -> int | None` (None unless FIXED and both inode figures present and total > 0).
  - Percent rule (agrees with `df`): `ceil(100 * used / (used + free))` when both known and the sum > 0.
  - `Inventory.filesystems: tuple[Filesystem, ...] | None = None` (None = not read, () = read, none found);
    `Inventory.unread_filesystems: tuple[RefusedReading, ...] = ()`.

**Out of scope:**
- `DiskUse` / `DiskUsage` - unchanged; the two sections share a resolution, not a model.

**STOP conditions:** `DomainModel` no longer takes `frozen=True` as a class keyword.

- [ ] **Step 1: Failing tests**

```python
"""The Filesystem value: how full it is, the way df computes it."""

from __future__ import annotations

import pytest

from lsdsk.domain.enums import FilesystemKind, InodeTable
from lsdsk.domain.models import Filesystem, Inventory


def _fs(**fields: object) -> Filesystem:
    return Filesystem.model_validate({"kind": FilesystemKind.MOUNT, "name": "/", **fields})


@pytest.mark.parametrize(
    ("used", "free", "expected"),
    [(0, 100, 0), (1, 199, 1), (50, 50, 50), (951, 49, 96), (100, 0, 100), (None, 5, None), (0, 0, None)],
)
def test_used_percent_rounds_up_the_way_df_does(used: int | None, free: int | None, expected: int | None) -> None:
    assert _fs(used_bytes=used, free_bytes=free).used_percent == expected


def test_only_a_fixed_inode_table_reports_inode_usage() -> None:
    ext4 = _fs(fs_type="ext4", inodes_total=200, inodes_free=50)
    zfs = _fs(fs_type="zfs", inodes_total=200, inodes_free=50)
    assert (ext4.inode_table, ext4.inodes_used_percent) == (InodeTable.FIXED, 75)
    assert (zfs.inode_table, zfs.inodes_used_percent) == (InodeTable.DYNAMIC, None)


def test_an_inventory_older_than_the_field_has_not_read_filesystems() -> None:
    assert Inventory(hostname="h").filesystems is None
```

(construct `Inventory` with whatever its required fields are, using the same helper `tests/test_diagnostics.py`
uses.)

- [ ] **Step 2: Run** - expect ImportError.
- [ ] **Step 3: Implement** the enums (docstrings with Example blocks, like `UseKind`), the model and the two
  `Inventory` fields with docstring Attributes lines, per Interfaces. `used_percent`:

```python
    @property
    def used_percent(self) -> int | None:
        """How full it is, rounded UP so it agrees with ``df`` on the same mount.

        Free means free to an ordinary user, so ext4's reserved blocks count as
        unavailable - which is what ``df`` prints too.
        """
        if self.used_bytes is None or self.free_bytes is None:
            return None
        total = self.used_bytes + self.free_bytes
        return None if total <= 0 else math.ceil(100 * self.used_bytes / total)
```

- [ ] **Step 4: Run** the new tests, `tests/test_data_architecture.py`, `tests/test_exported_docstrings.py`,
  `tests/test_domain_copies.py` - all pass.
- [ ] **Step 5: Commit** - `feat(domain): the Filesystem value and the inventory's filesystems`

---

### Task 6: Linux builder - mount and pool to disks, and the filesystem rows

**Files:**
- Modify: `src/lsdsk/adapters/hw/linux/usage.py` (public `disks_by_device`, `disks_by_pool`)
- Create: `src/lsdsk/adapters/hw/linux/filesystems.py` (`build_filesystems`)
- Modify: `src/lsdsk/adapters/hw/linux/builder.py` (`build_inventory`, at the `resolve_usage` call)
- Test: `tests/test_linux_filesystems.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 5.
- Produces:
  - `usage.disks_by_device(capture: LinuxCapture) -> dict[str, frozenset[str]]` - every `maj:min` a mount
    could name (a disk's own, its partitions', and every stacked device reached through the holders
    closure, with their partitions) to the disk nodes it lives on. Virtual disks excluded.
  - `usage.disks_by_pool(capture: LinuxCapture) -> dict[str, frozenset[str]]` - pool name to member disks,
    with the same partition-beats-whole-disk rule `_disk_usage` applies.
  - `filesystems.build_filesystems(capture: LinuxCapture, environment: Environment) -> tuple[tuple[Filesystem,
    ...] | None, tuple[RefusedReading, ...]]`.

**Out of scope:**
- `resolve_usage` - unchanged; both new functions reuse its private helpers (`_closure`, `_devs_of`,
  `_signature_of`) so the two sections cannot disagree about which disk holds a mount.

**STOP conditions:** a disk the `used by` cell names for a mount is missing from `disks_by_device` for the
same capture (Step 1's agreement test fails after the implementation).

- [ ] **Step 1: Failing tests** (helpers copied from `tests/test_linux_usage.py`: `_capture`, `_disk`,
  `_mount`, `BARE_METAL`)

```python
def test_a_filesystem_on_lvm_over_two_disks_names_both() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1", "holders": ["dm-0"]}}),
         "sdb": _disk("8:16", {"sdb1": {"dev": "8:17", "holders": ["dm-0"]}})},
        stacked={"dm-0": {"dev": "253:0", "dm_name": "vg0-var", "dm_uuid": "LVM-"}},
        mounts=[_mount("253:0", "/var")],
        filesystem_space={"253:0": {"frsize": 4096, "blocks": 100, "bfree": 10, "bavail": 5, "files": 9, "ffree": 3}},
    )
    rows, refused = build_filesystems(capture, BARE_METAL)
    assert refused == ()
    assert rows is not None
    (var,) = rows
    assert (var.name, var.disks, var.used_bytes, var.free_bytes) == ("/var", ("sda", "sdb"), 90 * 4096, 5 * 4096)


def test_bind_mounts_of_one_filesystem_are_one_row() -> None:
    capture = _capture({"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
                       mounts=[_mount("8:1", "/"), _mount("8:1", "/srv/a"), _mount("8:1", "/srv/b")],
                       filesystem_space={})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(row.name, row.other_mounts) for row in rows] == [("/", ("/srv/a", "/srv/b"))]


def test_a_pool_is_one_row_with_its_members_and_no_datasets() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1"}}), "sdb": _disk("8:16", {"sdb1": {"dev": "8:17"}})},
        signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"},
                    "8:17": {"fs_type": "zfs_member", "fs_label": "tank"}},
        mounts=[_mount("0:50", "/tank", "zfs", "tank"), _mount("0:51", "/tank/a", "zfs", "tank")],
        zfs_pools={"tank": {"size": 1000, "alloc": 960}},
        filesystem_space={},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    (pool,) = rows
    assert (pool.kind, pool.name, pool.disks, pool.other_mounts, pool.used_percent) == (
        FilesystemKind.POOL, "tank", ("sda", "sdb"), (), 96)


def test_a_missing_control_device_with_pool_members_is_a_refusal() -> None:
    capture = _capture({"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})},
                       signatures={"8:1": {"fs_type": "zfs_member", "fs_label": "tank"}},
                       zfs_pools=None, filesystem_space={})
    _rows, refused = build_filesystems(capture, BARE_METAL)
    assert [(r.reading, r.reason) for r in refused] == [("zfs-pools", "the ZFS control device was not available")]


def test_no_control_device_and_no_pool_members_is_not_a_refusal() -> None:
    capture = _capture({"sda": _disk("8:0")}, zfs_pools=None, filesystem_space={})
    assert build_filesystems(capture, BARE_METAL)[1] == ()


def test_a_refused_statvfs_is_carried_on_its_row() -> None:
    capture = _capture({"sda": _disk("8:0", {"sda1": {"dev": "8:1"}})}, mounts=[_mount("8:1", "/data")],
                       filesystem_space={"8:1": {"error": "Permission denied"}})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(r.reading, r.reason) for r in rows[0].readings_refused] == [("statvfs", "Permission denied")]
    assert rows[0].used_bytes is None


def test_a_mount_on_no_inventory_disk_is_not_a_row() -> None:
    capture = _capture({"sda": _disk("8:0")}, mounts=[_mount("7:0", "/snap/x", "squashfs")], filesystem_space={})
    assert build_filesystems(capture, BARE_METAL)[0] == ()


def test_a_container_and_an_unread_mount_table_read_nothing() -> None:
    assert build_filesystems(_capture({}), Environment.CONTAINER) == (None, ())
    assert build_filesystems(_capture({}, mounts=None), BARE_METAL) == (None, ())


def test_every_mount_the_used_by_cell_names_resolves_to_the_same_disk() -> None:
    capture = _capture(
        {"sda": _disk("8:0", {"sda1": {"dev": "8:1", "holders": ["dm-0"]}})},
        stacked={"dm-0": {"dev": "253:0", "dm_name": "cr", "dm_uuid": "CRYPT-",
                          "holders": ["dm-1"]}, "dm-1": {"dev": "253:1", "dm_name": "vg-root", "dm_uuid": "LVM-"}},
        mounts=[_mount("253:1", "/")],
    )
    usage = resolve_usage(capture, BARE_METAL)
    by_device = disks_by_device(capture)
    for node, disk_usage in usage.items():
        assert disk_usage is not None
        for use in disk_usage.uses:
            for mountpoint in use.mounts:
                (mount,) = [m for m in capture.mounts or () if m.mountpoint == mountpoint]
                assert node in by_device[mount.dev]
```

- [ ] **Step 2: Run** - expect ImportError.
- [ ] **Step 3: Implement**

`usage.py`:

```python
def _member_devs(block: BlockEntry, stacked: Mapping[str, StackedEntry]) -> set[str]:
    """Every maj:min a mount or a swap could name that lives on one disk."""
    leaves = [(block.dev, block.holders), *((p.dev, p.holders) for p in (block.partitions or {}).values())]
    devs = {dev for dev, _holders in leaves if dev}
    for _dev, holders in leaves:
        for holder in holders:
            for member in (holder, *_closure(holder, stacked)):
                devs.update(_devs_of(stacked.get(member, StackedEntry())))
    return devs


def disks_by_device(capture: LinuxCapture) -> dict[str, frozenset[str]]:
    """Which disks each device number lives on, the inverse of what a disk is used by.

    Built from the same holder closure as :func:`resolve_usage`, so a mount and
    the ``used by`` cell can never disagree about which disk holds it.
    """
    found: dict[str, set[str]] = {}
    for node, block in capture.block.items():
        if block.virtual:
            continue
        for dev in _member_devs(block, capture.stacked):
            found.setdefault(dev, set()).add(node)
    return {dev: frozenset(nodes) for dev, nodes in found.items()}


def disks_by_pool(capture: LinuxCapture) -> dict[str, frozenset[str]]:
    """Which disks each ZFS pool's members sit on, partition evidence first."""
    found: dict[str, set[str]] = {}
    for node, block in capture.block.items():
        if block.virtual:
            continue
        partitions = tuple((block.partitions or {}).values())
        signatures = [_signature_of(partition.dev, capture) for partition in partitions]
        if not any(signature is not None for signature in signatures):
            signatures = [_signature_of(block.dev, capture)]
        for signature in signatures:
            if signature is not None and signature.fs_type == _ZFS_MEMBER and signature.fs_label:
                found.setdefault(signature.fs_label, set()).add(node)
    return {pool: frozenset(nodes) for pool, nodes in found.items()}
```

and add both to `__all__`.

`filesystems.py` (one function per concern, each under the complexity limits):

```python
"""Turn a Linux reading into the filesystems that live on its disks."""

from __future__ import annotations

from lsdsk.adapters.hw.linux.capture import LinuxCapture, MountEntry, SpaceEntry
from lsdsk.adapters.hw.linux.usage import disks_by_device, disks_by_pool
from lsdsk.adapters.hw.refusals import refusals_of
from lsdsk.domain.enums import Environment, FilesystemKind
from lsdsk.domain.models import FIXED_INODE_TABLE_TYPES, Filesystem, RefusedReading
from lsdsk.domain.disk_name import disk_sort_key  # the drive order; use the module's real name

_ZFS = "zfs"
_NO_CONTROL_DEVICE = "the ZFS control device was not available"


def _grouped(mounts: tuple[MountEntry, ...]) -> dict[str, list[MountEntry]]:
    """Every non-ZFS mount, grouped by the filesystem's device number in first-seen order."""
    groups: dict[str, list[MountEntry]] = {}
    for mount in mounts:
        if mount.fstype != _ZFS:
            groups.setdefault(mount.dev, []).append(mount)
    return groups


def _figures(space: SpaceEntry | None, fs_type: str) -> dict[str, int | None]:
    if space is None or space.error is not None or space.frsize is None:
        return {}
    blocks, bfree, bavail = space.blocks or 0, space.bfree or 0, space.bavail or 0
    figures: dict[str, int | None] = {
        "size_bytes": blocks * space.frsize,
        "used_bytes": max(blocks - bfree, 0) * space.frsize,
        "free_bytes": bavail * space.frsize,
    }
    if fs_type in FIXED_INODE_TABLE_TYPES:
        figures.update(inodes_total=space.files, inodes_free=space.ffree)
    return figures


def _mount_row(group: list[MountEntry], disks: frozenset[str], space: SpaceEntry | None) -> Filesystem:
    first = group[0]
    mountpoints = list(dict.fromkeys(mount.mountpoint for mount in group))
    return Filesystem(
        kind=FilesystemKind.MOUNT,
        name=mountpoints[0],
        other_mounts=tuple(mountpoints[1:]),
        fs_type=first.fstype,
        disks=tuple(sorted(disks, key=disk_sort_key)),
        read_only=bool(first.read_only),
        readings_refused=refusals_of({"statvfs": None if space is None else space.error}),
        **_figures(space, first.fstype),
    )


def _mount_rows(capture: LinuxCapture) -> list[Filesystem]:
    by_device = disks_by_device(capture)
    space = capture.filesystem_space or {}
    rows: list[Filesystem] = []
    for dev, group in _grouped(tuple(capture.mounts or ())).items():
        disks = by_device.get(dev, frozenset()) | by_device.get(group[0].source_dev or "", frozenset())
        if disks:
            rows.append(_mount_row(group, disks, space.get(dev)))
    return rows


def _pool_rows(capture: LinuxCapture) -> list[Filesystem]:
    members = disks_by_pool(capture)
    pools = capture.zfs_pools or {}
    rows: list[Filesystem] = []
    for name in sorted(set(members) | set(pools)):
        disks = members.get(name, frozenset())
        if not disks:
            continue
        entry = pools.get(name)
        figures = {} if entry is None else {
            "size_bytes": entry.size, "used_bytes": entry.alloc, "free_bytes": entry.size - entry.alloc}
        rows.append(Filesystem(kind=FilesystemKind.POOL, name=name, fs_type=_ZFS,
                               disks=tuple(sorted(disks, key=disk_sort_key)), **figures))
    return rows


def _pool_refusals(capture: LinuxCapture) -> tuple[RefusedReading, ...]:
    if capture.zfs_pools_error is not None:
        return refusals_of({"zfs-pools": capture.zfs_pools_error})
    if capture.zfs_pools is None and disks_by_pool(capture):
        return refusals_of({"zfs-pools": _NO_CONTROL_DEVICE})
    return ()


def build_filesystems(
    capture: LinuxCapture, environment: Environment
) -> tuple[tuple[Filesystem, ...] | None, tuple[RefusedReading, ...]]:
    """Every filesystem and pool that lives on one of the capture's disks.

    Args:
        capture: A Linux reading, live or replayed.
        environment: What kind of machine it came from.

    Returns:
        The rows (``None`` in a container or when mountinfo was not read, the
        same rule as ``used by``) and the refusals that belong to no single row.
    """
    if environment is Environment.CONTAINER or capture.mounts is None:
        return None, ()
    return (*_mount_rows(capture), *_pool_rows(capture)), _pool_refusals(capture)


__all__ = ["build_filesystems"]
```

Fix the `disk_sort_key` import to the real name in `src/lsdsk/domain/disk_name.py` (the drive order:
"sdz before sdaa"). If a pool's alloc exceeds size the capture model accepted it but `Filesystem` free would
go negative: clamp with `max(entry.size - entry.alloc, 0)`.

`builder.build_inventory`: beside `resolve_usage`, `filesystems, unread_filesystems =
build_filesystems(capture, environment)` and pass both to the `Inventory(...)` construction.

- [ ] **Step 4: Run** the new tests plus `tests/test_linux_usage.py`, `tests/test_many_bind_mounts_stay_linear.py`,
  `tests/test_hw_builder*.py` - all pass. Add a linearity arm: 5000 bind mounts of one device build in under the
  ceiling `test_many_bind_mounts_stay_linear.py` already uses (copy its timing helper).
- [ ] **Step 5: Commit** - `feat(linux): the filesystems that live on each disk, joined like used-by`

---

### Task 7: Windows builder

**Files:**
- Create: `src/lsdsk/adapters/hw/windows/filesystems.py`
- Modify: `src/lsdsk/adapters/hw/windows/builder.py` (`build_inventory`, beside `resolve_usage`)
- Test: `tests/test_windows_filesystems.py`

**Interfaces:**
- Consumes: Tasks 4, 5; `usage._disk_numbers` logic - re-expose as public `usage.disk_numbers(nodes:
  frozenset[str]) -> dict[int, str]` (rename, keep a single definition) rather than importing a private.
- Produces: `windows.filesystems.build_filesystems(capture: WindowsCapture, environment: Environment) ->
  tuple[tuple[Filesystem, ...] | None, tuple[RefusedReading, ...]]` - one `VOLUME` row per volume with
  paths and at least one inventory disk; `name = paths[0]`, `other_mounts = paths[1:]`, figures from the
  space fields (`used = total - free`, `free = available`), inodes always `None`, refusal
  `volume-space` from `space_error`. `(None, ())` when `volumes is None` or in a container.

**Out of scope:** `usage.resolve_usage` behaviour.

**STOP conditions:** `usage._disk_numbers` is used by a test under its private name (then keep a private
alias that calls the public one and say so in the commit message).

- [ ] **Step 1: Failing tests** with `tests/test_windows_usage.py`'s `_disk`, `_volume`, `_capture` helpers:

```python
def test_a_volume_on_an_inventory_disk_is_a_row_with_its_space() -> None:
    capture = _capture([_disk(0)], volumes={"0": _volume(paths=["C:\\", "C:\\mnt\\x\\"], disks=[0],
                       total_bytes=100, free_bytes=40, available_bytes=30, fs_type="NTFS", read_only=False)})
    rows, refused = build_filesystems(capture, Environment.BARE_METAL)
    assert refused == ()
    assert rows is not None
    (c,) = rows
    assert (c.kind, c.name, c.other_mounts, c.disks) == (FilesystemKind.VOLUME, "C:\\", ("C:\\mnt\\x\\",), ("PhysicalDrive0",))
    assert (c.used_bytes, c.free_bytes, c.inodes_used_percent) == (60, 30, None)


def test_a_volume_without_a_path_or_a_disk_is_not_a_row() -> None:
    capture = _capture([_disk(0)], volumes={"0": _volume(paths=[], disks=[0]), "1": _volume(paths=["R:\\"], disks=[])})
    assert build_filesystems(capture, Environment.BARE_METAL)[0] == ()


def test_a_refused_space_call_is_carried_on_its_row() -> None:
    capture = _capture([_disk(0)], volumes={"0": _volume(paths=["D:\\"], disks=[0], space_error="could not (error 21)")})
    rows, _ = build_filesystems(capture, Environment.BARE_METAL)
    assert rows is not None
    assert [(r.reading, r.reason) for r in rows[0].readings_refused] == [("volume-space", "could not (error 21)")]
```

(adapt `_disk`/`_volume` argument names to the helpers' real ones.)

- [ ] **Step 2: Run** - expect ImportError.
- [ ] **Step 3: Implement** `build_filesystems` with one helper `_volume_row(entry: VolumeEntry, disks:
  tuple[str, ...]) -> Filesystem`; wire into `build_inventory`.
- [ ] **Step 4: Run** the new tests and `tests/test_windows_usage.py`, `tests/test_used_by_column.py`.
- [ ] **Step 5: Commit** - `feat(windows): each volume's fill, joined to its disks like used-by`

---

### Task 8: The table, and the section on the default page

**Files:**
- Modify: `src/lsdsk/adapters/render/tables.py` (`FILESYSTEM_COLUMNS`, `filesystem_table_row`,
  `render_filesystems`, `__all__`)
- Modify: `src/lsdsk/adapters/render/full.py` (after `render_disks`)
- Modify: `tests/test_display.py` (`sections` dict in
  `test_the_default_page_contains_every_section_a_command_can_show`)
- Test: `tests/test_filesystems_table.py`

**Interfaces:**
- Produces:
  - The approved drop order is `inodes%`, then `type`, then `size`, then `used`; `filesystem`, `use%` and
    `disks` never drop. A higher priority drops first:

    ```python
    FILESYSTEM_COLUMNS: tuple[Column, ...] = (
        Column("filesystem", "filesystem", priority=0, flexible=True, min_width=10),
        Column("type", "type", priority=5),
        Column("size", "size", align=Align.RIGHT, priority=4),
        Column("used", "used", align=Align.RIGHT, priority=3),
        Column("free", "free", align=Align.RIGHT, priority=1),
        Column("use", "use%", align=Align.RIGHT, priority=0),
        Column("inodes", "inodes%", align=Align.RIGHT, priority=6),
        Column("disks", "disks", priority=0, flexible=True, min_width=6),
    )
    ```
  - `filesystem_table_row(filesystem: Filesystem, *, both_scales: bool) -> Row`.
  - `render_filesystems(inventory: Inventory, findings: Sequence[Finding], width: int = DEFAULT_WIDTH) ->
    Table` - title `f"Filesystems on {inventory.hostname}"`; caption carries a legend naming `-` and/or
    `n/a` only when drawn, plus, when `inventory.filesystems is None`, the sentence "Filesystems were not
    read." with no rows.

**Out of scope:** `DISK_COLUMNS` and the `used_by` cell.

**STOP conditions:** `_render`'s signature differs from `(title, columns, rows: TableRows, width, caption)`.

- [ ] **Step 1: Failing tests**

```python
def test_a_row_carries_every_figure_under_its_own_heading() -> None:
    fs = Filesystem(kind=FilesystemKind.MOUNT, name="/var", fs_type="ext4", disks=("sda",),
                    size_bytes=20_000_000_000, used_bytes=19_100_000_000, free_bytes=900_000_000,
                    inodes_total=100, inodes_free=10)
    row = filesystem_table_row(fs, both_scales=False)
    assert {key: row[key][0] for key in ("filesystem", "type", "use", "inodes", "disks")} == {
        "filesystem": "/var", "type": "ext4", "use": "96%", "inodes": "90%", "disks": "sda"}


def test_a_dynamic_inode_table_reads_not_applicable_and_an_unread_figure_reads_a_dash() -> None:
    fs = Filesystem(kind=FilesystemKind.POOL, name="tank", fs_type="zfs", disks=("sda",))
    row = filesystem_table_row(fs, both_scales=False)
    assert (row["inodes"][0], row["use"][0], row["size"][0]) == (theme.NOT_APPLICABLE, theme.NOT_READ, theme.NOT_READ)


def test_other_mounts_are_counted_not_listed() -> None:
    fs = Filesystem(kind=FilesystemKind.MOUNT, name="/", other_mounts=("/a", "/b"), disks=("sda",))
    assert filesystem_table_row(fs, both_scales=False)["filesystem"][0] == "/ +2"


_EXT4 = Filesystem(kind=FilesystemKind.MOUNT, name="/var", fs_type="ext4", disks=("sda",),
                   size_bytes=100, used_bytes=50, free_bytes=50, inodes_total=10, inodes_free=5)
_POOL = Filesystem(kind=FilesystemKind.POOL, name="tank", fs_type="zfs", disks=("sdb", "sdc"),
                   size_bytes=100, used_bytes=10, free_bytes=90)
_UNREAD = Filesystem(kind=FilesystemKind.MOUNT, name="/data", fs_type="ext4", disks=("sdd",))


def _text(*rows: Filesystem, width: int = 160) -> str:
    console = Console(width=width, record=True, color_system=None)
    console.print(render_filesystems(_inventory(*rows), (), width=width))
    return console.export_text()


def test_the_legend_names_only_the_markers_drawn() -> None:
    plain = _text(_EXT4)
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND not in plain
    assert tables.FILESYSTEM_NOT_READ_LEGEND not in plain
    with_pool = _text(_EXT4, _POOL)
    assert tables.FILESYSTEM_NOT_APPLICABLE_LEGEND in with_pool
    assert tables.FILESYSTEM_NOT_READ_LEGEND not in with_pool
    assert tables.FILESYSTEM_NOT_READ_LEGEND in _text(_EXT4, _POOL, _UNREAD)


def test_an_inventory_that_read_no_filesystems_says_so() -> None:
    assert "Filesystems were not read." in _text_of(_inventory_unread())


def test_filesystem_use_and_disks_survive_every_width() -> None:
    dropped = False
    for width in range(200, 39, -1):
        rendered = _text(_EXT4, _POOL, width=width)
        for heading in ("filesystem", "use%", "disks"):
            assert heading in rendered, f"{heading} lost at width {width}"
        present = "inodes%" in rendered
        assert not (dropped and present), f"inodes% came back at width {width}"
        dropped = dropped or not present
```

`_inventory(*rows)` builds an `Inventory` with `filesystems=rows` using the helper the other table tests use;
`_inventory_unread()` the same with `filesystems=None`; `_text_of(inventory)` prints any inventory the way
`_text` does. The legend sentences are module constants in `tables.py` (and in `__all__`, since tests reach
them): `FILESYSTEM_NOT_READ_LEGEND = f"{theme.NOT_READ} = not read"` and `FILESYSTEM_NOT_APPLICABLE_LEGEND =
f"{theme.NOT_APPLICABLE} = no fixed inode table on this filesystem"`. Neither string may occur in a cell.

- [ ] **Step 2: Run** - expect ImportError.
- [ ] **Step 3: Implement** - mark each row with the worst finding whose `subject == filesystem.name`
  (`severity_index(findings)`), cells styled `theme.STYLE_UNKNOWN` for `-`/`n/a`, sizes via
  `theme.format_size_both` in the rich form and `theme.format_size` in the plain form (pass `TableRows(rich,
  plain)` so `Layout.preferring` surrenders the long form first), `disks` joined with a space. In `full.py`,
  add the section right after `render_disks` with a `blank` between, the same way the others are added.
- [ ] **Step 4: Update the completeness guard** - in `tests/test_display.py` add `"filesystems":
  tables.render_filesystems(machine, findings, width=width)` to `sections`. Note: on `linux-sas-hba` the
  section renders only its "not read" sentence; the guard still sees the section's title. Verify it catches
  the deletion: remove the `full.py` call, run the guard, require FAIL, restore.
- [ ] **Step 5: Run** the new tests and `tests/test_display.py`, `tests/test_link_bandwidth.py`.
- [ ] **Step 6: Commit** - `feat(render): the filesystems table and its place on the default page`

---

### Task 9: The `filesystems` command and the JSON envelope

**Files:**
- Modify: `src/lsdsk/domain/enums.py` (`CliCommand.FILESYSTEMS = "filesystems"`, APPENDED last)
- Modify: `src/lsdsk/adapters/cli/commands/scan.py` (`cli_filesystems`, `ScanData.filesystems`,
  `build_envelope`, `_refusals_named`)
- Modify: `src/lsdsk/adapters/cli/commands/__init__.py`, `src/lsdsk/adapters/cli/__init__.py`,
  `src/lsdsk/adapters/cli/root.py` (`_register_commands`)
- Modify: `src/lsdsk/adapters/cli/envelope.py` (docstring "eight" -> "nine")
- Modify tests: `tests/test_module_entry.py` (`expected_commands`), `tests/test_cli_scan.py`
  (`LISTING_COMMANDS`), `tests/test_a_section_explains_its_own_exit_code.py` (`SECTIONS`),
  `tests/test_unread_interface_on_every_page.py` (`PAGES`), `tests/test_input_bounds.py`
  (`_SALTED_VIEWS`), `tests/e2e/hostprobe.py` (`REPORTING`), `tests/test_history_cli.py`
  (`_JUDGED_AGAINST_HISTORY` only if the command reads history - it does not; leave it)
- Test: `tests/test_cli_filesystems.py`

**Interfaces:**
- Produces: `cli_filesystems(ctx, replay, output_format) -> None`; `ScanData.filesystems: tuple[Filesystem,
  ...] | None`; skipped lines `f"{reading}: {subject} - {reason}"` with subject = the filesystem's name for
  a row refusal and `"filesystems"` for `inventory.unread_filesystems`.

**Out of scope:** the TUI (Task 10) - but appending the enum member makes `PAGE_LABELS` fail at import;
add `CliCommand.FILESYSTEMS: "Filesystems"` to `PAGE_LABELS` in THIS task so the suite stays green, and
leave the page itself to Task 10. That makes `f9` a page key from this commit on, so ALSO move rescan to
`r,f10` and quit to `q,escape` here, and change the three existing `f9` rescan presses named in Task 10 Step 1
to `f10` in this commit - otherwise this commit is red. Task 10 then adds the page and its new tests.

**STOP conditions:** `tests/test_data_architecture.py`'s per-command envelope test needs `ok` true on
`linux-nvme-board` and the new command reports a refusal there.

- [ ] **Step 1: Failing tests**

```python
def test_the_command_prints_its_section() -> None:
    result = CliRunner().invoke(cli, ["filesystems", "--replay", str(FIXTURE)])
    assert "Filesystems on" in result.output


def test_the_json_envelope_names_the_command_and_carries_the_list() -> None:
    result = CliRunner().invoke(cli, ["filesystems", "--replay", str(FIXTURE), "--format", "json"])
    envelope = json.loads(result.output)
    assert envelope["command"] == "filesystems"
    assert "filesystems" in envelope["data"]
```

with `FIXTURE` = `tests/fixtures/hw/linux-nvme-board.json` and the runner/`cli` imports other CLI tests use.
Add a refused-row test through a hand-built capture written to `tmp_path` with `json.dumps` (never an
f-string): its JSON `skipped` must contain `"statvfs: /data - Permission denied"`.

- [ ] **Step 2: Run** - expect failures.
- [ ] **Step 3: Implement** `cli_filesystems` exactly like `cli_slots` (`job_id="cli-filesystems"`,
  `CliCommand.FILESYSTEMS`, local import `from lsdsk.adapters.render.tables import render_filesystems  #
  noqa: PLC0415 - keeps the import graph flat`, `console.print(render_filesystems(inventory, findings,
  width=console.width))`, `note_the_findings_this_page_left_out(findings, subjects, console)` with the
  subjects being every filesystem name), register it in the three places, extend the envelope and
  `_refusals_named`, and extend the hand-written lists named in **Files**.
- [ ] **Step 4: Run** `pytest -m "not local_only" -q` (whole suite, no path) - all pass.
- [ ] **Step 5: Commit** - `feat(cli): the filesystems command and its JSON list`

---

### Task 10: The TUI page and the moved keys

**Files:**
- Modify: `src/lsdsk/adapters/tui/app.py` (`FILESYSTEM_COLUMNS` page tuple, `compose()` TabPane,
  `_fill_filesystems`, `on_mount`, `action_rescan`'s clear tuple, `DETAIL_TABLES` only if the panel should
  follow it - it should not in this release, so leave `DETAIL_TABLES` alone, bindings)
- Modify tests: `tests/test_tui.py` (key-order list at 395-404, the f9 rescan test at ~690 and ~1720),
  `tests/test_tui_palette.py` (the f9 pass at ~106 and ~280 -> f10)
- Test: add page tests to `tests/test_tui.py`

**Interfaces:**
- Produces: `FILESYSTEM_COLUMNS = ("", *(column.title for column in tables.FILESYSTEM_COLUMNS))` (page
  tuple, in `__all__`); `TabPane("Filesystems", id=CliCommand.FILESYSTEMS.value)` holding
  `DataTable[str](id="filesystem-table", zebra_stripes=True, cursor_type="row")` and
  `Static(id="filesystem-legend")`; `_fill_filesystems(self) -> None`.
- Bindings: `Binding("r,f10", "rescan", "Rescan", priority=True, show=False)`,
  `Binding("q,escape", "quit", "Quit", priority=True)`.

**Out of scope:** the detail panel for a filesystem row (not in this release).

**STOP conditions:** after the binding change `f9` still triggers rescan in a pilot test (then Textual's
matching is not what the explorer reported; measure and report).

- [ ] **Step 1: Failing tests**

```python
def test_the_pages_sit_in_the_order_the_number_keys_promise() -> None:
    assert [command.value for command in PAGES] == [
        "topology", "controllers", "disks", "health", "smart", "findings", "slots", "trend", "filesystems"]


@pytest.mark.asyncio
async def test_f9_opens_the_filesystems_page_and_f10_rescans_without_quitting() -> None:
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("f9")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "filesystems"
        before = app.findings
        await pilot.press("f10")
        await pilot.pause()
        assert app.findings is not before, "f10 did not reach the rescan action"
        assert app.is_running, "f10 quit the app; it rescans now"
        assert app.query_one(TabbedContent).active == "filesystems", "rescan should not move the page"


@pytest.mark.asyncio
async def test_a_rescan_does_not_add_the_filesystem_columns_twice() -> None:
    app = LsdskApp(inventory())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("9")
        await pilot.pause()
        table = app.query_one("#filesystem-table", DataTable)
        columns = len(table.columns)
        await pilot.press("r")
        await pilot.pause()
        assert len(table.columns) == columns
```

`inventory()` is the existing helper at the top of `tests/test_tui.py`. In the same step change the existing
`test_every_bound_key_reaches_its_own_action` (~line 690) and the rescan test at ~1720 from `f9` to `f10` (its
assertion message too), and `tests/test_tui_palette.py`'s two `f9` presses (~106, ~280) and the `f9` in its
docstring (~93) to `f10`: the user moved the key, so the tests follow it.

- [ ] **Step 2: Run** `pytest tests/test_tui.py tests/test_tui_palette.py -q` - expect failures.
- [ ] **Step 3: Implement** the page like the slots page: `_fill_filesystems` builds rows with
  `tables.filesystem_table_row(fs, both_scales=True)`, restyles through `_cell`/`tui_palette.restyle`, adds them
  with `_marked(row, tables.FILESYSTEM_COLUMNS)` and `key=fs.name`, then `_explain_marks("#filesystem-legend",
  drawn)`. Call it in `on_mount` and `action_rescan`, and add `"#filesystem-table"` to the rescan's clear tuple.
- [ ] **Step 4: Run** the two TUI test files plus `tests/test_data_architecture.py` (page name = command name).
- [ ] **Step 5: Commit** - `feat(tui): the filesystems page on 9/f9; rescan moves to f10, quit keeps q/escape`

---

### Task 11: A real capture carrying filesystems

**Files:**
- Create: `tests/fixtures/hw/linux-zfs-filesystems.json` (scrubbed; name says what it holds)
- Modify: `tests/test_hw_tree.py` (`DENSITY_COUNTS` gains the capture's storage/siblings/full counts) and any
  other per-capture table that fails naming the new file (`test_fixture_serials.py` runs over it unchanged)

**Interfaces:** Produces the fixture later tasks name.

**Out of scope:** recapturing any existing fixture.

**STOP conditions:**
- no reachable host has both a ZFS pool and an ext4 filesystem on a listed disk (then capture the ZFS host,
  and keep the ext4 arm on hand-built captures only - say so in the commit message);
- the serial-invariant test reports a location the scrub missed.

- [ ] **Step 1: Build and ship** - `uv build`, then copy the wheel to a Proxmox node with ZFS (a node from the
  real-hardware host list in the main checkout's `.env`, `LSDSK_E2E_HOSTS`; copy `.env` into the worktree
  first, check with `grep -c '^LSDSK_E2E_HOSTS=' .env`, never print it), install into a throwaway `uv venv`
  under `/tmp` on the node and run `lsdsk snapshot -o /tmp/capture.json` as an UNPRIVILEGED user and once as
  root; keep the root capture. Remove the venv and wheel afterwards.
- [ ] **Step 2: Inspect** - the capture has `zfs_pools` with every pool `zpool list -Hp` shows (compare on the
  node), `filesystem_space` for the vfat/ext4 mounts, and no pool config blob.
- [ ] **Step 3: Scrub** per CLAUDE.md "To refresh or add one": serials in all eight places, then rewrite
  `hostname` to `linux-zfs-filesystems`; also replace pool names and mountpoints that name the site with
  generic ones (`rpool`, `tank`, `/srv/data`) consistently in mounts, signatures and `zfs_pools`.
- [ ] **Step 4: Run** `pytest tests/test_fixture_serials.py tests/test_hw_tree.py -q`; add the
  `DENSITY_COUNTS` entry from the failure's actual counts after checking them against the drawing by eye.
- [ ] **Step 5: Commit** - `test(fixtures): a real capture with ZFS pools and stat-ed filesystems`

---

### Task 12: Fill thresholds and the finding

**Files:**
- Modify: `src/lsdsk/domain/thresholds.py` (four fields)
- Modify: `src/lsdsk/adapters/config/tunables.py` (`read_thresholds`)
- Modify: `src/lsdsk/adapters/config/defaultconfig.d/60-thresholds.toml` (four keys, comment blocks in the
  file's format)
- Create: `src/lsdsk/domain/filesystem_fill.py` (`diagnose_filesystems`)
- Modify: `src/lsdsk/domain/diagnostics.py` (call it in `diagnose`, re-export in `__all__`)
- Modify: `tests/test_config_keys_are_live.py` (`CAPTURES` += `linux-zfs-filesystems`, `COMMANDS` +=
  `("filesystems",)`, `PROBES` entries for the four keys)
- Test: `tests/test_filesystem_fill.py`

**Interfaces:**
- Produces: `Thresholds.filesystem_full_hint_percent: int = Field(default=85, ge=1, le=100)`,
  `filesystem_full_warning_percent = 95`, `inodes_full_hint_percent = 85`, `inodes_full_warning_percent =
  95` (same bounds); `diagnose_filesystems(inventory: Inventory, thresholds: Thresholds = DEFAULT_THRESHOLDS)
  -> list[Finding]`.
- Finding shape: `subject=fs.name`; title `f"{fs.name} is {pct}% full, at or above the {limit}%
  {'warning' if warning else 'hint'} threshold"`, inode title `f"{fs.name} has used {pct}% of its inodes, at
  or above the {limit}% ... threshold"`; detail `f"{used:.1f} GB of {size:.1f} GB used, {free:.1f} GB free,
  on {', '.join(fs.disks)}."` (decimal GB, computed in the domain); action for space "Free space or grow the
  filesystem before it fills: a full filesystem fails writes, and a full ZFS pool also slows down." and for
  inodes "Remove small files or rebuild it with more inodes: a filesystem out of inodes refuses new files
  while it still has free space."

**Out of scope:** history/trend of fill.

**STOP conditions:** the config harness cannot observe a key on the new capture (then the capture is not full
enough for the default thresholds; the probe must set a threshold BELOW the capture's fill, which is how
`PROBES` entries work - read two existing entries first).

- [ ] **Step 1: Failing tests** (parametrised at limit-1, limit, limit+1 like `tests/test_limit_equalities.py`)

```python
@pytest.mark.parametrize(
    ("percent", "severity"),
    [(84, None), (85, Severity.HINT), (94, Severity.HINT), (95, Severity.WARNING), (100, Severity.WARNING)],
)
def test_space_is_judged_at_or_above_each_threshold(percent: int, severity: Severity | None) -> None:
    fs = Filesystem(kind=FilesystemKind.MOUNT, name="/var", fs_type="ext4", disks=("sda",),
                    used_bytes=percent, free_bytes=100 - percent, size_bytes=100)
    found = diagnose_filesystems(_inventory(fs))
    assert [f.severity for f in found] == ([] if severity is None else [severity])


def test_a_read_only_mount_is_never_judged() -> None:
    fs = Filesystem(kind=FilesystemKind.MOUNT, name="/snap", fs_type="squashfs", disks=("sda",),
                    used_bytes=100, free_bytes=0, read_only=True)
    assert diagnose_filesystems(_inventory(fs)) == []


def test_inodes_are_judged_only_on_a_fixed_table() -> None:
    ext4 = Filesystem(kind=FilesystemKind.MOUNT, name="/a", fs_type="ext4", disks=("sda",),
                      inodes_total=100, inodes_free=3)
    xfs = ext4.with_changes(name="/b", fs_type="xfs")
    titles = [f.title for f in diagnose_filesystems(_inventory(ext4, xfs))]
    assert titles == ["/a has used 97% of its inodes, at or above the 95% warning threshold"]


def test_the_thresholds_are_the_configured_ones() -> None:
    fs = Filesystem(kind=FilesystemKind.MOUNT, name="/v", fs_type="ext4", disks=("sda",), used_bytes=50, free_bytes=50)
    tight = Thresholds(filesystem_full_hint_percent=40, filesystem_full_warning_percent=50)
    assert [f.severity for f in diagnose_filesystems(_inventory(fs), tight)] == [Severity.WARNING]


def test_an_inventory_that_read_no_filesystems_raises_nothing() -> None:
    assert diagnose_filesystems(_inventory_unread()) == []
```

(`_inventory(*rows)` builds an `Inventory` with `filesystems=rows`; `_inventory_unread()` with `None`.)

- [ ] **Step 2: Run** - expect ImportError.
- [ ] **Step 3: Implement** the rule (one helper per judged figure, guard clauses, under complexity 10), the
  four `Thresholds` fields (no hint<=warning validator: the harness sets every key to 7), the four
  `read_thresholds` lines (`values.positive_int(...)`), and the four toml keys, each with Default / When to
  change / Consequence / `Environment Variable: LSDSK___THRESHOLDS__<KEY>="<n>"`. Call
  `findings.extend(diagnose_filesystems(inventory, thresholds))` after `diagnose_firmware_consistency`.
- [ ] **Step 4: Harness** - add the capture and the command to `CAPTURES`/`COMMANDS`, and one `PROBES` entry per
  key that sets it below the capture's fullest filesystem; run `tests/test_config_keys_are_live.py`,
  `tests/test_history_config.py`, `tests/test_limit_equalities.py`.
- [ ] **Step 5: Run** the whole suite: `pytest -m "not local_only" -q`.
- [ ] **Step 6: Commit** - `feat(diagnostics): a nearly full filesystem or inode table is a finding`

---

### Task 13: Documentation, skill, ADR, changelog

**Files:**
- Modify: `COMMANDS.md`, `PAGES.md` (keys `1` to `9`, f9 page, f10 rescan, q/escape quit), `README.md` (eight ->
  nine pages; four places), `REPORT.md` (the new section), `FINDINGS.md` (the two findings), `CONFIG.md` (the
  four keys) and each `de/` twin; `python scripts/translation_manifest.py --refresh <doc>` per doc
- Modify: `tests/test_readme_documents_the_cli.py` (`PAGE_KEY_PROSE = "`1` to `9`"`)
- Create: `docs/adr/0005-how-full-a-filesystem-is.md` (Status/Context/Decision/Consequences, from the design
  doc: the section, why statvfs cannot judge a pool, the ioctl and its policies, no config blob in a capture,
  the inode rule, read-only exclusion, the key move)
- Modify: `docs/systemdesign/module_reference.md` (new modules; `tests/test_module_reference.py` holds it)
- Modify: `CHANGELOG.md` `[Unreleased]`: Added (section, command, page, JSON list, findings, keys), Changed (rescan
  f9 -> f10, quit no longer on f10)
- Modify: `skills/lsdsk/SKILL.md` through `bitranox:meta-skill-writer` ONLY: commands block (nine views, 1-9),
  "Only the eight section commands" -> nine, `diagnose_filesystems(` in the rules list, a `data.filesystems`
  sentence; then run `tests/test_the_skill_agrees_with_the_package.py`,
  `tests/test_skill_quotes_output_the_tool_produces.py`, `tests/test_skill_describes_the_fabric_view*`.
- Delete: `docs/plans/2026-10-12-filesystems-design.md` and this plan, once the ADR carries the decisions
  (precedent: the [30] plan was removed when ADR 0004 landed).

**STOP conditions:** a German twin sentence cannot be made to say exactly what the English says (ask).

- [ ] **Step 1:** Edit each English doc, then its twin sentence by sentence, refresh the manifest, run
  `tests/test_translations.py tests/test_readme_documents_the_cli.py tests/test_documented_surface.py`.
- [ ] **Step 2:** ADR, module reference, CHANGELOG; run `tests/test_module_reference.py`.
- [ ] **Step 3:** SKILL.md via the skill-writer; run the skill tests named above.
- [ ] **Step 4:** Delete the two plan docs; grep the tree for `2026-10-12-filesystems` and fix every hit.
- [ ] **Step 5: Commit** each of the three groups separately.

---

### Task 14: Whole-feature verification

- [ ] `make test` (bmk, from the worktree) - `ALL GATES PASSED` via `gate.py`; commit any bmk churn.
- [ ] `pytest -m "not local_only" -q` from the repo root, no path - read the passed count against the baseline.
- [ ] Replay every committed fixture through `lsdsk filesystems --replay <f>` and `--format json`: old fixtures
  print the "not read" sentence and `"filesystems": null`; the new one prints rows matching its capture.
- [ ] `make testintegration` (copy `.env` first): on every host the `/` row is populated, and on a ZFS host each
  pool row's `use%` is within one point of `zpool list -o cap` (extend `tests/e2e/test_real_hardware.py` with
  that assertion in this task, reading `zpool list` on the host through the existing ssh helper).
- [ ] Run the TUI on a real terminal at 180x50: press 9, f9, r, f10, q; confirm the footer shows the new keys.
- [ ] Hand the branch back for review (`bitranox:process-review-requesting-code-review`), then merge to main,
  bump to 1.8.0 only on the user's release go.
