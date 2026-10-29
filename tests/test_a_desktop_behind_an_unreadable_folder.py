"""A Desktop folder behind a parent this account cannot enter: no desktop, never a traceback.

Before Python 3.14, ``Path.is_dir`` and ``Path.is_file`` re-raise the
``PermissionError`` a ``stat`` meets under a parent with no search permission,
where 3.14 answers ``False``. Each shortcut adapter asks exactly those questions
of the Desktop folder, so on 3.11-3.13 ``install-shortcut`` left with exit 13
and no JSON envelope, and the offer before the interactive view raised out of
it. These hold the 3.14 answer on every interpreter the project supports: a
folder that cannot be reached is no folder to put a shortcut in.

A folder with mode 000 is the parent here, which only a POSIX account other
than root is refused by; the fixture skips where the refusal does not happen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut, offer_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut
from lsdsk.adapters.shortcut.windows import WindowsDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from lsdsk.application.ports import DesktopShortcuts
    from lsdsk.domain.enums import ShortcutChoice

    #: Builds one platform's adapter with its Desktop behind the locked folder.
    _Adapter = Callable[[Path, pytest.MonkeyPatch], DesktopShortcuts]


@pytest.fixture
def locked(tmp_path: Path) -> Iterator[Path]:
    """A folder holding ``Desktop/`` and ``home/Desktop/``, then closed to this account.

    Its mode is put back afterwards so pytest can remove the tree.
    """
    folder = tmp_path / "locked"
    (folder / "home" / "Desktop").mkdir(parents=True)
    (folder / "Desktop").mkdir()
    folder.chmod(0o000)
    try:
        try:
            (folder / "Desktop").stat()
        except PermissionError:
            yield folder
        else:
            pytest.skip("this account is not refused by a mode-000 folder (root, or a filesystem ignoring modes)")
    finally:
        folder.chmod(0o755)


def _seams(shortcuts: DesktopShortcuts) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: Path("/usr/bin/uvx"),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=1, uvx=None),
    )


def _never_asked() -> ShortcutChoice:
    raise AssertionError("the offer asked although there is no desktop to put a shortcut on")


class _KnownFolder:
    DESKTOP = "Desktop"
    PROGRAMS = "Programs"


class _FakeError(Exception):
    """Stands in for pwshpy's ``PwshPyError``."""


@dataclass
class _FakePs:
    """Names the known folders from a mapping and records each shortcut it is asked to write."""

    folders: dict[str, Path]
    written: list[str] = field(default_factory=list[str])

    def get_known_folder(self, folder: object) -> Path | None:
        return self.folders.get(str(folder))

    def new_shortcut(self, path: object, **fields: object) -> object:
        del fields
        self.written.append(str(path))
        return object()


@dataclass
class _FakePwshpy:
    ps: _FakePs
    KnownFolder: _KnownFolder = field(default_factory=_KnownFolder)
    PwshPyError: type[Exception] = _FakeError


def _linux(locked: Path, monkeypatch: pytest.MonkeyPatch) -> LinuxDesktopShortcut:
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(locked / "Desktop"))
    return LinuxDesktopShortcut(config_home=locked.parent / "cfg")


def _macos(locked: Path, monkeypatch: pytest.MonkeyPatch) -> MacosDesktopShortcut:
    monkeypatch.setenv("HOME", str(locked / "home"))
    return MacosDesktopShortcut()


def _windows(locked: Path, monkeypatch: pytest.MonkeyPatch) -> WindowsDesktopShortcut:
    del monkeypatch
    ps = _FakePs(folders={"Desktop": locked / "Desktop", "Programs": locked / "Desktop"})
    return WindowsDesktopShortcut(load=lambda: _FakePwshpy(ps=ps))


_ADAPTERS = [
    pytest.param(_linux, id="linux"),
    pytest.param(_macos, id="macos"),
    pytest.param(_windows, id="windows"),
]


@pytest.mark.os_posix
@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_a_desktop_that_cannot_be_reached_is_no_desktop_and_no_shortcut(
    locked: Path, monkeypatch: pytest.MonkeyPatch, adapter: _Adapter
) -> None:
    shortcuts = adapter(locked, monkeypatch)

    assert shortcuts.has_desktop() is False
    assert shortcuts.exists() is False


@pytest.mark.os_posix
@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_install_shortcut_refuses_an_unreachable_desktop_with_its_envelope(
    locked: Path, monkeypatch: pytest.MonkeyPatch, adapter: _Adapter, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typed refusal a caller can parse, not a ``PermissionError`` turned into exit 13 with nothing on stdout."""
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, seams=_seams(adapter(locked, monkeypatch)))

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CONFIG_ERROR"
    assert "no desktop folder at" in envelope["error"]["message"]


@pytest.mark.os_posix
@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_the_offer_stays_silent_for_an_unreachable_desktop(
    locked: Path, monkeypatch: pytest.MonkeyPatch, adapter: _Adapter
) -> None:
    """The offer runs before the interactive view, so a raise here would end the view the reader started."""
    offer_shortcut(DisplaySettings(), ask_choice=_never_asked, seams=_seams(adapter(locked, monkeypatch)))


@pytest.mark.os_posix
def test_a_start_menu_that_cannot_be_reached_is_skipped_quietly(tmp_path: Path, locked: Path) -> None:
    """The Desktop ``.lnk`` is written; a Programs folder behind a closed parent is no Start menu to add to."""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    ps = _FakePs(folders={"Desktop": desktop, "Programs": locked / "Desktop"})

    written = WindowsDesktopShortcut(load=lambda: _FakePwshpy(ps=ps)).create(
        launch=("C:/uv/uvx.exe", "lsdsk@latest"), icon=None
    )

    assert written.path == desktop / "lsdsk.lnk"
    assert ps.written == [str(desktop / "lsdsk.lnk")]
    assert written.skipped == ()
