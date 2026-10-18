"""The fleet run's own bookkeeping, tested without a fleet.

``test_real_hardware`` can only run where the fleet is reachable, so the parts
of it that decide something - which uv a Windows host runs, and whether the
Windows bitmap walk was really exercised - live in ``fleet`` and are held here
on every runner.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from fleet import (
    FRAGMENTATION_CHECK,
    WINDOWS_UV_CANDIDATES,
    first_uv_path,
    windows_fragmentation_gaps,
    windows_uv_lookup_command,
)


def _windows_host(**facts: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "host": "win1",
        "platform": "Windows",
        "privileged": True,
        "check_names": [FRAGMENTATION_CHECK],
        "ntfs_volumes": 1,
        "ntfs_volumes_read": 1,
    }
    return {**base, **facts}


def test_the_first_uv_the_lookup_printed_wins() -> None:
    printed = "C:\\Users\\me\\AppData\\Local\\Microsoft\\WinGet\\Links\\uv.exe\r\nC:\\Program Files\\uv\\uv.exe\r\n"
    assert first_uv_path(printed) == "C:\\Users\\me\\AppData\\Local\\Microsoft\\WinGet\\Links\\uv.exe"


def test_a_lookup_that_found_nothing_answers_none_rather_than_a_guess() -> None:
    assert first_uv_path("") is None
    assert first_uv_path("INFO: Could not find files for the given pattern(s).\r\n") is None


def test_the_lookup_asks_path_first_then_every_known_location() -> None:
    command = windows_uv_lookup_command()
    assert command.startswith("where uv ")
    for candidate in WINDOWS_UV_CANDIDATES:
        assert f'(if exist "{candidate}" echo {candidate})' in command
    assert "C:\\Program Files\\uv\\uv.exe" in WINDOWS_UV_CANDIDATES
    assert "%LOCALAPPDATA%\\Microsoft\\WinGet\\Links\\uv.exe" in WINDOWS_UV_CANDIDATES


def test_a_windows_host_that_read_a_bitmap_leaves_no_gap() -> None:
    assert windows_fragmentation_gaps([_windows_host()]) == []


def test_a_windows_host_whose_fragmentation_check_never_ran_is_a_gap() -> None:
    """The check returns nothing when no NTFS row exists, which used to read as covered."""
    gaps = windows_fragmentation_gaps([_windows_host(check_names=["smart:ran"], ntfs_volumes=0, ntfs_volumes_read=0)])
    assert len(gaps) == 1
    assert "win1" in gaps[0]
    assert "0 NTFS volume(s)" in gaps[0]


def test_a_host_recorded_before_check_names_existed_is_a_gap_not_a_pass() -> None:
    host = _windows_host()
    del host["check_names"]
    assert windows_fragmentation_gaps([host])


def test_a_linux_host_is_not_asked_about_ntfs() -> None:
    linux: dict[str, Any] = {"host": "lin1", "platform": "Linux", "check_names": []}
    assert windows_fragmentation_gaps([linux]) == []
