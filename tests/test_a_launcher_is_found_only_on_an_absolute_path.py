"""The shortcut's launcher is looked up only in the absolute directories on ``PATH``.

The Windows shortcut runs as administrator on every double-click, so whatever
the lookup answers becomes elevated code. ``shutil.which`` searches the current
directory first on Windows, and a relative ``PATH`` entry is resolved against the
current directory everywhere, so a ``uvx`` or ``lsdsk`` planted in the folder
lsdsk was started from (a download folder, a cloned repository) would become the
shortcut's target. Only an absolute ``PATH`` directory names the same file
wherever lsdsk runs, so nothing else is searched.

These use real files and the real lookup, so the Windows cells of CI exercise the
current-directory insert itself.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.uv_bootstrap import executable_on_path, uvx_path
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from pathlib import Path

_SUFFIX = ".exe" if sys.platform == "win32" else ""


def _plant(directory: Path, name: str) -> Path:
    """An executable file called ``name`` in ``directory``, created along with it."""
    directory.mkdir(parents=True, exist_ok=True)
    planted = directory / f"{name}{_SUFFIX}"
    planted.write_bytes(b"#!/bin/sh\nexit 0\n")
    planted.chmod(stat.S_IRWXU)
    return planted


def _search(*entries: str | Path) -> str:
    """A ``PATH`` value holding ``entries`` in order."""
    return os.pathsep.join(str(entry) for entry in entries)


def test_a_launcher_in_the_working_directory_is_never_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The folder lsdsk runs in is not searched, on Windows where the lookup inserts it least of all."""
    work = tmp_path / "work"
    _plant(work, "uvx")
    monkeypatch.chdir(work)

    assert uvx_path(search=_search(tmp_path / "empty")) is None


@pytest.mark.parametrize("entry", ["", ".", "bin"], ids=["empty", "dot", "relative-dir"])
def test_a_relative_path_entry_is_never_searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    """An empty, ``.`` or relative entry names a different folder wherever lsdsk runs, so it is skipped."""
    work = tmp_path / "work"
    _plant(work, "uvx")
    _plant(work / "bin", "uvx")
    monkeypatch.chdir(work)

    assert uvx_path(search=_search(entry, tmp_path / "empty")) is None


def test_an_absolute_entry_after_a_relative_one_is_the_one_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping a relative entry does not stop the search: the absolute directory behind it still answers."""
    work = tmp_path / "work"
    _plant(work / "bin", "uvx")
    real = _plant(tmp_path / "real", "uvx")
    monkeypatch.chdir(work)

    found = uvx_path(search=_search("bin", tmp_path / "real"))

    assert found is not None
    assert found.is_absolute()
    assert found.samefile(real)


def test_a_plainly_installed_lsdsk_is_looked_up_the_same_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback launcher has the same lookup, so neither name can be planted in the working directory."""
    work = tmp_path / "work"
    _plant(work, "lsdsk")
    real = _plant(tmp_path / "real", "lsdsk")
    monkeypatch.chdir(work)

    assert executable_on_path("lsdsk", search=_search(".", tmp_path / "empty")) is None
    found = executable_on_path("lsdsk", search=_search(".", tmp_path / "real"))
    assert found is not None
    assert found.samefile(real)


@pytest.mark.os_posix
def test_install_shortcut_ignores_launchers_planted_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """End to end: with ``uvx`` and ``lsdsk`` only in the working directory, there is no launcher and no shortcut."""
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    work = tmp_path / "work"
    _plant(work, "uvx")
    _plant(work, "lsdsk")
    _plant(work / "bin", "uvx")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("PATH", _search("", ".", "bin", tmp_path / "empty"))

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=False)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    envelope = json.loads(capfd.readouterr().out)
    assert "uv is not installed, so there is no launcher for the shortcut" in envelope["error"]["message"]
    assert list(desktop.iterdir()) == []


def test_a_windows_launcher_is_found_by_its_pathext_suffix(tmp_path: Path) -> None:
    """A directory holding only ``uvx.EXE`` answers for ``uvx`` where ``PATHEXT`` lists ``.EXE``.

    The suffix is added by the lookup itself rather than left to
    ``shutil.which``: on Python 3.11 ``which`` checks a name carrying a
    directory exactly as written, so ``uvx`` behind a directory never matched ``uvx.exe`` and
    every Windows machine on 3.11 read as having no uv. The Windows rule is
    driven here through ``pathext`` so every runner holds it.
    """
    directory = tmp_path / "bin"
    directory.mkdir()
    launcher = directory / "uvx.EXE"
    launcher.write_bytes(b"MZ")
    launcher.chmod(stat.S_IRWXU)

    assert executable_on_path("uvx", search=str(directory), pathext=".COM;.EXE") == launcher
    assert executable_on_path("uvx", search=str(directory), pathext=None) is None


@pytest.mark.os_posix
def test_a_file_without_the_execute_bit_is_passed_over_for_the_executable_behind_it(tmp_path: Path) -> None:
    """A ``uvx`` the account may not execute is no launcher, so the executable one later on ``PATH`` answers.

    POSIX only: Windows grants execute to every file ``os.access`` is asked about.
    Root is no exception here, since a file with no execute bit at all is not
    executable for root either.
    """
    readable_only = tmp_path / "first"
    readable_only.mkdir()
    (readable_only / "uvx").write_bytes(b"#!/bin/sh\nexit 0\n")
    (readable_only / "uvx").chmod(0o644)
    real = _plant(tmp_path / "second", "uvx")

    found = uvx_path(search=_search(readable_only, tmp_path / "second"))

    assert found is not None
    assert found.samefile(real)


@pytest.mark.os_posix
def test_a_directory_the_account_cannot_enter_is_passed_over_without_raising(tmp_path: Path) -> None:
    """A mode-000 directory on ``PATH`` holding ``uvx`` answers nothing, and the next directory answers.

    Before Python 3.14 ``Path.is_file`` re-raises the refused ``stat`` as a
    ``PermissionError``, so without the guard the whole lookup failed on the
    oldest supported Python and passed on the newest. Root enters any
    directory, so the refusal cannot be made there.
    """
    # Asked through sys.platform, which the type checker narrows on; Windows has no geteuid.
    if sys.platform != "win32" and os.geteuid() == 0:
        pytest.skip("root enters a mode-000 directory, so the refused stat cannot be produced")
    locked = tmp_path / "locked"
    _plant(locked, "uvx")
    real = _plant(tmp_path / "real", "uvx")
    locked.chmod(0)
    try:
        found = uvx_path(search=_search(locked, tmp_path / "real"))
    finally:
        locked.chmod(stat.S_IRWXU)

    assert found is not None
    assert found.samefile(real)
