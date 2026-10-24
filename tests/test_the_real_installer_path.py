"""``install-shortcut --install-uv`` through the real installer path, with a stand-in installer.

Every other installer test injects a runner, so the real ``subprocess`` call - its
exit code and where the child's output goes - was never run. Here the command
is driven with no seams at all: ``PATH`` holds only a stand-in ``sh``, which is
what the POSIX installer command starts, so the real ``uvx`` lookup, the real
subprocess and the real shortcut adapter all run while nothing is downloaded and
nothing outside the test's own directory is written.

The stand-in prints to its standard output the way the real installer does,
optionally leaves a ``uvx`` behind, and exits with a chosen code. POSIX only:
the Windows installer is PowerShell, which a shell script cannot stand in for.
"""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from pathlib import Path

_STAND_IN_SH = """#!/bin/sh
PATH=/usr/bin:/bin
echo "downloading uv (stand-in installer)"
if [ "{leave_uvx}" = yes ]; then
    printf '#!/bin/sh\\nexit 0\\n' > "{bin_dir}/uvx"
    chmod 755 "{bin_dir}/uvx"
fi
exit {code}
"""


def _machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, code: int, leave_uvx: bool) -> Path:
    """A home with a Desktop and a PATH holding only the stand-in installer; returns the Desktop."""
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "sh"
    script = _STAND_IN_SH.format(leave_uvx="yes" if leave_uvx else "no", bin_dir=bin_dir, code=code)
    stand_in.write_bytes(script.encode("utf-8"))
    stand_in.chmod(stat.S_IRWXU)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return desktop


@pytest.mark.os_posix
def test_a_failing_real_installer_leaves_config_error_naming_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    desktop = _machine(tmp_path, monkeypatch, code=7, leave_uvx=False)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    out = capfd.readouterr().out
    envelope = json.loads(out[out.index("{") :])
    assert "the uv installer failed (exit 7)" in envelope["error"]["message"]
    assert list(desktop.iterdir()) == []
