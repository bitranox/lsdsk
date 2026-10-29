"""The Linux desktop shortcut: an executable ``.desktop`` that runs lsdsk in a terminal.

lsdsk needs a terminal to self-elevate through sudo, so the entry is
``Terminal=true``; it is written both to the Desktop (made executable, which a
desktop environment requires before it will launch one) and to the application
menu. These drive the real adapter against a redirected HOME, so the files land
under the test's own directory.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut, UnwritableDesktopEntryError
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import OutputFormat


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

    It is strict where the spec is: a backtick or dollar sign inside the quotes
    with no backslash before it, and a ``%`` that is not half of ``%%``, are
    refused rather than read leniently, since a launcher reads the first as
    shell syntax and the second as a field code it substitutes.
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
        elif quoted and character in "`$":
            raise AssertionError(f"a reserved {character!r} inside the quotes must be escaped with a backslash")
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
    for argument in argv:
        assert "%" not in argument.replace("%%", ""), f"a lone % in {argument!r} is a field code, not a percent sign"
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
    ("argument", "written"),
    [
        pytest.param("a`b", 'Exec="a\\\\`b"', id="backtick"),
        pytest.param("a$b", 'Exec="a\\\\$b"', id="dollar"),
        pytest.param('a"b', 'Exec="a\\\\"b"', id="double-quote"),
        pytest.param("a\\b", 'Exec="a\\\\\\\\b"', id="backslash"),
        pytest.param("100%", 'Exec="100%%"', id="percent"),
        pytest.param("%f", 'Exec="%%f"', id="field-code-lookalike"),
    ],
)
def test_each_reserved_character_is_written_with_the_escape_the_spec_names(
    home: Path, argument: str, written: str
) -> None:
    """The exact bytes, not a read-back: a lenient reader accepts what a launcher would not.

    Inside the quotes a backtick, dollar sign, double quote and backslash each take
    a backslash, a ``%`` is doubled, and the string rule then doubles every
    backslash once more - so the file holds two backslashes before each of them.
    """
    text = LinuxDesktopShortcut().create(launch=(argument,), icon=None).read_text(encoding="utf-8")

    assert _keys(text)["Exec"] == written.removeprefix("Exec=")


@pytest.mark.os_linux
@pytest.mark.parametrize("control", [chr(0x7F), chr(0x1F), chr(0x00)], ids=["DEL", "unit-separator", "NUL"])
def test_every_control_character_including_del_is_refused(home: Path, control: str) -> None:
    """DEL (0x7F) sits just past the C0 range, so a class that stops at 0x1F would let it into the file."""
    with pytest.raises(UnwritableDesktopEntryError, match="holds a control character"):
        LinuxDesktopShortcut().create(launch=(f"lsdsk{control}",), icon=None)

    assert list((home / "Desktop").iterdir()) == []


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


@pytest.mark.os_linux
def test_both_entries_are_executable_by_everyone(home: Path) -> None:
    """A desktop environment refuses to launch an entry that is not executable for the user who clicks it."""
    LinuxDesktopShortcut().create(launch=("lsdsk",), icon=None)

    for entry in (home / "Desktop" / "lsdsk.desktop", home / ".local" / "share" / "applications" / "lsdsk.desktop"):
        assert entry.stat().st_mode & 0o777 == 0o755, entry


@pytest.mark.os_linux
def test_a_bare_home_in_user_dirs_is_home_itself_and_so_no_desktop(home: Path) -> None:
    """``"$HOME"`` without the slash is expanded like ``"$HOME/"``: home, which turns the desktop off."""
    config_home = _user_dirs(home / "cfg", 'XDG_DESKTOP_DIR="$HOME"')
    shortcut = LinuxDesktopShortcut(config_home=config_home)

    assert shortcut.desktop_dir() == home
    assert shortcut.has_desktop() is False


@pytest.mark.os_linux
def test_a_relative_path_in_user_dirs_is_ignored_for_the_default(home: Path) -> None:
    """The spec allows only ``$HOME/...`` or an absolute path; a relative one would follow the working directory."""
    (home / "Desk").mkdir()
    config_home = _user_dirs(home / "cfg", 'XDG_DESKTOP_DIR="Desk"')

    assert LinuxDesktopShortcut(config_home=config_home).desktop_dir() == home / "Desktop"


@pytest.mark.os_linux
def test_a_commented_out_desktop_line_is_not_read(home: Path) -> None:
    (home / "Old").mkdir()
    config_home = _user_dirs(home / "cfg", '# XDG_DESKTOP_DIR="$HOME/Old"')

    assert LinuxDesktopShortcut(config_home=config_home).desktop_dir() == home / "Desktop"


@pytest.mark.os_linux
def test_user_dirs_is_read_from_xdg_config_home_when_none_is_given(home: Path, user_config_dir: Path) -> None:
    """With no directory given, user-dirs.dirs is read from the config home the environment names.

    ``user_config_dir`` points that variable away from ``~/.config`` on Linux, and
    its parent is the config home itself, so the file here is found only if the
    variable is honoured.
    """
    (home / "Schreibtisch").mkdir()
    _user_dirs(user_config_dir.parent, 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"')

    assert LinuxDesktopShortcut().desktop_dir() == home / "Schreibtisch"


@pytest.mark.os_linux
def test_an_undecodable_line_elsewhere_in_user_dirs_does_not_hide_the_desktop_line(home: Path) -> None:
    """One path that will not decode names no folder; it is no reason to ignore the line that does."""
    (home / "Schreibtisch").mkdir()
    config_home = home / "cfg"
    config_home.mkdir()
    (config_home / "user-dirs.dirs").write_bytes(
        b'XDG_MUSIC_DIR="$HOME/M\xffsik"\nXDG_DESKTOP_DIR="$HOME/Schreibtisch"\n'
    )

    assert LinuxDesktopShortcut(config_home=config_home).desktop_dir() == home / "Schreibtisch"


@pytest.mark.os_linux
@pytest.mark.parametrize(
    "launch",
    [("/usr/bin/uvx", "--exclude-newer", "7 days", "lsdsk@latest"), _AWKWARD_LAUNCH],
    ids=["uvx-launch", "awkward-launch"],
)
def test_the_written_entry_passes_desktop_file_validate_without_a_hint(home: Path, launch: tuple[str, ...]) -> None:
    """The freedesktop validator is the reader the spec is written for; it must accept the entry silently.

    Skipped where the validator is not installed. A hint counts as a finding: two
    main categories (``System;Utility;``) list the application twice in a menu,
    which the validator reports only as a hint and with exit status 0.
    """
    # Where desktop-file-utils installs it on Debian, Ubuntu and Fedora; spelled out
    # in the call below too, so the argv is fixed text rather than a PATH lookup.
    if not Path("/usr/bin/desktop-file-validate").is_file():
        pytest.skip("desktop-file-validate is not installed")
    written = LinuxDesktopShortcut().create(launch=launch, icon=home / "a dir\\with $x" / "icon.png")

    # The entry named relative to its folder, which keeps the whole argv fixed text.
    result = subprocess.run(
        ("/usr/bin/desktop-file-validate", "lsdsk.desktop"),
        cwd=written.parent,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "LC_ALL": "C"},
        check=False,
        timeout=60,
    )

    assert (result.returncode, result.stdout + result.stderr) == (0, "")


def _menu_that_cannot_be_written(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the application menu at a plain FILE, so its directory can never be made and the entry never written."""
    data_home = home / "data"
    data_home.mkdir()
    (data_home / "applications").write_bytes(b"")
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    return data_home / "applications"


@pytest.mark.os_linux
def test_a_menu_entry_that_fails_after_the_desktop_one_is_a_warning_not_a_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Desktop entry is the shortcut, and it is written: a failed menu copy must not report that nothing was.

    The menu copy is the extra, as on Windows, which skips a Start menu it cannot
    find, and on macOS, which has none: the reader is told it is missing, and why.
    """
    menu = _menu_that_cannot_be_written(home, monkeypatch)
    warnings: list[str] = []

    written = LinuxDesktopShortcut(warn=warnings.append).create(launch=("lsdsk",), icon=None)

    assert written == home / "Desktop" / "lsdsk.desktop"
    assert written.read_text(encoding="utf-8").startswith("[Desktop Entry]\n")
    assert len(warnings) == 1, warnings
    assert "on the Desktop" in warnings[0]
    assert str(menu) in warnings[0]


@pytest.mark.os_linux
def test_install_shortcut_reports_the_desktop_entry_when_only_the_menu_copy_failed(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _menu_that_cannot_be_written(home, monkeypatch)
    seams = ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(),
        uvx=lambda: Path("/usr/bin/uvx"),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
        copy_icon=lambda: home / "icon.png",
    )

    install_shortcut(output_format=OutputFormat.JSON, seams=seams)

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["data"]["shortcut"] == str(home / "Desktop" / "lsdsk.desktop")
