"""``read_environment`` takes everything it reads from the roots it is handed.

Handed an empty ``/proc`` it still reported ``container_marker: lxc`` on a
Proxmox host: the systemd runtime file and the container marker files were read
from the live machine whatever the seam said, so a test, or a replay of another
machine's tree, could not exclude the host it ran on. Overriding ``proc`` now
roots those reads in its parent, and ``root`` names the tree directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.capture import MAX_DEVICE_TEXT
from lsdsk.adapters.hw.linux.reader import read_environment
from lsdsk.adapters.hw.snapshot import build_from

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.os_agnostic


def _write(path: Path, text: str) -> None:
    """Create a file and its directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_an_empty_proc_yields_no_container_evidence_whatever_the_host_is(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()

    evidence = read_environment(proc=proc)

    assert not {"container_marker", "container_files", "cgroup", "mount_markers"} & evidence.keys()
    assert not any(key.startswith("dmi_") for key in evidence)


def test_the_systemd_runtime_file_is_read_from_the_tree_handed_in(tmp_path: Path) -> None:
    """The positive twin: the marker comes from the seam, so the empty case above proves isolation."""
    _write(tmp_path / "run" / "systemd" / "container", "podman\n")
    (tmp_path / "proc").mkdir()

    assert read_environment(proc=tmp_path / "proc")["container_marker"] == "podman"


def test_the_marker_files_are_read_from_the_tree_handed_in(tmp_path: Path) -> None:
    _write(tmp_path / ".dockerenv", "")
    _write(tmp_path / "run" / ".containerenv", "")
    (tmp_path / "proc").mkdir()

    assert read_environment(proc=tmp_path / "proc")["container_files"] == ["docker", "podman"]


def test_the_dmi_strings_are_read_from_the_tree_handed_in(tmp_path: Path) -> None:
    _write(tmp_path / "sys" / "class" / "dmi" / "id" / "sys_vendor", "ACME Corp\n")
    (tmp_path / "proc").mkdir()

    assert read_environment(proc=tmp_path / "proc")["dmi_vendor"] == "ACME Corp"


def test_pid_ones_environment_cgroup_and_mountinfo_come_from_proc(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "environ").write_bytes(b"PATH=/bin\0container=lxc\0")
    _write(proc / "1" / "cgroup", "0::/lxc/101\n")
    _write(proc / "self" / "mountinfo", "6226 6210 0:99 /cpuinfo /proc/cpuinfo rw - fuse.lxcfs lxcfs rw\n")

    evidence = read_environment(proc=proc)

    assert (evidence["container_marker"], evidence["cgroup"], evidence["mount_markers"]) == (
        "lxc",
        "0::/lxc/101",
        "lxc",
    )


def test_a_root_named_directly_is_used_with_the_default_proc_left_alone(tmp_path: Path) -> None:
    _write(tmp_path / "run" / "systemd" / "container", "oci\n")
    proc = tmp_path / "elsewhere"
    proc.mkdir()

    assert read_environment(proc=proc, root=tmp_path)["container_marker"] == "oci"


def test_a_root_named_directly_wins_over_the_parent_of_proc(tmp_path: Path) -> None:
    """The tree comes from ``root`` and not from ``proc``'s parent, which here holds a different marker."""
    _write(tmp_path / "host" / "run" / "systemd" / "container", "oci\n")
    _write(tmp_path / "other" / "run" / "systemd" / "container", "podman\n")
    proc = tmp_path / "other" / "proc"
    proc.mkdir(parents=True)

    assert read_environment(proc=proc, root=tmp_path / "host")["container_marker"] == "oci"
    assert read_environment(proc=proc)["container_marker"] == "podman"


def test_a_carriage_return_inside_a_mount_point_does_not_cut_the_row(tmp_path: Path) -> None:
    """The kernel prints a CR unescaped; universal newlines turned it into a row break."""
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "mountinfo").write_bytes(b"6226 6210 0:99 /cpu /proc/cpu\rinfo rw - fuse.lxcfs lxcfs rw\n")

    assert read_environment(proc=proc)["mount_markers"] == "lxc"


def _built_from_environment_of(tmp_path: Path, marker: str) -> dict[str, object]:
    """Read PID 1's environment holding ``container=marker``, then build a capture around what was read."""
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "environ").write_bytes(f"PATH=/bin\0container={marker}\0".encode())
    evidence = read_environment(proc=proc)
    build_from(
        {"schema": 2, "platform": "linux", "hostname": "h", "kernel": "6.8.0", "pci": {}, "environment": evidence}
    )
    return evidence


def test_a_container_marker_longer_than_a_device_text_reads_as_not_read_and_the_capture_builds(tmp_path: Path) -> None:
    """The value reaches a field bounded at ``MAX_DEVICE_TEXT``, which refused the whole capture."""
    evidence = _built_from_environment_of(tmp_path, "x" * (MAX_DEVICE_TEXT + 10))

    assert "container_marker" not in evidence


def test_a_container_marker_at_the_device_text_bound_is_kept(tmp_path: Path) -> None:
    marker = "x" * MAX_DEVICE_TEXT

    assert _built_from_environment_of(tmp_path, marker)["container_marker"] == marker
