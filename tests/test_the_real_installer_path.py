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
import shutil
import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.domain.enums import OutputFormat
from lsdsk.domain.shortcut import UV_INSTALLER_URL_POSIX

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
    envelope = json.loads(capfd.readouterr().out)
    assert "the uv installer failed (exit 7)" in envelope["error"]["message"]
    assert list(desktop.iterdir()) == []


@pytest.mark.os_posix
def test_the_installers_own_output_never_reaches_the_json_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Standard output is the envelope alone; the installer's chatter goes to standard error."""
    desktop = _machine(tmp_path, monkeypatch, code=0, leave_uvx=True)

    install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    captured = capfd.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["ok"] is True
    assert envelope["data"]["launch"][0] == str(tmp_path / "bin" / "uvx")
    assert "downloading uv (stand-in installer)" in captured.err
    assert len(list(desktop.iterdir())) == 1


#: A stand-in ``curl`` that records the arguments it was given, then fails the way a
#: real ``curl`` does when the host cannot be resolved (its exit 6).
_FAILING_CURL = """#!/bin/sh
printf '%s\\n' "$@" > "{record}"
echo "curl: (6) Could not resolve host: astral.sh" >&2
exit 6
"""

#: A stand-in ``curl`` that records its arguments and "downloads" a tiny installer:
#: the script it prints leaves a ``uvx`` behind and exits 0.
_SERVING_CURL = """#!/bin/sh
PATH=/usr/bin:/bin
printf '%s\\n' "$@" > "{record}"
cat <<'SCRIPT'
PATH=/usr/bin:/bin
echo "installing uv (stand-in script)"
printf '#!/bin/sh\\nexit 0\\n' > "{bin_dir}/uvx"
chmod 755 "{bin_dir}/uvx"
exit 0
SCRIPT
"""


def _machine_with_curl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, curl: str) -> tuple[Path, Path]:
    """A home with a Desktop, and a PATH holding the real ``sh`` beside a stand-in ``curl``.

    Unlike :func:`_machine`, the shell is the real one, so the installer command's
    own script runs and decides what a failed download comes to. Returns the
    Desktop and the file the stand-in ``curl`` records its arguments in.
    """
    real_sh = shutil.which("sh")
    assert real_sh is not None, "a POSIX machine has an sh"
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sh").symlink_to(real_sh)
    record = tmp_path / "curl-arguments.txt"
    stand_in = bin_dir / "curl"
    stand_in.write_bytes(curl.format(record=record, bin_dir=bin_dir).encode("utf-8"))
    stand_in.chmod(stat.S_IRWXU)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return desktop, record


@pytest.mark.os_posix
def test_a_download_that_fails_is_reported_as_the_installers_failure_not_as_a_missing_uvx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """``curl`` failing is the installer failing, with ``curl``'s own exit code.

    Piped straight into ``sh``, a failed download handed the shell an empty
    script, which exits 0: the run read as a successful install that merely left
    ``uvx`` off PATH, and the reader was told to open a new shell rather than
    that nothing had been downloaded.
    """
    desktop, record = _machine_with_curl(tmp_path, monkeypatch, curl=_FAILING_CURL)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    message = json.loads(capfd.readouterr().out)["error"]["message"]
    assert "the uv installer failed (exit 6)" in message
    assert "uvx is still not on PATH" not in message
    assert UV_INSTALLER_URL_POSIX in record.read_text(encoding="utf-8").splitlines()
    assert list(desktop.iterdir()) == []


@pytest.mark.os_posix
def test_a_download_that_succeeds_is_run_and_its_uvx_becomes_the_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The control: a ``curl`` that serves a script has that script run, and its ``uvx`` is used."""
    desktop, record = _machine_with_curl(tmp_path, monkeypatch, curl=_SERVING_CURL)

    install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    captured = capfd.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["ok"] is True
    assert envelope["data"]["launch"][0] == str(tmp_path / "bin" / "uvx")
    assert "installing uv (stand-in script)" in captured.err
    assert UV_INSTALLER_URL_POSIX in record.read_text(encoding="utf-8").splitlines()
    assert len(list(desktop.iterdir())) == 1
