"""Ctrl+C while lsdsk is still importing itself leaves 130 and no traceback.

Starting lsdsk imports a quarter of a second of modules before any handler of
this project exists, and a person who presses Ctrl+C in that window - having
typed the wrong command, say - was shown a 37 to 64 line traceback through the
import machinery. The exit status was right; the screen said the tool broke.

The window is made deterministic rather than raced: a ``sitecustomize`` on the
child's ``PYTHONPATH`` installs an import hook that, when one chosen module is
about to be imported, writes a marker file and sleeps. The test waits for the
marker, sends SIGINT, and reads what the child left. Each sleep point is a
different stage of the start: the package's own import, and the command tree
the CLI imports on first use. Both ways in are driven: the console script's
own ``from lsdsk.entry import main``, and ``python -m lsdsk``, which is also how
the elevated copy is started.

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
CONSOLE_SCRIPT = "import sys\nfrom lsdsk.entry import main\nsys.exit(main())\n"


def _interrupted_while_importing(tmp_path: Path, module: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Start lsdsk, wait until it is importing ``module``, press Ctrl+C, and collect what it left."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_bytes(SLEEPER.encode())
    marker = tmp_path / "sleeping"
    inherited = os.environ.get("PYTHONPATH")
    env = {
        **os.environ,
        "PYTHONPATH": str(hooks) if not inherited else os.pathsep.join([str(hooks), inherited]),
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
        pytest.param("lsdsk.composition", id="while the package imports"),
        pytest.param("lsdsk.adapters.cli.root", id="while the command tree imports"),
    ],
)
@pytest.mark.parametrize(
    "argv",
    [pytest.param(["-c", CONSOLE_SCRIPT], id="console script"), pytest.param(["-m", "lsdsk"], id="python -m")],
)
def test_ctrl_c_during_startup_leaves_130_and_no_traceback(tmp_path: Path, module: str, argv: list[str]) -> None:
    left = _interrupted_while_importing(tmp_path, module, argv)

    assert "Traceback" not in left.stderr, left.stderr
    assert "KeyboardInterrupt" not in left.stderr, left.stderr
    assert left.returncode == 130, (left.returncode, left.stderr)
