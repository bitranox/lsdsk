"""The ``install-shortcut`` command: resolve a launcher, then write the shortcut.

The branch logic is driven through an injected recording double at the
DesktopShortcuts seam, so no real installer runs and no file is written to the
developer's own Desktop; one os_linux case drives the real Linux adapter against
a redirected HOME to prove the file is actually written.
"""

from __future__ import annotations

import dataclasses
import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import (
    ShortcutSeams,
    install_shortcut,
    shortcuts_for,
)
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut
from lsdsk.adapters.shortcut.windows import WindowsDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import OutputFormat
from lsdsk.domain.shortcut import manual_install_line, uvx_launch_args

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lsdsk.application.ports import DesktopShortcuts


class _RecordingShortcuts:
    """A DesktopShortcuts double that records what it was told to create."""

    def __init__(self, *, desktop: bool = True) -> None:
        self.desktop = desktop
        self.created: tuple[tuple[str, ...], Path | None] | None = None

    def exists(self) -> bool:
        return False

    def has_desktop(self) -> bool:
        return self.desktop

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)
        return Path("/desktop/lsdsk.desktop")

    def no_desktop_reason(self) -> str:
        return f"no desktop folder at {Path('/desktop')}"


def _seams(
    shortcuts: DesktopShortcuts,
    *,
    uvx: Path | None = None,
    lsdsk: Path | None = None,
    installed: Path | None = None,
    installer_exit: int = 0,
) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: uvx,
        installed_lsdsk=lambda: lsdsk,
        install_uv=lambda: UvInstallResult(exit_code=installer_exit, uvx=installed),
    )


@pytest.mark.os_agnostic
def test_a_present_uvx_is_what_the_shortcut_is_told_to_run() -> None:
    recording = _RecordingShortcuts()
    uvx = Path("/usr/bin/uvx")
    install_shortcut(seams=_seams(recording, uvx=uvx))
    assert recording.created is not None
    # The launch carries the path as this platform renders it: on Windows the same Path reads \usr\bin\uvx.
    assert recording.created[0] == uvx_launch_args(str(uvx))


@pytest.mark.os_agnostic
def test_a_plainly_installed_lsdsk_is_the_fallback_launcher() -> None:
    recording = _RecordingShortcuts()
    lsdsk = Path("/usr/local/bin/lsdsk")
    install_shortcut(seams=_seams(recording, uvx=None, lsdsk=lsdsk))
    assert recording.created is not None
    assert recording.created[0] == (str(lsdsk),)


@pytest.mark.os_agnostic
def test_without_a_launcher_and_without_install_uv_it_refuses_and_guides(capsys: pytest.CaptureFixture[str]) -> None:
    recording = _RecordingShortcuts()
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=_seams(recording, uvx=None, lsdsk=None), install_uv=False)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    assert recording.created is None, "nothing should be written when there is no launcher"
    err = capsys.readouterr().err
    assert "--install-uv" in err
    assert "astral.sh" in err


@pytest.mark.os_agnostic
def test_install_uv_installs_then_creates_with_the_new_uvx() -> None:
    recording = _RecordingShortcuts()
    installed = Path("/root/.local/bin/uvx")
    install_shortcut(
        seams=_seams(recording, uvx=None, lsdsk=None, installed=installed),
        install_uv=True,
    )
    assert recording.created is not None
    assert recording.created[0] == uvx_launch_args(str(installed))


@pytest.mark.os_agnostic
def test_an_install_that_still_yields_no_uvx_fails_without_writing(capsys: pytest.CaptureFixture[str]) -> None:
    recording = _RecordingShortcuts()
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=_seams(recording, uvx=None, lsdsk=None, installed=None), install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    assert recording.created is None
    _ = capsys.readouterr()


@pytest.mark.os_agnostic
def test_an_account_with_no_desktop_is_refused_naming_the_folder(capsys: pytest.CaptureFixture[str]) -> None:
    """On a headless server there is nowhere to put a shortcut, so nothing is written."""
    recording = _RecordingShortcuts(desktop=False)
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=_seams(recording, uvx=Path("/usr/bin/uvx")))

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    assert recording.created is None
    err = capsys.readouterr().err
    assert "no desktop folder" in err
    assert str(Path("/desktop")) in err


@pytest.mark.os_agnostic
def test_an_account_with_no_desktop_is_refused_in_json_too(capsys: pytest.CaptureFixture[str]) -> None:
    recording = _RecordingShortcuts(desktop=False)
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, seams=_seams(recording, uvx=Path("/usr/bin/uvx")))

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert "no desktop folder" in json.dumps(envelope)


class _RefusingShortcuts(_RecordingShortcuts):
    """A DesktopShortcuts double whose write fails with ``error``."""

    def __init__(self, error: OSError) -> None:
        super().__init__()
        self.error = error

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        raise self.error


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("error", "expected", "said"),
    [
        (PermissionError(13, "Permission denied", "/desktop/lsdsk.desktop"), ExitCode.PERMISSION_DENIED, "not allowed"),
        (IsADirectoryError(21, "Is a directory", "/desktop/lsdsk.desktop"), ExitCode.IO_ERROR, "could not write"),
        (OSError(28, "No space left on device", "/desktop/lsdsk.desktop"), ExitCode.IO_ERROR, "could not write"),
    ],
    ids=["permission", "directory-in-the-way", "disk-full"],
)
def test_a_failed_write_leaves_the_documented_code_and_an_envelope_not_its_errno(
    error: OSError, expected: ExitCode, said: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed write is mapped like ``snapshot``'s, never left as its errno.

    The errno is the filesystem's number, and some collide with codes this tool
    means something by: 2 is a usage error.
    """
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(
            output_format=OutputFormat.JSON,
            seams=_seams(_RefusingShortcuts(error), uvx=Path("/usr/bin/uvx")),
        )

    assert leaving.value.code == expected
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == expected.name
    assert said in envelope["error"]["message"]
    assert "lsdsk.desktop" in envelope["error"]["message"]


@pytest.mark.os_agnostic
def test_an_icon_that_will_not_copy_is_a_failed_write_too(capsys: pytest.CaptureFixture[str]) -> None:
    """The command does not degrade to an iconless shortcut: the reader asked, so the reader is told."""

    def refuse() -> Path:
        raise OSError(28, "No space left on device", "/icons/lsdsk.png")

    recording = _RecordingShortcuts()
    seams = dataclasses.replace(_seams(recording, uvx=Path("/usr/bin/uvx")), copy_icon=refuse)
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=seams)

    assert leaving.value.code == ExitCode.IO_ERROR
    assert recording.created is None
    assert "could not write" in capsys.readouterr().err


@pytest.mark.os_linux
def test_the_real_linux_adapter_with_a_directory_where_the_file_goes_leaves_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    (tmp_path / "Desktop" / "lsdsk.desktop").mkdir(parents=True)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(
            output_format=OutputFormat.JSON, seams=_seams(LinuxDesktopShortcut(), uvx=Path("/usr/bin/uvx"))
        )

    assert leaving.value.code == ExitCode.IO_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["type"] == "IO_ERROR"


@pytest.mark.os_agnostic
def test_the_platform_dispatch_has_linux_macos_and_windows_and_nothing_else() -> None:
    assert isinstance(shortcuts_for("linux"), LinuxDesktopShortcut)
    assert isinstance(shortcuts_for("darwin"), MacosDesktopShortcut)
    assert isinstance(shortcuts_for("win32"), WindowsDesktopShortcut)
    assert shortcuts_for("aix") is None


@pytest.mark.os_linux
def test_the_real_linux_adapter_writes_a_desktop_file_and_reports_it_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    (tmp_path / "Desktop").mkdir()

    seams = _seams(LinuxDesktopShortcut(), uvx=Path("/usr/bin/uvx"))
    install_shortcut(output_format=OutputFormat.JSON, seams=seams)

    written = tmp_path / "Desktop" / "lsdsk.desktop"
    assert written.is_file()
    assert 'Exec="/usr/bin/uvx" "--exclude-newer" "7 days"' in written.read_text(encoding="utf-8")

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["command"] == "install-shortcut"
    assert envelope["data"]["shortcut"] == str(written)
    assert envelope["data"]["launch"] == list(uvx_launch_args("/usr/bin/uvx"))


@pytest.mark.parametrize("installed", [None, Path("/root/.local/bin/uvx")], ids=["no-uvx-after", "uvx-after"])
def test_a_failed_installer_is_reported_with_its_exit_code_and_the_manual_line(
    installed: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-zero installer is a failure in its own words, never 'uv was installed but ...'."""
    recording = _RecordingShortcuts()
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(
            output_format=OutputFormat.JSON,
            seams=_seams(recording, uvx=None, lsdsk=None, installed=installed, installer_exit=7),
            install_uv=True,
        )

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    assert recording.created is None
    message = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "the uv installer failed (exit 7)" in message
    assert manual_install_line(sys.platform) in message
    assert "uv was installed" not in message


@pytest.mark.os_linux
def test_a_launcher_path_with_a_newline_is_refused_with_io_error_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A newline in the path would inject a key into the entry, so the write is refused instead."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "Desktop").mkdir()

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(
            output_format=OutputFormat.JSON,
            seams=_seams(LinuxDesktopShortcut(), uvx=Path("/opt/uv\nX-Injected=1/uvx")),
        )

    assert leaving.value.code == ExitCode.IO_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["type"] == "IO_ERROR"
    assert "control character" in envelope["error"]["message"]
    assert list((tmp_path / "Desktop").iterdir()) == []


def test_the_human_report_names_the_launch_as_a_shell_would_quote_it(capsys: pytest.CaptureFixture[str]) -> None:
    """The line is for a reader to recognise or paste, so it is the shell form, not the .desktop form."""
    lsdsk = Path("/opt/my apps/lsdsk")
    install_shortcut(seams=_seams(_RecordingShortcuts(), uvx=None, lsdsk=lsdsk), platform="linux")

    out = capsys.readouterr().out
    assert f"It launches: {shlex.join((str(lsdsk),))}\n" in out


@pytest.mark.os_agnostic
def test_the_human_report_on_windows_names_the_launch_as_cmd_reads_it(capsys: pytest.CaptureFixture[str]) -> None:
    """Cmd and PowerShell know no single quotes, so the Windows line is the CreateProcess form."""
    uvx = Path("C:/Users/Jane Doe/.local/bin/uvx.exe")
    install_shortcut(seams=_seams(_RecordingShortcuts(), uvx=uvx), platform="win32")

    out = capsys.readouterr().out
    assert f'It launches: "{uvx}" --exclude-newer "7 days" ' in out
    assert "'" not in out.split("It launches: ", 1)[1]


def test_a_platform_with_no_shortcut_adapter_is_refused_with_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, platform="aix")

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["type"] == "CONFIG_ERROR"
    assert "not available on aix" in envelope["error"]["message"]


@pytest.mark.os_posix
def test_a_plain_lsdsk_found_through_a_relative_path_entry_is_launched_by_its_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shortcut runs from the Desktop, so a launcher resolved as ``bin/lsdsk`` must be made absolute.

    Driven through the real lookups and the real platform adapter: ``PATH`` holds
    one relative entry with a plain ``lsdsk`` and no ``uvx``.
    """
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lsdsk = bin_dir / "lsdsk"
    lsdsk.write_bytes(b"#!/bin/sh\nexit 0\n")
    lsdsk.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")

    install_shortcut(output_format=OutputFormat.JSON)

    launch = json.loads(capsys.readouterr().out)["data"]["launch"]
    assert launch == [str(lsdsk)]
