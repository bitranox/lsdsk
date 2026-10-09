"""Kernel-published text is split on the newline and nothing else.

The kernel escapes space, tab, newline and backslash in a path it prints into
``/proc/self/mountinfo`` and ``/proc/swaps``; every other byte, including form
feed, carriage return and the Unicode line separators, is written as it is.
``str.splitlines`` and a bare ``str.split`` treat those as line and field
breaks, so a mount whose path holds one lost its row and its disk read as "not
mounted".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.decode.virtualization import container_markers_in_mounts
from lsdsk.adapters.hw.linux import mounts

if TYPE_CHECKING:
    from pathlib import Path

ABSENT_NODE = "/dev/lsdsk-test-absent"
ODD_BYTES = ["\x0c", "\r", "\x0b", "\x1c", "\x85", " ", " "]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("odd", ODD_BYTES, ids=repr)
def test_a_mount_path_holding_a_line_break_character_keeps_its_row(tmp_path: Path, odd: str) -> None:
    path = tmp_path / "mountinfo"
    path.write_bytes(
        (f"30 22 8:2 / /mnt/a{odd}b rw - ext4 {ABSENT_NODE}2 rw\n31 22 8:3 / /srv rw - ext4 {ABSENT_NODE}3 rw\n").encode()
    )
    rows = mounts.read_mounts(path)
    assert rows is not None
    assert [(row["dev"], row["mountpoint"]) for row in rows] == [("8:2", f"/mnt/a{odd}b"), ("8:3", "/srv")]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("odd", ODD_BYTES, ids=repr)
def test_a_swap_path_holding_a_line_break_character_is_one_row(tmp_path: Path, odd: str) -> None:
    path = tmp_path / "swaps"
    path.write_bytes(
        f"Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/swap{odd}file\t\tfile\t\t4194300\t\t0\t\t-2\n".encode()
    )
    assert mounts.read_swaps(path) == [{}]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("odd", ODD_BYTES, ids=repr)
def test_a_udev_value_holding_a_line_break_character_is_kept_whole(tmp_path: Path, odd: str) -> None:
    (tmp_path / "b8:2").write_bytes(f"E:ID_FS_LABEL=a{odd}b\nE:ID_FS_TYPE=ext4\n".encode())
    assert mounts.read_signatures(["8:2"], tmp_path) == {"8:2": {"fs_label": f"a{odd}b", "fs_type": "ext4"}}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("odd", ODD_BYTES, ids=repr)
def test_a_container_mount_whose_path_holds_a_line_break_character_is_still_seen(odd: str) -> None:
    line = f"6226 6210 0:99 / /proc/a{odd}b rw - fuse.lxcfs lxcfs rw"
    assert container_markers_in_mounts(line) == "lxc"
