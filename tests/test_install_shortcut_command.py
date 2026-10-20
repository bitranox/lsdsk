"""The ``install-shortcut`` command: resolve a launcher, then write the shortcut.

The branch logic is driven through an injected recording double at the
DesktopShortcuts seam, so no real installer runs and no file is written to the
developer's own Desktop; one os_linux case drives the real Linux adapter against
a redirected HOME to prove the file is actually written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lsdsk.adapters.cli.commands.shortcut import (
    ShortcutSeams,
    install_shortcut,
    shortcuts_for,
)
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.domain.enums import OutputFormat
from lsdsk.domain.shortcut import uvx_launch_args


class _RecordingShortcuts:
    """A DesktopShortcuts double that records what it was told to create."""

    def __init__(self) -> None:
        self.created: tuple[tuple[str, ...], Path | None] | None = None

    def exists(self) -> bool:
        return False

    def create(self, *, launch: object, icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)  # type: ignore[arg-type]
        return Path("/desktop/lsdsk.desktop")

    def desktop_dir(self) -> Path:
        return Path("/desktop")


def _seams(
    shortcuts: _RecordingShortcuts,
    *,
    uvx: Path | None = None,
    lsdsk: Path | None = None,
    installed: Path | None = None,
) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: uvx,
        installed_lsdsk=lambda: lsdsk,
        install_uv=lambda: installed,
    )


@pytest.mark.os_agnostic
def test_a_present_uvx_is_what_the_shortcut_is_told_to_run() -> None:
    recording = _RecordingShortcuts()
    install_shortcut(seams=_seams(recording, uvx=Path("/usr/bin/uvx")))
    assert recording.created is not None
    assert recording.created[0] == uvx_launch_args("/usr/bin/uvx")


@pytest.mark.os_agnostic
def test_a_plainly_installed_lsdsk_is_the_fallback_launcher() -> None:
    recording = _RecordingShortcuts()
    install_shortcut(seams=_seams(recording, uvx=None, lsdsk=Path("/usr/local/bin/lsdsk")))
    assert recording.created is not None
    assert recording.created[0] == ("/usr/local/bin/lsdsk",)


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
    install_shortcut(
        seams=_seams(recording, uvx=None, lsdsk=None, installed=Path("/root/.local/bin/uvx")),
        install_uv=True,
    )
    assert recording.created is not None
    assert recording.created[0] == uvx_launch_args("/root/.local/bin/uvx")


@pytest.mark.os_agnostic
def test_an_install_that_still_yields_no_uvx_fails_without_writing(capsys: pytest.CaptureFixture[str]) -> None:
    recording = _RecordingShortcuts()
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=_seams(recording, uvx=None, lsdsk=None, installed=None), install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    assert recording.created is None
    _ = capsys.readouterr()


@pytest.mark.os_agnostic
def test_the_platform_dispatch_has_linux_and_leaves_the_rest_pending() -> None:
    assert isinstance(shortcuts_for("linux"), LinuxDesktopShortcut)
    assert shortcuts_for("darwin") is None
    assert shortcuts_for("win32") is None


@pytest.mark.os_linux
def test_the_real_linux_adapter_writes_a_desktop_file_and_reports_it_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    (tmp_path / "Desktop").mkdir()

    seams = _seams(LinuxDesktopShortcut(), uvx=Path("/usr/bin/uvx"))  # type: ignore[arg-type]
    install_shortcut(output_format=OutputFormat.JSON, seams=seams)

    written = tmp_path / "Desktop" / "lsdsk.desktop"
    assert written.is_file()
    assert "Exec=/usr/bin/uvx --exclude-newer" in written.read_text(encoding="utf-8")

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["command"] == "install-shortcut"
    assert envelope["data"]["shortcut"] == str(written)
    assert envelope["data"]["launch"] == list(uvx_launch_args("/usr/bin/uvx"))
