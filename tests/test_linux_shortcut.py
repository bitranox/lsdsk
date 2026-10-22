"""The Linux desktop shortcut: an executable ``.desktop`` that runs lsdsk in a terminal.

lsdsk needs a terminal to self-elevate through sudo, so the entry is
``Terminal=true``; it is written both to the Desktop (made executable, which a
desktop environment requires before it will launch one) and to the application
menu. These drive the real adapter against a redirected HOME, so the files land
under the test's own directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut


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


def _user_dirs(config_home: Path, desktop_line: str) -> Path:
    """Write a ``user-dirs.dirs`` the way ``xdg-user-dirs-update`` does, with one desktop line."""
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "user-dirs.dirs").write_text(
        f'# This file is written by xdg-user-dirs-update\n{desktop_line}\nXDG_DOWNLOAD_DIR="$HOME/Downloads"\n',
        encoding="utf-8",
    )
    return config_home


@pytest.mark.os_linux
def test_a_localised_desktop_named_in_user_dirs_is_the_one_used(home: Path) -> None:
    """A German desktop keeps its icons in ~/Schreibtisch, recorded in user-dirs.dirs, not the environment."""
    (home / "Schreibtisch").mkdir()
    config_home = _user_dirs(home / "cfg", 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"')
    shortcut = LinuxDesktopShortcut(config_home=config_home)

    assert shortcut.desktop_dir() == home / "Schreibtisch"
    assert shortcut.create(launch=("lsdsk",), icon=None) == home / "Schreibtisch" / "lsdsk.desktop"


@pytest.mark.os_linux
def test_an_absolute_desktop_path_in_user_dirs_is_taken_as_written(home: Path) -> None:
    elsewhere = home / "elsewhere" / "Desk"
    elsewhere.mkdir(parents=True)
    config_home = _user_dirs(home / "cfg", f'XDG_DESKTOP_DIR="{elsewhere}"')

    assert LinuxDesktopShortcut(config_home=config_home).desktop_dir() == elsewhere


@pytest.mark.os_linux
def test_the_environment_beats_user_dirs(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_home = _user_dirs(home / "cfg", 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"')
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(home / "FromEnv"))

    assert LinuxDesktopShortcut(config_home=config_home).desktop_dir() == home / "FromEnv"


@pytest.mark.os_linux
def test_a_home_with_no_desktop_folder_has_no_desktop(home: Path) -> None:
    """A headless server's account has no Desktop folder: nowhere to put a shortcut."""
    (home / "Desktop").rmdir()

    assert LinuxDesktopShortcut().has_desktop() is False


@pytest.mark.os_linux
def test_a_desktop_folder_means_a_desktop(home: Path) -> None:
    assert LinuxDesktopShortcut().has_desktop() is True


@pytest.mark.os_linux
def test_a_desktop_disabled_in_user_dirs_is_no_desktop(home: Path) -> None:
    """The spec's ``"$HOME/"`` turns the desktop folder off; home itself is not a desktop."""
    config_home = _user_dirs(home / "cfg", 'XDG_DESKTOP_DIR="$HOME/"')

    assert LinuxDesktopShortcut(config_home=config_home).has_desktop() is False


@pytest.mark.os_linux
def test_a_user_dirs_line_that_names_a_missing_folder_is_no_desktop(home: Path) -> None:
    config_home = _user_dirs(home / "cfg", 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"')

    assert LinuxDesktopShortcut(config_home=config_home).has_desktop() is False


@pytest.mark.os_linux
def test_create_never_makes_the_desktop_folder_itself(home: Path) -> None:
    """Writing the shortcut must not conjure a Desktop on a machine that has none."""
    (home / "Desktop").rmdir()

    with pytest.raises(FileNotFoundError) as refused:
        LinuxDesktopShortcut().create(launch=("lsdsk",), icon=None)
    assert refused.value.filename is not None
    assert Path(refused.value.filename).parent == home / "Desktop", "the write must fail on the missing Desktop"
    assert not (home / "Desktop").exists()
