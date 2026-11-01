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

#: A stand-in ``curl`` that serves an installer which itself fails, as the real
#: script does on a platform it does not support, leaving no ``uvx`` behind.
_SERVING_A_FAILING_SCRIPT_CURL = """#!/bin/sh
PATH=/usr/bin:/bin
printf '%s\\n' "$@" > "{record}"
cat <<'SCRIPT'
echo "error: unsupported platform (stand-in script)" >&2
exit 3
SCRIPT
"""


def _machine_with_curl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, curl: str | None) -> tuple[Path, Path]:
    """A home with a Desktop, and a PATH holding the real ``sh`` beside a stand-in ``curl``.

    Unlike :func:`_machine`, the shell is the real one, so the installer command's
    own script runs and decides what a failed download comes to. Returns the
    Desktop and the file the stand-in ``curl`` records its arguments in. With
    ``curl`` ``None`` the PATH holds no ``curl`` at all, as on a minimal system.
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
    if curl is not None:
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


@pytest.mark.os_posix
def test_an_installer_script_that_fails_is_reported_with_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The downloaded script's own failure is the installer's exit code, not a quiet missing ``uvx``.

    The download worked here, so ``curl`` says nothing; only the shell the script
    ran in knows it failed. Ending the fetch-then-run line in anything but that
    shell's status reads the failure as a successful install that left ``uvx``
    off PATH.
    """
    desktop, _record = _machine_with_curl(tmp_path, monkeypatch, curl=_SERVING_A_FAILING_SCRIPT_CURL)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    message = json.loads(capfd.readouterr().out)["error"]["message"]
    assert "the uv installer failed (exit 3)" in message
    assert "uvx is still not on PATH" not in message
    assert list(desktop.iterdir()) == []


@pytest.mark.os_posix
def test_a_missing_curl_is_reported_under_the_installers_name_not_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The shell names a command it cannot find after ``$0``, so ``$0`` must name the step, not the download.

    With the URL as ``$0`` the shell's line read ``https://astral.sh/uv/.../install.sh: 1: curl: not
    found``: it put the address in the place of the program that failed, which reads as the URL being
    what was not found.
    """
    desktop, _record = _machine_with_curl(tmp_path, monkeypatch, curl=None)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    captured = capfd.readouterr()
    assert "the uv installer failed (exit 127)" in json.loads(captured.out)["error"]["message"]
    # The shell's own line, "<$0>: 1: curl: not found" in dash and "<$0>: line 1: curl: command not found"
    # in bash, in the shell's language; the error's manual install line also names curl, but not so.
    shell_lines = [line for line in captured.err.splitlines() if ": curl:" in line]
    assert shell_lines, f"the shell should have said curl is missing: {captured.err!r}"
    assert all(UV_INSTALLER_URL_POSIX not in line for line in shell_lines), shell_lines
    assert all(line.startswith("lsdsk-uv-installer:") for line in shell_lines), shell_lines
    assert list(desktop.iterdir()) == []


#: A stand-in ``curl`` answering as the real one does for an address the server
#: has no file at: with ``-f`` it prints nothing and exits 22, and without it it
#: prints the server's error page and exits 0, as for any other download.
_NOT_FOUND_CURL = """#!/bin/sh
printf '%s\\n' "$@" > "{record}"
for argument in "$@"; do
    case "$argument" in
        --fail|-[!-]*f*)
            echo "curl: (22) The requested URL returned error: 404" >&2
            exit 22
            ;;
    esac
done
echo "404: Not Found"
exit 0
"""


@pytest.mark.os_posix
def test_a_download_the_server_refuses_is_reported_as_curls_failure_not_run_as_a_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """An HTTP error is ``curl``'s exit 22, and the error page it would have printed is never run.

    Without ``-f`` a 404 is a successful download of the server's error page,
    which the shell then runs as the installer: the reader would be told of an
    installer failing on ``404:: not found`` rather than of a download that
    failed.
    """
    desktop, record = _machine_with_curl(tmp_path, monkeypatch, curl=_NOT_FOUND_CURL)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, install_uv=True)

    assert leaving.value.code == ExitCode.CONFIG_ERROR
    captured = capfd.readouterr()
    assert "the uv installer failed (exit 22)" in json.loads(captured.out)["error"]["message"]
    # The shell names itself by $0 in front of every error it reports, so a run error page leaves such a line.
    assert [line for line in captured.err.splitlines() if line.startswith("lsdsk-uv-installer:")] == []
    assert UV_INSTALLER_URL_POSIX in record.read_text(encoding="utf-8").splitlines()
    assert list(desktop.iterdir()) == []
