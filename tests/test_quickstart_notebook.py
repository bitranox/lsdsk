"""The Quickstart notebook installs a checkout whatever its path looks like.

The install cell once ran its two installs as IPython ``!`` shell lines, and IPython
substitutes ``{source}`` into such a line UNQUOTED: a checkout under a folder called
``with space`` reached uv as two arguments and the install failed with
``Distribution not found at: file:///.../with``. CI never saw it, because its checkout
path has no blank in it.

So the cell is plain Python handing ``subprocess`` an argument list, and these tests
run that cell's code as a script, the way a kernel executes it once IPython has nothing
of its own to translate: from a checkout under a folder with a space, with the
interpreter it calls replaced by a stand-in that records the argv it is given and
installs nothing. The stand-in is a ``/bin/sh`` script, which is why the tests need a
POSIX host; the notebook job in CI runs on ubuntu.
"""

from __future__ import annotations

import json
import runpy
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_NOTEBOOK = _ROOT / "notebooks" / "Quickstart.ipynb"
_INSTALL_CELL_ID = "github-install"

# One line per invocation, each argument ended by a NUL, so a blank inside an
# argument stays visibly inside it.
_RECORDING_STANDIN = "\n".join(
    [
        "#!/bin/sh",
        r"""printf '%s\0' "$@" >> "$ARGV_LOG" """,
        r"""printf '\n' >> "$ARGV_LOG" """,
        "",
    ]
)
_FAILING_STANDIN = "#!/bin/sh\nexit 3\n"


def _install_cell_source() -> str:
    """The code of the notebook's install cell, as a kernel receives it.

    Returns:
        The cell's source joined into one string.

    Raises:
        AssertionError: The notebook has no install cell.
    """
    notebook = json.loads(_NOTEBOOK.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell.get("id") == _INSTALL_CELL_ID:
            source = cell["source"]
            return source if isinstance(source, str) else "".join(source)
    raise AssertionError(f"no cell with id {_INSTALL_CELL_ID!r} in {_NOTEBOOK}")


def _recorded_invocations(log: Path) -> list[list[str]]:
    """Read back the argv of every stand-in run, in the order they ran.

    Args:
        log: The file the stand-in appended to.

    Returns:
        One argument list per invocation, without the program name.
    """
    lines = log.read_bytes().decode("utf-8").split("\n")
    return [line.split("\0")[:-1] for line in lines if line]


def _run_install_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, standin_body: str) -> Path:
    """Run the install cell from a checkout under a folder with a space in its name.

    Args:
        tmp_path: The test's own directory.
        monkeypatch: Points the working directory, ``sys.executable`` and the
            stand-in's log for the run.
        standin_body: The shell script the stand-in interpreter runs.

    Returns:
        The checkout the cell ran from.
    """
    checkout = tmp_path / "with space" / "lsdsk"
    checkout.mkdir(parents=True)
    shutil.copyfile(_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    standin = tmp_path / "standin-python"
    standin.write_text(standin_body, encoding="utf-8", newline="\n")
    standin.chmod(standin.stat().st_mode | stat.S_IXUSR)
    cell = tmp_path / "install_cell.py"
    cell.write_text(_install_cell_source(), encoding="utf-8", newline="\n")
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(sys, "executable", str(standin))
    monkeypatch.setenv("ARGV_LOG", str(tmp_path / "argv.log"))
    runpy.run_path(str(cell), run_name="__main__")
    return checkout


@pytest.mark.os_posix
def test_the_install_cell_hands_a_checkout_path_with_a_space_over_as_one_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _run_install_cell(tmp_path, monkeypatch, standin_body=_RECORDING_STANDIN)

    assert _recorded_invocations(tmp_path / "argv.log") == [
        ["-m", "pip", "install", "--quiet", "--upgrade", "uv"],
        ["-m", "uv", "pip", "install", "--python", sys.executable, "--upgrade", str(checkout.resolve())],
    ]


@pytest.mark.os_posix
def test_a_failed_install_fails_the_install_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(subprocess.CalledProcessError) as raised:
        _run_install_cell(tmp_path, monkeypatch, standin_body=_FAILING_STANDIN)

    assert raised.value.returncode == 3
