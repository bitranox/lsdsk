"""A launch or icon path UTF-8 cannot encode is refused like a control character, never a crash.

On POSIX a filename is bytes, and Python reads a byte that is not valid UTF-8 as
a lone surrogate. The shortcut files are UTF-8 text - the Desktop Entry spec
requires it - so such a path cannot be written into one. Left to the encoder it
raised ``UnicodeEncodeError``: ``install-shortcut`` exited 70 with a traceback
and no envelope, and the offer stopped the interactive view from opening. It is
refused instead with the ``OSError`` a control character already gets.
"""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut, offer_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut, UnwritableDesktopEntryError
from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut, UnwritableCommandFileError
from lsdsk.adapters.uv_bootstrap import UvInstallResult, uvx_path
from lsdsk.domain.enums import OutputFormat, ShortcutChoice

if TYPE_CHECKING:
    from pathlib import Path

#: What Python makes of byte 0xFF in a filename: a lone surrogate, built here rather than typed.
_UNDECODABLE = chr(0xDCFF)
_REFUSAL = "is not valid UTF-8"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "home" / "Desktop").mkdir(parents=True)
    return tmp_path / "home"


def _written(home: Path) -> list[Path]:
    return sorted(p for p in home.rglob("*") if p.is_file())


@pytest.mark.os_linux
def test_a_launch_argument_that_is_not_utf8_is_refused_by_the_linux_writer(home: Path) -> None:
    with pytest.raises(UnwritableDesktopEntryError, match=_REFUSAL):
        LinuxDesktopShortcut().create(launch=(f"/opt/bin{_UNDECODABLE}/uvx", "lsdsk@latest"), icon=None)

    assert _written(home) == []


@pytest.mark.os_linux
def test_an_icon_path_that_is_not_utf8_is_refused_by_the_linux_writer(home: Path) -> None:
    with pytest.raises(UnwritableDesktopEntryError, match=_REFUSAL):
        LinuxDesktopShortcut().create(launch=("/opt/bin/uvx",), icon=home / f"icon{_UNDECODABLE}.png")

    assert _written(home) == []


@pytest.mark.os_posix
def test_a_launch_argument_that_is_not_utf8_is_refused_by_the_macos_writer(home: Path) -> None:
    with pytest.raises(UnwritableCommandFileError, match=_REFUSAL) as refused:
        MacosDesktopShortcut().create(launch=(f"/opt/bin{_UNDECODABLE}/uvx", "lsdsk@latest"), icon=None)

    assert isinstance(refused.value, OSError)
    assert _written(home) == []


def _plant_uvx_under_an_undecodable_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``uvx`` in a directory whose name ends in byte 0xFF, that directory alone on PATH.

    The name is spelt with the surrogate Python reads that byte as, which pathlib
    encodes back to the byte itself on POSIX.
    """
    directory = tmp_path / f"bin{_UNDECODABLE}"
    directory.mkdir()
    assert os.fsencode(directory).endswith(b"/bin\xff"), "the directory must hold the raw byte, not its surrogate"
    uvx = directory / "uvx"
    uvx.write_bytes(b"#!/bin/sh\nexit 0\n")
    uvx.chmod(stat.S_IRWXU)
    monkeypatch.setenv("PATH", str(directory))


@pytest.mark.os_linux
def test_install_shortcut_refuses_a_launcher_path_that_is_not_utf8_with_an_envelope(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """End to end: exit 74 with a JSON envelope naming why, where it was exit 70 and a traceback."""
    _plant_uvx_under_an_undecodable_directory(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON)

    assert leaving.value.code == ExitCode.IO_ERROR
    error = json.loads(capfd.readouterr().out)["error"]
    assert error["type"] == "IO_ERROR"
    assert _REFUSAL in error["message"]
    assert _written(home) == []


def _no_install() -> UvInstallResult:
    raise AssertionError("uvx is on PATH, so nothing is installed")


@pytest.mark.os_linux
def test_the_offer_says_why_and_lets_the_view_open(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The offer returns, so the view opens, having said it could not create the shortcut."""
    _plant_uvx_under_an_undecodable_directory(tmp_path, monkeypatch)
    seams = ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(), uvx=uvx_path, installed_lsdsk=lambda: None, install_uv=_no_install
    )

    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.CREATE, seams=seams)

    err = capfd.readouterr().err
    assert "Could not create the desktop shortcut:" in err
    assert _REFUSAL in err
    assert _written(home) == []
