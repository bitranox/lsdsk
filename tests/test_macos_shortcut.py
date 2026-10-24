"""The macOS desktop shortcut: an executable ``.command`` that runs lsdsk in Terminal.

Double-clicking a ``.command`` opens Terminal and runs it, which is what lsdsk
needs - it self-elevates with ``sudo`` and so wants a terminal. The file-writing
logic is plain POSIX, so these run on any POSIX host (Linux included) against a
redirected HOME; selecting the adapter per platform is the dispatcher's job.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    return tmp_path


@pytest.mark.os_posix
def test_create_writes_an_executable_command_that_runs_the_launch(home: Path) -> None:
    written = MacosDesktopShortcut().create(launch=("uvx", "lsdsk@latest"), icon=None)

    assert written == home / "Desktop" / "lsdsk.command"
    text = written.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "exec uvx lsdsk@latest" in text
    assert os.access(written, os.X_OK), "a .command must be executable to launch on double-click"


@pytest.mark.os_posix
def test_exists_turns_true_once_created(home: Path) -> None:
    shortcut = MacosDesktopShortcut()
    assert shortcut.exists() is False
    shortcut.create(launch=("lsdsk",), icon=None)
    assert shortcut.exists() is True


@pytest.mark.os_posix
def test_a_launch_argument_with_a_space_is_quoted(home: Path) -> None:
    text = (
        MacosDesktopShortcut().create(launch=("/opt/my uv/uvx", "lsdsk@latest"), icon=None).read_text(encoding="utf-8")
    )
    assert "exec '/opt/my uv/uvx' lsdsk@latest" in text


@pytest.mark.os_posix
def test_the_desktop_dir_is_the_home_desktop(home: Path) -> None:
    assert MacosDesktopShortcut().desktop_dir() == home / "Desktop"


@pytest.mark.os_posix
def test_a_desktop_folder_means_a_desktop(home: Path) -> None:
    assert MacosDesktopShortcut().has_desktop() is True


@pytest.mark.os_posix
def test_a_home_with_no_desktop_folder_has_no_desktop(home: Path) -> None:
    (home / "Desktop").rmdir()

    assert MacosDesktopShortcut().has_desktop() is False


@pytest.mark.os_posix
def test_create_never_makes_the_desktop_folder_itself(home: Path) -> None:
    (home / "Desktop").rmdir()

    with pytest.raises(FileNotFoundError) as refused:
        MacosDesktopShortcut().create(launch=("lsdsk",), icon=None)
    assert refused.value.filename is not None
    assert Path(refused.value.filename).parent == home / "Desktop", "the write must fail on the missing Desktop"
    assert not (home / "Desktop").exists()


@pytest.mark.os_posix
def test_a_symlink_at_the_destination_is_replaced_not_written_through(home: Path) -> None:
    victim = home / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    victim.chmod(0o600)
    destination = home / "Desktop" / "lsdsk.command"
    destination.symlink_to(victim)

    MacosDesktopShortcut().create(launch=("lsdsk",), icon=None)

    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert victim.stat().st_mode & 0o777 == 0o600
    assert not destination.is_symlink()
    assert destination.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


@pytest.mark.os_posix
def test_a_missing_desktop_folder_is_named_in_the_reason(home: Path) -> None:
    (home / "Desktop").rmdir()

    assert MacosDesktopShortcut().no_desktop_reason() == f"no desktop folder at {home / 'Desktop'}"


@pytest.mark.os_posix
def test_the_command_is_executable_by_everyone(home: Path) -> None:
    written = MacosDesktopShortcut().create(launch=("lsdsk",), icon=None)

    assert written.stat().st_mode & 0o777 == 0o755
