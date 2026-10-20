"""The one consent-gated path that starts a subprocess and reaches the network.

lsdsk otherwise shells out to nothing and opens no socket. When a reader accepts
the desktop shortcut and ``uvx`` is not installed, lsdsk offers to install uv for
them from the official installer; that is the single exception, confined to this
module and guarded by ``test_no_subprocess_or_network``. These drive it with a
fake runner and a fake ``which``, so the real installer never runs here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.uv_bootstrap import install_uv, uvx_path
from lsdsk.domain.shortcut import UV_INSTALLER_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Recorder:
    """A stand-in runner that records the commands it is handed and returns a fixed code."""

    def __init__(self, code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.code = code

    def __call__(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return self.code


@pytest.mark.os_agnostic
def test_uvx_path_follows_what_which_finds() -> None:
    """Resolution is injected, so the answer is exactly what the lookup returned."""
    assert uvx_path(which=lambda _name: None) is None
    assert uvx_path(which=lambda _name: "/opt/bin/uvx") == Path("/opt/bin/uvx")


@pytest.mark.os_agnostic
def test_the_posix_installer_runs_the_pinned_astral_shell_script() -> None:
    """On POSIX the command fetches the pinned install.sh and pipes it to a shell."""
    runner = _Recorder()
    result = install_uv(run=runner, platform="linux", which=lambda _name: "/opt/bin/uvx")

    assert result == Path("/opt/bin/uvx")
    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command[0] == "sh"
    assert "curl" in command[-1]
    assert f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.sh" in command[-1]


@pytest.mark.os_agnostic
def test_the_windows_installer_runs_the_pinned_astral_powershell_script() -> None:
    """On Windows the command fetches the pinned install.ps1 through PowerShell."""
    runner = _Recorder()
    result = install_uv(run=runner, platform="win32", which=lambda _name: None)

    assert result is None  # the fake which still finds no uvx after the fake install
    command = runner.commands[0]
    assert command[0] == "powershell"
    assert f"https://astral.sh/uv/{UV_INSTALLER_VERSION}/install.ps1" in command[-1]


@pytest.mark.os_agnostic
def test_the_uvx_path_is_re_resolved_after_the_installer_runs() -> None:
    """A successful install makes uvx resolvable, which is the signal the caller needs."""
    runner = _Recorder()
    result = install_uv(run=runner, platform="linux", which=lambda _name: "/home/me/.local/bin/uvx")

    assert result == Path("/home/me/.local/bin/uvx")
