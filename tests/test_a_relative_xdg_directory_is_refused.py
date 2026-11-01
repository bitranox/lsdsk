"""A relative XDG directory is refused, never resolved against the folder lsdsk runs in.

``XDG_DESKTOP_DIR``, ``XDG_DATA_HOME`` and ``XDG_CONFIG_HOME`` decide where the
Linux shortcut goes and which ``user-dirs.dirs`` names the Desktop. A relative
value names a different folder wherever lsdsk is started, so it is the reader's
environment that is wrong: ``install-shortcut`` refuses with ``CONFIG_ERROR``
naming the variable and writes nothing, and the offer says it could not create
the shortcut and lets the view open. That is the rule a relative
``XDG_CONFIG_HOME`` already followed for the icon.

These drive the real Linux adapter over real files under ``tmp_path``.
"""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut, offer_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult, uvx_path
from lsdsk.domain.enums import OutputFormat, ShortcutChoice

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.os_linux


class _Machine:
    """A home with a Desktop, a ``uvx`` on an absolute PATH entry, and an empty folder lsdsk runs in."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, set_config_home: Callable[[str], str]) -> None:
        self.home = tmp_path / "home"
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir(parents=True)
        self.work = tmp_path / "work"
        self.work.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        uvx = bin_dir / "uvx"
        uvx.write_bytes(b"#!/bin/sh\nexit 0\n")
        uvx.chmod(stat.S_IRWXU)
        monkeypatch.chdir(self.work)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("PATH", str(bin_dir))
        monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        set_config_home(str(tmp_path / "config"))
        self.set_config_home = set_config_home
        self.planted: set[Path] = set()

    def written(self) -> list[Path]:
        """Every file under the working folder and the Desktop that the test did not plant itself."""
        found = (p for root in (self.work, self.desktop) for p in root.rglob("*") if p.is_file())
        return sorted(p for p in found if p not in self.planted)


@pytest.fixture
def machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linux_config_home_set_to: Callable[[str], str]
) -> _Machine:
    return _Machine(tmp_path, monkeypatch, linux_config_home_set_to)


#: The config home is set through its conftest fixture, the one file allowed to name it.
_CONFIG_HOME_ARM = "config-home"


def _relative(monkeypatch: pytest.MonkeyPatch, machine: _Machine, arm: str) -> str:
    """Set the arm's variable to a relative folder that exists under the working folder; its name.

    The folder exists so the relative value would be usable if it were followed.
    """
    (machine.work / "rel").mkdir()
    if arm != _CONFIG_HOME_ARM:
        monkeypatch.setenv(arm, "rel")
        return arm
    variable = machine.set_config_home("rel")
    # A user-dirs.dirs in the working folder naming that same folder as the Desktop.
    planted = machine.work / "rel" / "user-dirs.dirs"
    planted.write_text(f'XDG_DESKTOP_DIR="{machine.work / "rel"}"\n')
    machine.planted.add(planted)
    return variable


_ARMS = ["XDG_DESKTOP_DIR", "XDG_DATA_HOME", _CONFIG_HOME_ARM]


@pytest.mark.parametrize("arm", _ARMS)
def test_install_shortcut_refuses_a_relative_xdg_directory_and_writes_nothing(
    machine: _Machine, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], arm: str
) -> None:
    """The refusal is a configuration error naming the variable, made before anything is written."""
    variable = _relative(monkeypatch, machine, arm)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    message = json.loads(capfd.readouterr().out)["error"]["message"]
    assert message == (
        f"the shortcut was not written: {variable}=rel is not an absolute path; "
        f"set {variable} to an absolute path or unset it"
    )
    assert machine.written() == []


def test_install_shortcut_with_absolute_xdg_directories_writes_the_shortcut(
    machine: _Machine, capfd: pytest.CaptureFixture[str]
) -> None:
    """Control: the same machine with every variable absolute gets its shortcut, so the refusal is the rule."""
    install_shortcut(output_format=OutputFormat.JSON)

    envelope = json.loads(capfd.readouterr().out)
    assert envelope["ok"] is True
    assert machine.written() == [machine.desktop / "lsdsk.desktop"]


def _no_install() -> UvInstallResult:
    raise AssertionError("uvx is on PATH, so nothing is installed")


def _offer_seams() -> ShortcutSeams:
    """The real Linux adapter and the real ``uvx`` lookup; installing uv would be a test failure."""
    return ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(), uvx=uvx_path, installed_lsdsk=lambda: None, install_uv=_no_install
    )


@pytest.mark.parametrize("arm", _ARMS)
def test_the_offer_says_it_could_not_create_the_shortcut_and_returns(
    machine: _Machine, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], arm: str
) -> None:
    """The view opens in every case: the offer prints why and returns, writing nothing."""
    variable = _relative(monkeypatch, machine, arm)

    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.CREATE, seams=_offer_seams())

    err = capfd.readouterr().err
    assert "Could not create the desktop shortcut:" in err
    assert f"set {variable} to an absolute path or unset it" in err
    assert machine.written() == []


def test_the_offer_stays_silent_about_a_relative_directory_when_it_is_turned_off(
    machine: _Machine, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A reader who turned the offer off is not told about a shortcut they did not want."""
    _relative(monkeypatch, machine, "XDG_DESKTOP_DIR")

    offer_shortcut(
        DisplaySettings(offer_desktop_shortcut=False),
        ask_choice=lambda: ShortcutChoice.CREATE,
        seams=_offer_seams(),
    )

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert machine.written() == []


def test_install_shortcut_refuses_a_relative_config_home_before_it_installs_uv(
    machine: _Machine, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The icon's folder is named before uv is installed, even where the Desktop needs no ``user-dirs.dirs``.

    With ``XDG_DESKTOP_DIR`` set the Desktop is found without reading the config
    home, so the relative ``XDG_CONFIG_HOME`` is first met by the icon copy - which
    used to come after uv had been installed for a shortcut that was then refused.
    """
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(machine.desktop))
    (machine.work / "rel").mkdir()
    variable = machine.set_config_home("rel")
    installs: list[str] = []

    def recording_install() -> UvInstallResult:
        installs.append("installed")
        # A uvx it now resolves to, so an install that did run would go on to the icon copy.
        return UvInstallResult(exit_code=0, uvx=machine.home / "uvx")

    seams = ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(), uvx=lambda: None, installed_lsdsk=lambda: None, install_uv=recording_install
    )
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True, seams=seams, platform="linux")

    assert installs == []
    assert leaving.value.code == ExitCode.CONFIG_ERROR
    message = json.loads(capfd.readouterr().out)["error"]["message"]
    assert message.startswith("the shortcut was not written: ")
    assert f"set {variable} to an absolute path or unset it" in message
    assert machine.written() == []
