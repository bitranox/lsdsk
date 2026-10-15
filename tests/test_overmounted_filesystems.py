"""A filesystem another mount hides keeps no figures that belong to the mount lookup reaches.

``statvfs(path)`` resolves the path to the TOPMOST mount there, so a filesystem
mounted over - by a second disk, or by a tmpfs the reader drops - used to be
given the top one's size and fill, and could raise a fill finding about the
wrong filesystem. Each test runs mountinfo text through the real reader, with a
``statvfs`` that answers the way the kernel does (for the mount on top), then
through the real builder and fill rule.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.hw.linux import mounts
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.linux.filesystems import build_filesystems
from lsdsk.adapters.render.tables import filesystem_marked_row
from lsdsk.domain.enums import Environment, FilesystemKind, Severity
from lsdsk.domain.filesystem_fill import diagnose_filesystems
from lsdsk.domain.models import Filesystem, Inventory

if TYPE_CHECKING:
    from pathlib import Path

    from lsdsk.domain.models import Finding

ABSENT_NODE = "/dev/lsdsk-test-absent"
HIDDEN_REASON = "another mount hides every mountpoint of it, so statvfs there would read a different filesystem"


@dataclass(frozen=True)
class Stat:
    """One filesystem's statvfs figures."""

    f_blocks: int
    f_bfree: int
    f_frsize: int = 4096
    f_bavail: int = 0
    f_files: int = 0
    f_ffree: int = 0


#: Nearly full, so the fill rule raises a finding on whatever row carries it.
FULL = Stat(f_blocks=1000, f_bfree=10, f_bavail=10)
#: Nearly empty.
EMPTY = Stat(f_blocks=1000, f_bfree=990, f_bavail=990)


class _Kernel:
    """Answers statvfs for whichever filesystem is on top at a path, and records each path asked."""

    def __init__(self, on_top: dict[str, Stat]) -> None:
        self.on_top = on_top
        self.asked: list[str] = []

    def __call__(self, path: str) -> Stat:
        self.asked.append(path)
        return self.on_top[path]


def _read(tmp_path: Path, mountinfo: str, kernel: _Kernel) -> LinuxCapture:
    path = tmp_path / "mountinfo"
    path.write_text(mountinfo, encoding="utf-8")
    rows = mounts.read_mounts(path, device_number=lambda _source: None)
    assert rows is not None
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "linux",
        "hostname": "h",
        "kernel": "6.1",
        "pci": {},
        "block": {
            "sda": {"dev": "8:0", "holders": [], "partitions": {"sda1": {"dev": "8:1"}, "sda2": {"dev": "8:2"}}},
            "sdb": {"dev": "8:16", "holders": [], "partitions": {"sdb1": {"dev": "8:17"}, "sdb2": {"dev": "8:18"}}},
        },
        "mounts": rows,
        "swaps": [],
        "stacked": {},
        "signatures": {},
        "filesystem_space": mounts.read_space(rows, statvfs=kernel),
    }
    return LinuxCapture.model_validate(reading)


def _rows(capture: LinuxCapture) -> tuple[Filesystem, ...]:
    rows, _refused = build_filesystems(capture, Environment.BARE_METAL)
    assert rows is not None
    return rows


def _findings(rows: tuple[Filesystem, ...]) -> list[Finding]:
    return diagnose_filesystems(Inventory(hostname="h", filesystems=rows))


def _refusals(row: Filesystem) -> list[tuple[str, str]]:
    return [(refused.reading, refused.reason) for refused in row.readings_refused]


# sda1 mounted at /data, then sdb1 mounted over it: the second row's parent is
# the first mount, at the same mountpoint.
DISK_OVER_DISK = (
    "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
    f"30 22 8:1 / /data rw - ext4 {ABSENT_NODE}1 rw\n"
    f"31 30 8:17 / /data rw - ext4 {ABSENT_NODE}2 rw\n"
)

# sda1 mounted at /data, then a tmpfs over it, which the reader drops.
TMPFS_OVER_DISK = (
    "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
    f"30 22 8:1 / /data rw - ext4 {ABSENT_NODE}1 rw\n"
    "31 30 0:50 / /data rw - tmpfs tmpfs rw\n"
)


@pytest.mark.os_agnostic
def test_a_disk_mounted_over_another_keeps_its_figures_and_hides_the_one_below(tmp_path: Path) -> None:
    kernel = _Kernel({"/data": FULL})

    rows = _rows(_read(tmp_path, DISK_OVER_DISK, kernel))

    below, top = sorted(rows, key=lambda row: row.disks)
    assert (below.name, below.disks, top.name, top.disks) == ("/data", ("sda",), "/data", ("sdb",))
    assert (top.size_bytes, top.used_percent, top.readings_refused) == (1000 * 4096, 99, ())
    assert (below.size_bytes, below.used_bytes, below.free_bytes) == (None, None, None)
    assert _refusals(below) == [("statvfs", HIDDEN_REASON)]
    # The covered mountpoint was never asked for the filesystem below.
    assert kernel.asked == ["/data"]


@pytest.mark.os_agnostic
def test_a_dropped_tmpfs_over_a_disk_lends_it_no_figures(tmp_path: Path) -> None:
    rows = _rows(_read(tmp_path, TMPFS_OVER_DISK, _Kernel({"/data": FULL})))

    (data,) = rows
    assert (data.name, data.disks, data.used_bytes, data.used_percent) == ("/data", ("sda",), None, None)
    assert _refusals(data) == [("statvfs", HIDDEN_REASON)]
    # The tmpfs's fill is not this disk's, so nothing is judged.
    assert _findings(rows) == []


@pytest.mark.os_agnostic
def test_two_filesystems_at_one_path_never_raise_two_findings_about_it(tmp_path: Path) -> None:
    rows = _rows(_read(tmp_path, DISK_OVER_DISK, _Kernel({"/data": FULL})))

    subjects = [finding.subject for finding in _findings(rows)]

    # Control: the top filesystem is full, so there is a finding to duplicate.
    assert subjects == ["/data"]
    assert len(subjects) == len(set(subjects))


@pytest.mark.os_agnostic
def test_a_filesystem_still_reachable_elsewhere_is_read_and_named_there(tmp_path: Path) -> None:
    mountinfo = (
        "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
        f"30 22 8:1 / /data rw - ext4 {ABSENT_NODE}1 rw\n"
        f"31 22 8:1 / /srv/data rw - ext4 {ABSENT_NODE}1 rw\n"
        "32 30 0:50 / /data rw - tmpfs tmpfs rw\n"
    )
    kernel = _Kernel({"/data": FULL, "/srv/data": EMPTY})

    (row,) = _rows(_read(tmp_path, mountinfo, kernel))

    assert kernel.asked == ["/srv/data"]
    assert (row.name, row.other_mounts, row.used_percent, row.readings_refused) == ("/srv/data", ("/data",), 1, ())


@pytest.mark.os_agnostic
def test_a_mount_under_a_covered_directory_is_hidden_too(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(
        "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
        f"30 22 8:1 / /mnt rw - ext4 {ABSENT_NODE}1 rw\n"
        f"31 30 8:17 / /mnt/b rw - ext4 {ABSENT_NODE}2 rw\n"
        "32 30 0:50 / /mnt rw - tmpfs tmpfs rw\n"
        f"33 32 8:33 / /mnt/c rw - ext4 {ABSENT_NODE}3 rw\n",
        encoding="utf-8",
    )
    rows = mounts.read_mounts(path, device_number=lambda _source: None)
    assert rows is not None
    hidden = {str(row["mountpoint"]): row.get("hidden") is True for row in rows}
    # /mnt is covered by the tmpfs, /mnt/b sits inside the covered /mnt, and
    # /mnt/c sits inside the tmpfs on top, which every path reaches.
    assert hidden == {"/": False, "/mnt": True, "/mnt/b": True, "/mnt/c": False}


@pytest.mark.os_agnostic
def test_a_parent_loop_in_mountinfo_ends_rather_than_spinning(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(
        f"30 31 8:1 / /a rw - ext4 {ABSENT_NODE}1 rw\n31 30 8:17 / /b rw - ext4 {ABSENT_NODE}2 rw\n",
        encoding="utf-8",
    )
    answer: list[list[dict[str, str | bool]] | None] = []
    # Run in a daemon thread bounded by a join timeout, so a walk that loops
    # fails this test by name instead of hanging the suite.
    walker = threading.Thread(
        target=lambda: answer.append(mounts.read_mounts(path, device_number=lambda _source: None)), daemon=True
    )
    walker.start()
    walker.join(timeout=10)
    assert not walker.is_alive(), "the hidden-mount walk did not end on a parent loop"
    (rows,) = answer
    assert rows is not None
    assert [row.get("hidden") for row in rows] == [None, None]


@pytest.mark.os_agnostic
def test_a_capture_taken_before_hidden_existed_reads_every_mount_as_reachable() -> None:
    capture = LinuxCapture.model_validate(
        {
            "schema": 2,
            "platform": "linux",
            "hostname": "h",
            "kernel": "6.1",
            "pci": {},
            "mounts": [{"dev": "8:1", "mountpoint": "/data", "fstype": "ext4"}],
        }
    )
    assert [mount.hidden for mount in capture.mounts or ()] == [False]


@pytest.mark.os_agnostic
def test_a_row_with_nothing_read_carries_no_marker_from_a_row_of_its_name() -> None:
    top = Filesystem(kind=FilesystemKind.MOUNT, name="/data", used_bytes=99, free_bytes=1)
    hidden = Filesystem(kind=FilesystemKind.MOUNT, name="/data")
    severities = {"/data": Severity.WARNING}

    marked_top = filesystem_marked_row(top, severities, both_scales=False)
    marked_hidden = filesystem_marked_row(hidden, severities, both_scales=False)

    # Control: the row the finding is about carries its marker.
    assert marked_top.marker[0].strip()
    assert not marked_hidden.marker[0].strip()


# The shapes below were checked against the kernel in a user and mount
# namespace, each tmpfs a distinct size so statvfs showed which one answered:
# mountinfo lists a mount made earlier first, and the parent it records is the
# mount the mountpoint was found in when it was made.

# sda1 mounted at /data/a/b, THEN a tmpfs at /data/a: both sit in the root
# mount, so lookup of /data/a/b crosses into the tmpfs and never reaches sda1.
ANCESTOR_MOUNTED_LATER = (
    "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
    f"30 22 8:1 / /data/a/b rw - ext4 {ABSENT_NODE}1 rw\n"
    "31 22 0:50 / /data/a rw - tmpfs tmpfs rw\n"
)

# The tmpfs at /data/a FIRST, then sda1 at /data/a/b: the deeper mount is made
# inside the tmpfs, so its parent is the tmpfs and it sits on top.
ANCESTOR_MOUNTED_FIRST = (
    "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
    "30 22 0:50 / /data/a rw - tmpfs tmpfs rw\n"
    f"31 30 8:1 / /data/a/b rw - ext4 {ABSENT_NODE}1 rw\n"
)

# Siblings in one parent, each made later than the other and none above another
# by COMPONENT: /x/a is a text prefix of /x/ab, and /y/ab sits beside /y/a/b.
NOT_A_PATH_PREFIX = (
    "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
    f"30 22 8:1 / /x/ab rw - ext4 {ABSENT_NODE}1 rw\n"
    f"31 22 8:17 / /x/a rw - ext4 {ABSENT_NODE}2 rw\n"
    f"32 22 8:2 / /y/a/b rw - ext4 {ABSENT_NODE}3 rw\n"
    f"33 22 8:18 / /y/ab rw - ext4 {ABSENT_NODE}4 rw\n"
)


@pytest.mark.os_agnostic
def test_a_mount_on_an_ancestor_directory_made_later_hides_the_deeper_one(tmp_path: Path) -> None:
    kernel = _Kernel({"/data/a/b": FULL})

    rows = _rows(_read(tmp_path, ANCESTOR_MOUNTED_LATER, kernel))

    (data,) = rows
    assert (data.name, data.disks, data.used_percent) == ("/data/a/b", ("sda",), None)
    assert _refusals(data) == [("statvfs", HIDDEN_REASON)]
    # The tmpfs's fill reached nobody's row, so nothing is judged.
    assert kernel.asked == []
    assert _findings(rows) == []


@pytest.mark.os_agnostic
def test_a_deeper_mount_made_after_its_ancestor_sits_on_top_and_is_read(tmp_path: Path) -> None:
    kernel = _Kernel({"/data/a/b": FULL})

    (data,) = _rows(_read(tmp_path, ANCESTOR_MOUNTED_FIRST, kernel))

    assert kernel.asked == ["/data/a/b"]
    assert (data.name, data.used_percent, data.readings_refused) == ("/data/a/b", 99, ())


@pytest.mark.os_agnostic
def test_a_sibling_whose_name_is_a_text_prefix_hides_nothing(tmp_path: Path) -> None:
    kernel = _Kernel({"/x/ab": FULL, "/x/a": EMPTY, "/y/a/b": FULL, "/y/ab": EMPTY})

    rows = _rows(_read(tmp_path, NOT_A_PATH_PREFIX, kernel))

    assert sorted(kernel.asked) == ["/x/a", "/x/ab", "/y/a/b", "/y/ab"]
    assert sorted((row.name, row.used_percent) for row in rows) == [
        ("/x/a", 1),
        ("/x/ab", 99),
        ("/y/a/b", 99),
        ("/y/ab", 1),
    ]
    assert all(row.readings_refused == () for row in rows)


# A tmpfs mounted on / with the system running: lookup STARTS at the process
# root rather than crossing into what is stacked on it, so every path still
# reaches the old root, and the tmpfs and anything mounted inside it are the
# unreachable ones.
STACKED_ON_THE_ROOT = (
    "22 1 0:28 / / rw - zfs rpool/ROOT/pve-1 rw\n"
    f"30 22 8:1 / /data rw - ext4 {ABSENT_NODE}1 rw\n"
    "40 22 0:60 / / rw - tmpfs overroot rw\n"
    f"41 40 8:17 / /inside rw - ext4 {ABSENT_NODE}2 rw\n"
)


@pytest.mark.os_agnostic
def test_a_mount_stacked_on_the_root_hides_nothing_beneath_it(tmp_path: Path) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(STACKED_ON_THE_ROOT, encoding="utf-8")
    rows = mounts.read_mounts(path, device_number=lambda _source: None)
    assert rows is not None
    hidden = {str(row["mountpoint"]): row.get("hidden") is True for row in rows}

    # The tmpfs row itself is dropped; what was mounted inside it is cut off with it.
    assert hidden == {"/": False, "/data": False, "/inside": True}


@pytest.mark.os_agnostic
def test_the_root_and_its_mounts_are_read_with_a_mount_stacked_on_the_root(tmp_path: Path) -> None:
    kernel = _Kernel({"/data": FULL})

    rows = _rows(_read(tmp_path, STACKED_ON_THE_ROOT, kernel))

    by_name = {row.name: row for row in rows}
    assert kernel.asked == ["/data"]
    assert (by_name["/data"].used_percent, by_name["/data"].readings_refused) == (99, ())
    assert _refusals(by_name["/inside"]) == [("statvfs", HIDDEN_REASON)]


# The top of a mount tree may name itself as its parent (the kernel's mountinfo
# documentation allows it), so the root here is its own parent.
SELF_PARENTED_ROOT = f"21 21 8:1 / / rw - ext4 {ABSENT_NODE}1 rw\n23 21 8:17 / /data rw - ext4 {ABSENT_NODE}2 rw\n"

# The same root with a tmpfs stacked on it, which the reader drops.
SELF_PARENTED_ROOT_UNDER_A_TMPFS = (
    f"21 21 8:1 / / rw - ext4 {ABSENT_NODE}1 rw\n"
    "22 21 0:60 / / rw - tmpfs overroot rw\n"
    f"23 21 8:17 / /data rw - ext4 {ABSENT_NODE}2 rw\n"
)


def _hidden(tmp_path: Path, mountinfo: str) -> dict[str, bool]:
    path = tmp_path / "mountinfo"
    path.write_text(mountinfo, encoding="utf-8")
    rows = mounts.read_mounts(path, device_number=lambda _source: None)
    assert rows is not None
    return {str(row["mountpoint"]): row.get("hidden") is True for row in rows}


def test_a_root_that_is_its_own_parent_hides_nothing_mounted_on_it(tmp_path: Path) -> None:
    kernel = _Kernel({"/": EMPTY, "/data": FULL})

    rows = _rows(_read(tmp_path, SELF_PARENTED_ROOT, kernel))

    by_name = {row.name: row for row in rows}
    assert kernel.asked == ["/", "/data"]
    assert (by_name["/data"].used_percent, by_name["/data"].readings_refused) == (99, ())
    assert by_name["/"].readings_refused == ()


def test_a_mount_stacked_on_a_self_parented_root_hides_neither_the_root_nor_its_mounts(tmp_path: Path) -> None:
    assert _hidden(tmp_path, SELF_PARENTED_ROOT_UNDER_A_TMPFS) == {"/": False, "/data": False}
