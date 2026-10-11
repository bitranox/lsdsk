"""A full standard output is reported as that, not as the files it came after failing.

``config-deploy`` and ``config-generate-examples`` wrote their files and then
reported the result inside the same ``try`` as the write, so a standard output
that refused the report (``> /dev/full``) was logged and answered as "Failed to
deploy configuration" although every file was in place. The run is a real child
process: the answer depends on what the interpreter does with the refused write.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).parent.parent

_LAUNCHER = """
import sys

from lsdsk.adapters.cli.main import main
from lsdsk.composition import build_production

raise SystemExit(main(sys.argv[1:], services_factory=build_production))
"""


def _run_into_a_full_disk(argv: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    with Path("/dev/full").open("wb") as full:
        return subprocess.run(  # noqa: S603 - argv is built here, no shell
            [sys.executable, "-c", _LAUNCHER, *argv],
            stdout=full,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env={**os.environ, "TERM": "dumb", **environment},
            check=False,
            timeout=180,
        )


def _deploy(tmp_path: Path) -> tuple[list[str], dict[str, str], Callable[[], bool]]:
    home = tmp_path / "xdg"
    return (
        ["config-deploy", "--target", "user"],
        {"XDG_CONFIG_HOME": str(home), "HOME": str(tmp_path)},
        lambda: any(home.rglob("*.toml")),
    )


def _generate(tmp_path: Path) -> tuple[list[str], dict[str, str], Callable[[], bool]]:
    target = tmp_path / "examples"
    return ["config-generate-examples", "--destination", str(target)], {}, lambda: any(target.rglob("*.toml"))


@pytest.mark.os_linux
@pytest.mark.parametrize("case", [_deploy, _generate], ids=["config-deploy", "config-generate-examples"])
def test_a_full_standard_output_after_the_files_landed_is_not_a_failed_write(
    case: Callable[[Path], tuple[list[str], dict[str, str], Callable[[], bool]]], tmp_path: Path
) -> None:
    """The files exist, so stderr must say standard output refused, and never that the write failed."""
    argv, environment, files_landed = case(tmp_path)

    run = _run_into_a_full_disk(argv, environment)

    assert files_landed(), f"the control failed: nothing was written; stderr: {run.stderr!r}"
    assert "Failed to" not in run.stderr, run.stderr
    assert "Traceback" not in run.stderr, run.stderr
    assert "standard output" in run.stderr, run.stderr
    assert run.returncode == ExitCode.IO_ERROR, f"exit {run.returncode}; stderr: {run.stderr!r}"
