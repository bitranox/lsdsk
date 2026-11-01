"""The one consent-gated path that starts a subprocess and reaches the network.

lsdsk otherwise shells out to nothing and opens no socket. When a reader accepts
the desktop shortcut and ``uvx`` is not installed, lsdsk offers to install uv for
them from the official installer; that is the single exception, confined to this
module and guarded by ``test_no_subprocess_or_network``. These drive it with a
fake runner and a ``PATH`` of their own, so the real installer never runs here.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.uv_bootstrap import UvInstallResult, install_uv, run_installer, uvx_path
from lsdsk.domain.shortcut import UV_INSTALLER_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _uvx_in(directory: Path) -> Path:
    """A real executable ``uvx`` in ``directory``, created along with it."""
    directory.mkdir(parents=True, exist_ok=True)
    uvx = directory / ("uvx.exe" if sys.platform == "win32" else "uvx")
    uvx.write_bytes(b"#!/bin/sh\nexit 0\n")
    uvx.chmod(0o755)
    return uvx


class _Recorder:
    """A stand-in runner that records the commands it is handed and returns a fixed code."""

    def __init__(self, code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.code = code

    def __call__(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return self.code


def test_uvx_path_answers_the_uvx_in_the_searched_directory(tmp_path: Path) -> None:
    """The answer is the file in the ``PATH`` handed in, or ``None`` where that holds none."""
    uvx = _uvx_in(tmp_path / "bin")

    assert uvx_path(search=str(tmp_path / "empty")) is None
    found = uvx_path(search=str(tmp_path / "bin"))
    assert found is not None
    assert found.samefile(uvx)


@pytest.mark.os_agnostic
def test_the_posix_installer_runs_the_pinned_astral_shell_script(tmp_path: Path) -> None:
    """On POSIX the command fetches the pinned install.sh with curl and runs it in a shell.

    The URL is the script's ``$1``, an argument of its own, so it is never part
    of the text the shell parses; ``$0`` is a name for the step, which the shell
    puts in front of its own error lines.
    """
    runner = _Recorder()
    uvx = _uvx_in(tmp_path / "bin")
    result = install_uv(run=runner, platform="linux", search=str(tmp_path / "bin"))

    assert result == UvInstallResult(exit_code=0, uvx=uvx)
    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command[:2] == ["sh", "-c"]
    assert "curl" in command[2]
    assert "astral.sh" not in command[2]
    assert command[3:] == ["lsdsk-uv-installer", f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.sh"]


@pytest.mark.os_agnostic
def test_the_windows_installer_runs_the_pinned_astral_powershell_script(tmp_path: Path) -> None:
    """On Windows the command fetches the pinned install.ps1 through PowerShell."""
    runner = _Recorder()
    result = install_uv(run=runner, platform="win32", search=str(tmp_path))

    assert result.uvx is None  # the fake install left no uvx in the searched directory
    command = runner.commands[0]
    assert command[0] == "powershell"
    assert f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.ps1" in command[-1]


@pytest.mark.os_agnostic
def test_the_uvx_path_is_re_resolved_after_the_installer_runs(tmp_path: Path) -> None:
    """A successful install makes uvx resolvable, which is the signal the caller needs.

    The ``uvx`` appears only while the installer runs, so a lookup made before it
    would have answered ``None``.
    """
    bin_dir = tmp_path / "bin"

    def installs(command: Sequence[str]) -> int:
        del command
        _uvx_in(bin_dir)
        return 0

    result = install_uv(run=installs, platform="linux", search=str(bin_dir))

    assert result.uvx is not None
    assert result.uvx.samefile(bin_dir / result.uvx.name)


def test_the_installers_exit_code_comes_back_with_the_result(tmp_path: Path) -> None:
    """A failed installer is not reported as a success that merely left uvx off PATH."""
    result = install_uv(run=_Recorder(code=7), platform="linux", search=str(tmp_path))

    assert result.exit_code == 7
    assert result.uvx is None


def test_the_real_runner_returns_the_exit_code_and_keeps_the_childs_output_off_standard_output(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A failing child is an exit code, not an exception, and what it prints goes to standard error.

    Standard output belongs to the JSON envelope that follows the install, so the
    installer's progress lines there would make it unparseable.
    """
    child = [sys.executable, "-c", "import sys; print('installer chatter'); sys.exit(3)"]

    assert run_installer(child) == 3
    captured = capfd.readouterr()
    assert "installer chatter" not in captured.out
    assert "installer chatter" in captured.err
