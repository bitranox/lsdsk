"""The Linux desktop shortcut: an executable ``.desktop`` that runs lsdsk in a terminal.

lsdsk needs a terminal to self-elevate through sudo, so the entry is
``Terminal=true``; it is written both to the Desktop (made executable, which a
desktop environment requires before it will launch one) and to the application
menu. These drive the real adapter against a redirected HOME, so the files land
under the test's own directory.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A redirected HOME with a Desktop directory, as a real profile has."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    (tmp_path / "Desktop").mkdir()
    return tmp_path


@pytest.mark.os_linux
def test_create_writes_an_executable_terminal_desktop_file(home: Path) -> None:
    written = LinuxDesktopShortcut().create(launch=("uvx", "lsdsk@latest"), icon=None)

    assert written == home / "Desktop" / "lsdsk.desktop"
    text = written.read_text(encoding="utf-8")
    assert "Exec=uvx lsdsk@latest" in text
    assert "Terminal=true" in text
    assert "Type=Application" in text
    assert os.access(written, os.X_OK), "a desktop environment will not launch a non-executable .desktop"


@pytest.mark.os_linux
def test_create_also_writes_into_the_application_menu(home: Path) -> None:
    LinuxDesktopShortcut().create(launch=("lsdsk",), icon=None)

    menu_entry = home / ".local" / "share" / "applications" / "lsdsk.desktop"
    assert menu_entry.is_file()
    assert "Exec=lsdsk" in menu_entry.read_text(encoding="utf-8")


@pytest.mark.os_linux
def test_exists_turns_true_once_the_shortcut_is_created(home: Path) -> None:
    shortcut = LinuxDesktopShortcut()
    assert shortcut.exists() is False
    shortcut.create(launch=("lsdsk",), icon=None)
    assert shortcut.exists() is True


@pytest.mark.os_linux
def test_an_icon_is_referenced_only_when_one_is_given(home: Path) -> None:
    icon = home / "icon.png"
    icon.write_bytes(b"\x89PNG\r\n")

    with_icon = LinuxDesktopShortcut().create(launch=("lsdsk",), icon=icon).read_text(encoding="utf-8")
    assert f"Icon={icon}" in with_icon

    (home / "Desktop" / "lsdsk.desktop").unlink()
    without = LinuxDesktopShortcut().create(launch=("lsdsk",), icon=None).read_text(encoding="utf-8")
    assert "Icon=" not in without


@pytest.mark.os_linux
def test_a_launch_argument_with_a_space_is_quoted_in_exec(home: Path) -> None:
    text = (
        LinuxDesktopShortcut().create(launch=("/opt/my uv/uvx", "lsdsk@latest"), icon=None).read_text(encoding="utf-8")
    )
    assert "Exec='/opt/my uv/uvx' lsdsk@latest" in text


@pytest.mark.os_linux
def test_the_desktop_dir_is_named_for_a_message(home: Path) -> None:
    assert LinuxDesktopShortcut().desktop_dir() == home / "Desktop"
