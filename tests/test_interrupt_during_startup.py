"""Ctrl+C while lsdsk is still importing itself leaves 130 and no traceback.

Starting lsdsk imports a quarter of a second of modules before any handler of
this project exists, and a person who presses Ctrl+C in that window - having
typed the wrong command, say - was shown a 37 to 64 line traceback through the
import machinery. The exit status was right; the screen said the tool broke.

The window is made deterministic rather than raced: the child's ``-c`` program
first installs an import hook that, when one chosen module is about to be
imported, writes a marker file and sleeps, then starts lsdsk. The hook is not a
``sitecustomize`` on ``PYTHONPATH``: that shadows the interpreter's own one, and
Homebrew's Python adds its ``site-packages`` - where a macOS CI cell installs
lsdsk - from exactly that file, so the child could no longer import lsdsk. The test waits for the
marker, sends SIGINT, and reads what the child left. Each sleep point is a
different stage of the start: the package's own import, and the command tree
the CLI imports on first use. Both ways in are driven: the console script's
own ``from lsdsk.entry import main``, and ``python -m lsdsk`` (run here through
``runpy.run_module``, which is what ``-m`` does), which is also how the elevated
copy is started.

POSIX only: Windows cannot deliver SIGINT to one child process (``os.kill``
with it terminates the process outright, and a console Ctrl+C event reaches
every process on the console, the test runner included).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

SLEEPER = """\
import importlib.abc
import os
import sys
import time

_TARGET = os.environ.get("LSDSK_TEST_SLEEP_ON_IMPORT")
_MARKER = os.environ.get("LSDSK_TEST_SLEEPING_MARKER")


class _SleepOnImport(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == _TARGET:
            with open(_MARKER, "w", encoding="utf-8") as marker:
                marker.write("sleeping")
            time.sleep(60)
        return None


if _TARGET and _MARKER:
    sys.meta_path.insert(0, _SleepOnImport())
"""

#: The console script's own wrapper does exactly this: import ``main`` from the
#: entry module, then exit with what it returns.
CONSOLE_SCRIPT = SLEEPER + "from lsdsk.entry import main\nsys.exit(main())\n"

#: What ``python -m lsdsk`` does, after the hook is in place.
DASH_M = SLEEPER + "import runpy\nrunpy.run_module('lsdsk', run_name='__main__', alter_sys=True)\n"


def _interrupted_while_importing(tmp_path: Path, module: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Start lsdsk, wait until it is importing ``module``, press Ctrl+C, and collect what it left."""
    marker = tmp_path / "sleeping"
    env = {
        **os.environ,
        "LSDSK_TEST_SLEEP_ON_IMPORT": module,
        "LSDSK_TEST_SLEEPING_MARKER": str(marker),
    }
    child = subprocess.Popen(  # noqa: S603 - argv is built here, no shell
        [sys.executable, *argv, "info"],
        env=env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + 60
    while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not marker.exists():
        child.kill()
        out, err = child.communicate()
        pytest.fail(f"lsdsk never imported {module}: rc {child.returncode}\n{out}\n{err}")
    child.send_signal(signal.SIGINT)
    out, err = child.communicate(timeout=60)
    return subprocess.CompletedProcess(child.args, child.returncode, out, err)


@pytest.mark.os_posix
@pytest.mark.parametrize(
    "module",
    [
        pytest.param("lsdsk.__init__conf__", id="while the package metadata imports"),
        pytest.param("lsdsk.composition", id="while the package imports"),
        pytest.param("lsdsk.adapters.cli.root", id="while the command tree imports"),
    ],
)
@pytest.mark.parametrize(
    "argv",
    [pytest.param(["-c", CONSOLE_SCRIPT], id="console script"), pytest.param(["-c", DASH_M], id="python -m")],
)
def test_ctrl_c_during_startup_leaves_130_and_no_traceback(tmp_path: Path, module: str, argv: list[str]) -> None:
    left = _interrupted_while_importing(tmp_path, module, argv)

    assert "Traceback" not in left.stderr, left.stderr
    assert "KeyboardInterrupt" not in left.stderr, left.stderr
    assert left.returncode == 130, (left.returncode, left.stderr)


#: Prints the modules importing the entry point loads, the way each way in imports it.
_LOADED_BY = """\
import sys

before = set(sys.modules)
{statement}
print(sorted(set(sys.modules) - before))
"""


@pytest.mark.parametrize(
    ("statement", "loaded"),
    [
        pytest.param("from lsdsk.entry import main", ["lsdsk", "lsdsk.entry"], id="console script"),
        pytest.param("import lsdsk.__main__", ["lsdsk", "lsdsk.__main__", "lsdsk.entry"], id="python -m"),
    ],
)
def test_reaching_the_entry_point_imports_nothing_but_the_entry_point(statement: str, loaded: list[str]) -> None:
    """Everything up to the entry point's own guard is outside every handler, so it imports nothing more.

    A Ctrl+C in that stretch is answered by the interpreter with a traceback;
    the stretch is kept to the import system finding and running these few
    modules, which no handler of this project can cover (nor can the
    interpreter's own start before them). Every other import happens inside
    the guard, where the test above drives it.
    """
    # The program goes in on stdin, so the argv is fixed and nothing in it is built.
    child = subprocess.run(
        [sys.executable, "-"],
        input=_LOADED_BY.format(statement=statement),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == repr(loaded), child.stdout
