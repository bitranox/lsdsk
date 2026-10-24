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

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut, UnwritableDesktopEntryError
from lsdsk.adapters.uv_bootstrap import UvInstallResult


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
    assert 'Exec="uvx" "lsdsk@latest"' in text
    assert "Terminal=true" in text
    assert "Type=Application" in text
    assert os.access(written, os.X_OK), "a desktop environment will not launch a non-executable .desktop"


@pytest.mark.os_linux
def test_create_also_writes_into_the_application_menu(home: Path) -> None:
    LinuxDesktopShortcut().create(launch=("lsdsk",), icon=None)

    menu_entry = home / ".local" / "share" / "applications" / "lsdsk.desktop"
    assert menu_entry.is_file()
    assert 'Exec="lsdsk"' in menu_entry.read_text(encoding="utf-8")


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
def test_a_launch_argument_with_a_space_is_double_quoted_in_exec(home: Path) -> None:
    """The Desktop Entry spec quotes with double quotes only; a single quote is an ordinary character there."""
    text = (
        LinuxDesktopShortcut().create(launch=("/opt/my uv/uvx", "lsdsk@latest"), icon=None).read_text(encoding="utf-8")
    )
    assert 'Exec="/opt/my uv/uvx" "lsdsk@latest"' in text


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


def _string_value(raw: str) -> str:
    """Undo the Desktop Entry ``string`` escapes: backslash-s, -n, -t, -r and a doubled backslash."""
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    out: list[str] = []
    characters = iter(raw)
    for character in characters:
        if character == "\\":
            follower = next(characters)
            out.append(escapes[follower])
        else:
            out.append(character)
    return "".join(out)


def _exec_argv(value: str) -> list[str]:
    """Split an unescaped ``Exec`` value into its argv the way the spec's quoting rules read it.

    Written from the specification rather than borrowed from GLib, so a mistake in
    the writer is not mirrored by the same library reading it back: outside quotes
    a space separates arguments, inside double quotes a backslash escapes the next
    of the four characters that need it, and ``%%`` is a literal percent sign.
    """
    argv: list[str] = []
    current: list[str] = []
    started = False
    quoted = False
    characters = iter(value)
    for character in characters:
        if quoted and character == "\\":
            follower = next(characters)
            assert follower in '"`$\\', (
                f"a backslash may escape only one of the four reserved characters, not {follower!r}"
            )
            current.append(follower)
        elif character == '"':
            quoted = not quoted
            started = True
        elif character == " " and not quoted:
            if started:
                argv.append("".join(current))
            current, started = [], False
        else:
            current.append(character)
            started = True
    assert not quoted, "an unterminated quote"
    if started:
        argv.append("".join(current))
    return [argument.replace("%%", "%") for argument in argv]


def _keys(text: str) -> dict[str, str]:
    """The ``key=value`` lines of a single-group entry, raw."""
    lines = text.splitlines()
    assert lines[0] == "[Desktop Entry]"
    return dict(line.split("=", 1) for line in lines[1:] if line)


_AWKWARD_LAUNCH = (
    "/opt/my uv/uvx",
    'say "hi"',
    "back`tick",
    "$HOME",
    "a\\b",
    "100%",
    "it's",
    "#;|&<>~*?()",
    f"Laufwerk-{chr(0xFC)}-{chr(0x4E2D)}-{chr(0x1F4BE)}",
)


@pytest.mark.os_linux
def test_every_launch_argument_reads_back_exactly_under_the_spec_quoting(home: Path) -> None:
    icon = home / "a dir\\with $x" / "icon.png"
    text = LinuxDesktopShortcut().create(launch=_AWKWARD_LAUNCH, icon=icon).read_text(encoding="utf-8")

    keys = _keys(text)
    assert _exec_argv(_string_value(keys["Exec"])) == list(_AWKWARD_LAUNCH)
    assert _string_value(keys["Icon"]) == str(icon)


@pytest.mark.os_linux
@pytest.mark.parametrize(
    ("launch", "icon_name"),
    [(("/opt/uv\nX-Injected=1/uvx",), "icon.png"), (("lsdsk",), "ic\non.png"), (("tab\there",), "icon.png")],
    ids=["newline-in-launch", "newline-in-icon", "tab-in-launch"],
)
def test_a_control_character_is_refused_and_nothing_is_written(
    home: Path, launch: tuple[str, ...], icon_name: str
) -> None:
    """A newline would end the key and start another one the reader never wrote."""
    with pytest.raises(UnwritableDesktopEntryError) as refused:
        LinuxDesktopShortcut().create(launch=launch, icon=home / icon_name)

    assert isinstance(refused.value, OSError)
    assert list((home / "Desktop").iterdir()) == []
    assert not (home / ".local" / "share" / "applications" / "lsdsk.desktop").exists()


@pytest.mark.os_linux
@pytest.mark.parametrize("where", ["desktop", "menu"])
def test_a_symlink_at_the_destination_is_replaced_not_written_through(home: Path, where: str) -> None:
    """Following it would overwrite, and chmod, a file the shortcut was never meant to touch."""
    victim = home / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    victim.chmod(0o600)
    destination = (
        home / "Desktop" / "lsdsk.desktop"
        if where == "desktop"
        else home / ".local" / "share" / "applications" / "lsdsk.desktop"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(victim)

    LinuxDesktopShortcut().create(launch=("lsdsk",), icon=None)

    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert victim.stat().st_mode & 0o777 == 0o600
    assert not destination.is_symlink()
    assert destination.read_text(encoding="utf-8").startswith("[Desktop Entry]\n")


@pytest.mark.os_linux
@pytest.mark.parametrize("value", ["$HOME/", "$HOME"])
def test_a_desktop_turned_off_in_user_dirs_is_refused_in_those_words(
    home: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Home is there, so "no desktop folder at <home>" told the reader something false."""
    config_home = _user_dirs(home / "cfg", f'XDG_DESKTOP_DIR="{value}"')
    seams = ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(config_home=config_home),
        uvx=lambda: Path("/usr/bin/uvx"),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
    )

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=seams)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    err = " ".join(capsys.readouterr().err.split())
    assert f"{config_home / 'user-dirs.dirs'} turns the desktop off" in err
    assert "no desktop folder" not in err


@pytest.mark.os_linux
def test_an_environment_desktop_that_is_home_is_refused_in_those_words(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(home))

    reason = LinuxDesktopShortcut().no_desktop_reason()

    assert "XDG_DESKTOP_DIR names the home folder itself" in reason


@pytest.mark.os_linux
def test_a_missing_desktop_folder_is_named_in_the_reason(home: Path) -> None:
    (home / "Desktop").rmdir()

    assert LinuxDesktopShortcut().no_desktop_reason() == f"no desktop folder at {home / 'Desktop'}"
