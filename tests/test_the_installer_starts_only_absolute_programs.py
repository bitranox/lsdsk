"""The uv installer's own programs come only from absolute ``PATH`` directories.

The installer command starts ``sh`` on POSIX and ``powershell`` on Windows, and
the POSIX script itself starts ``curl`` and a second ``sh``. Handed to the
operating system by bare name, each was looked up the way the launcher once was:
a relative ``PATH`` entry (``.``, an empty entry) resolves against the folder
lsdsk was started from, and Windows' ``CreateProcess`` searches that folder
before ``PATH`` at all. So a ``sh`` planted in a download folder ran in place of
the installer the reader agreed to. These plant the programs beside the run, with
real files, and require that none of them is started.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.uv_bootstrap import install_uv, run_installer
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _search(*entries: str | Path) -> str:
    """A ``PATH`` value holding ``entries`` in order."""
    return os.pathsep.join(str(entry) for entry in entries)


def _write_script(path: Path, text: str) -> Path:
    """An executable POSIX script at ``path`` holding ``text``, its directory created along with it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    path.chmod(stat.S_IRWXU)
    return path


def _planted(work: Path, name: str) -> Path:
    """A ``name`` in ``work`` that leaves a marker file when it is started; returns the marker's path."""
    marker = work / f"{name}-was-started"
    # A redirection, not ``touch``: the PATH these run under may hold no directory with a touch in it.
    _write_script(work / name, f'#!/bin/sh\n: > "{marker}"\nexit 0\n')
    return marker


class _Recorder:
    """A stand-in runner that records the commands it is handed and exits 0."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return 0


@pytest.mark.os_posix
@pytest.mark.parametrize("entry", [".", ""], ids=["dot", "empty"])
def test_a_shell_planted_in_the_working_directory_is_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """With the only ``sh`` reachable through a relative entry, nothing starts and the install failed.

    It is a failed install naming the missing program, never the planted file's
    exit 0 read as an install that merely left ``uvx`` off PATH, and never a crash.
    """
    work = tmp_path / "work"
    marker = _planted(work, "sh")
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", _search(entry, tmp_path / "empty"))

    result = install_uv(run=run_installer, platform=sys.platform, search=str(tmp_path / "empty"))

    assert not marker.exists(), "the sh planted in the working directory was started"
    assert result.missing_program == "sh"
    assert result.exit_code != 0


#: A stand-in ``curl`` that records it was the one started and serves a harmless installer.
_SERVING_CURL = """#!/bin/sh
printf '%s\\n' "$@" > "{record}"
printf '%s\\n' 'echo "stand-in installer ran"'
"""


@pytest.mark.os_posix
def test_the_installer_and_its_own_programs_come_from_the_absolute_directory_behind_a_relative_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """``.`` first and a real ``sh`` behind it: the real one runs, and so do the ``curl`` and ``sh`` it starts.

    The planted ``sh`` and ``curl`` cover both halves: the installer command's
    own program, and the programs its script starts, which read the child's
    ``PATH`` - so a relative entry is dropped from that too. The absolute
    directory holds the real shell and a stand-in ``curl`` rather than being the
    real ``/bin``, whose ``curl`` would download the real installer.
    """
    real_sh = shutil.which("sh", path="/bin:/usr/bin")
    assert real_sh is not None, "a POSIX machine has an sh"
    work = tmp_path / "work"
    planted_sh = _planted(work, "sh")
    planted_curl = _planted(work, "curl")
    real = tmp_path / "real"
    real.mkdir()
    (real / "sh").symlink_to(real_sh)
    record = tmp_path / "curl-arguments.txt"
    _write_script(real / "curl", _SERVING_CURL.format(record=record))
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", _search(".", real))

    result = install_uv(run=run_installer, platform=sys.platform, search=str(tmp_path / "empty"))

    assert not planted_sh.exists(), "the sh planted in the working directory was started"
    assert not planted_curl.exists(), "the curl planted in the working directory was started"
    assert result.exit_code == 0
    assert result.missing_program is None
    assert record.exists(), "the curl in the absolute directory should have fetched the installer"
    assert "stand-in installer ran" in capfd.readouterr().err


def test_windows_powershell_is_found_only_in_an_absolute_directory_by_its_pathext_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows command's ``powershell`` is resolved the way the launcher is: absolute entries, ``PATHEXT``.

    ``CreateProcess`` handed a bare ``powershell`` searches the folder lsdsk runs
    in before ``PATH``, so a planted ``powershell.EXE`` there would run. Driven
    through the ``win32`` platform, whose suffix rule applies on every runner.
    """
    work = tmp_path / "work"
    _write_script(work / "powershell.EXE", "#!/bin/sh\nexit 0\n")
    absolute = _write_script(tmp_path / "System32" / "powershell.EXE", "#!/bin/sh\nexit 0\n")
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", _search(".", tmp_path / "empty"))
    nothing_started = _Recorder()

    missing = install_uv(run=nothing_started, platform="win32", search=str(tmp_path / "empty"))

    assert nothing_started.commands == []
    assert missing.missing_program == "powershell"
    assert missing.exit_code != 0

    monkeypatch.setenv("PATH", _search(".", absolute.parent))
    started = _Recorder()

    found = install_uv(run=started, platform="win32", search=str(tmp_path / "empty"))

    assert found.missing_program is None
    assert [command[0] for command in started.commands] == [str(absolute)]


def test_the_installer_runs_with_the_absolute_path_entries_and_the_rest_of_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child's ``PATH`` loses its relative entries and nothing else; every other variable is kept.

    Replacing the whole environment would drop ``SystemRoot`` on Windows, which
    the child needs to start at all, so only ``PATH`` is rewritten.
    """
    absolute = tmp_path / "bin"
    monkeypatch.setenv("PATH", _search(".", "", "relative", absolute))
    monkeypatch.setenv("LSDSK_TEST_KEPT_VARIABLE", "kept")
    seen = tmp_path / "seen.json"
    child = [
        sys.executable,
        "-c",
        "import json, os, sys; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(dict(os.environ)))",
        str(seen),
    ]

    assert run_installer(child) == 0

    environment = json.loads(seen.read_bytes().decode("utf-8"))
    path_key = next(key for key in environment if key.upper() == "PATH")
    assert environment[path_key] == str(absolute)
    assert environment["LSDSK_TEST_KEPT_VARIABLE"] == "kept"


@pytest.mark.os_posix
def test_install_shortcut_says_which_installer_program_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """End to end: a ``sh`` only in the working directory is a failed install that names ``sh``, and no shortcut."""
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    work = tmp_path / "work"
    marker = _planted(work, "sh")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("PATH", _search(".", tmp_path / "empty"))

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    message = json.loads(capfd.readouterr().out)["error"]["message"]
    assert "the uv installer could not start: no 'sh' in an absolute PATH directory" in message
    assert not marker.exists()
    assert list(desktop.iterdir()) == []


def test_windows_powershell_is_looked_up_by_the_suffixes_the_environment_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``PATHEXT`` in the environment is the one the lookup uses, not Windows' default.

    ``.PY`` is in no default, so a ``powershell.PY`` is found only by a lookup
    that read the variable. Driven through the ``win32`` platform, whose suffix
    rule applies on every runner.
    """
    programs = tmp_path / "programs"
    powershell = _write_script(programs / "powershell.PY", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", _search(programs))
    monkeypatch.setenv("PATHEXT", ".PY")
    started = _Recorder()

    result = install_uv(run=started, platform="win32", search=str(tmp_path / "empty"))

    assert result.missing_program is None
    assert [command[0] for command in started.commands] == [str(powershell)]


@pytest.mark.parametrize("suffix", [".COM", ".EXE", ".BAT", ".CMD"])
def test_windows_powershell_is_found_by_each_default_suffix_where_the_environment_names_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """With no ``PATHEXT`` set, each suffix Windows' own default lists still finds the program.

    An environment without the variable is not one without executables: a
    lookup that then tried the bare name alone, or fewer suffixes than Windows
    does, would call a ``powershell.CMD`` missing and refuse the install.
    """
    programs = tmp_path / "programs"
    powershell = _write_script(programs / f"powershell{suffix}", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", _search(programs))
    monkeypatch.delenv("PATHEXT", raising=False)
    started = _Recorder()

    result = install_uv(run=started, platform="win32", search=str(tmp_path / "empty"))

    assert result.missing_program is None
    assert [command[0] for command in started.commands] == [str(powershell)]
