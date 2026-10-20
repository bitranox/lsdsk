"""The macOS desktop shortcut: an executable ``.command`` that runs lsdsk in Terminal.

Double-clicking a ``.command`` opens Terminal and runs it, which is what lsdsk
needs - it self-elevates with ``sudo`` and so wants a terminal. The file-writing
logic is plain POSIX, so these run on any POSIX host (Linux included) against a
redirected HOME; selecting the adapter per platform is the dispatcher's job.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut

if TYPE_CHECKING:
    from pathlib import Path


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
