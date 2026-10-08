"""A damaged installation refuses as a configuration error, never as the caller's mistake.

The package ships its own ``defaultconfig.toml``. When that file was missing, every
command left 2 - the code for a wrong command line - with a bare
``FileNotFoundError``; when it was unreadable, 13. A monitoring check read the
tool's own broken files as a typo in its own invocation. A CORRUPT shipped file was
already refused as 78 by the configuration library, so the three ways one file can
be damaged gave three different answers.

The runs are REAL processes over a COPY of the package, because the damage is to
files the installed package reads by its own location, and the only honest way to
damage them without touching the checkout is to run a copy that carries the damage.
The intact copy is the liveness control: it proves the copy is the package the child
imports and that the harness itself exits 0, so a refusal in the damaged arms is the
damage speaking and not the copy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

import lsdsk
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import SHIPPED_COMPANION_FILES

_PACKAGE = Path(lsdsk.__file__).resolve().parent
_SHIPPED = Path("adapters") / "config" / "defaultconfig.toml"


class _Run(NamedTuple):
    """What one spawned run left behind."""

    code: int
    stdout: str
    stderr: str


def _copy_of_the_package(tmp_path: Path) -> Path:
    """A private copy of the installed package, ready to be damaged.

    Args:
        tmp_path: The test's own directory.

    Returns:
        The directory to put on the child's ``PYTHONPATH``.
    """
    root = tmp_path / "site"
    shutil.copytree(_PACKAGE, root / "lsdsk", ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _launch(root: Path, argv: list[str]) -> _Run:
    """Run the CLI from the copy at ``root`` in a child process.

    Args:
        root: The directory holding the copied ``lsdsk`` package.
        argv: The arguments after the program name.

    Returns:
        The exit code and what reached stdout and stderr.
    """
    completed = subprocess.run(  # noqa: S603 - the interpreter running this suite, with fixed arguments
        [sys.executable, "-m", "lsdsk", "--no-record", *argv],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(root)},
        check=False,
        timeout=120,
    )
    return _Run(completed.returncode, completed.stdout, completed.stderr)


@pytest.mark.os_agnostic
def test_an_intact_copy_of_the_package_runs_as_the_installed_one_does(tmp_path: Path) -> None:
    root = _copy_of_the_package(tmp_path)
    run = _launch(root, ["config", "--format", "json"])
    assert run.code == 0, run.stderr
    assert json.loads(run.stdout)["ok"] is True


@pytest.mark.os_agnostic
def test_a_missing_shipped_configuration_refuses_as_a_configuration_error(tmp_path: Path) -> None:
    root = _copy_of_the_package(tmp_path)
    (root / "lsdsk" / _SHIPPED).unlink()
    run = _launch(root, ["config", "--format", "json"])
    assert run.code == ExitCode.CONFIG_ERROR, run.stderr
    assert "shipped configuration" in run.stderr
    assert "FileNotFoundError" not in run.stderr
    envelope = json.loads(run.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CONFIG_ERROR"


@pytest.mark.os_agnostic
def test_a_corrupt_shipped_configuration_is_named_as_damage_to_the_installation(tmp_path: Path) -> None:
    """A corrupt file already left 78; it now says whose file it is, as the other two kinds do."""
    root = _copy_of_the_package(tmp_path)
    companion = sorted((root / "lsdsk" / _SHIPPED.parent / "defaultconfig.d").glob("*.toml"))[0]
    with companion.open("a", encoding="utf-8") as handle:
        handle.write("\nbroken = = \n")
    run = _launch(root, ["info"])
    assert run.code == ExitCode.CONFIG_ERROR, run.stderr
    assert "shipped configuration" in run.stderr


@pytest.mark.os_posix
def test_an_unreadable_shipped_configuration_refuses_as_a_configuration_error(tmp_path: Path) -> None:
    assert sys.platform != "win32"  # os_posix; narrows os.geteuid for the type checker
    if os.geteuid() == 0:
        pytest.skip("root reads a file whatever its mode says")
    root = _copy_of_the_package(tmp_path)
    (root / "lsdsk" / _SHIPPED).chmod(0)
    run = _launch(root, ["info"])
    assert run.code == ExitCode.CONFIG_ERROR, run.stderr
    assert "shipped configuration" in run.stderr
    assert "PermissionError" not in run.stderr


@pytest.mark.os_agnostic
def test_a_missing_companion_file_refuses_as_damage_to_the_installation(tmp_path: Path) -> None:
    """A deleted file under ``defaultconfig.d`` is damage, not a smaller configuration.

    The companion directory was read by globbing what exists, so a missing
    ``60-thresholds.toml`` merely removed its keys from the shipped defaults and
    the run went on with whatever the code fell back to, exit 0 and no sentence.
    """
    root = _copy_of_the_package(tmp_path)
    (root / "lsdsk" / _SHIPPED.parent / "defaultconfig.d" / "60-thresholds.toml").unlink()
    run = _launch(root, ["config", "--format", "json"])
    assert run.code == ExitCode.CONFIG_ERROR, run.stderr
    assert "shipped configuration" in run.stderr
    assert "60-thresholds.toml" in run.stderr, "the refusal does not name the file that is gone"
    assert json.loads(run.stdout)["error"]["type"] == "CONFIG_ERROR"


@pytest.mark.os_agnostic
def test_the_expected_companion_files_are_exactly_the_ones_the_package_ships() -> None:
    """The list the loader checks against must follow the directory, in both directions.

    A file added to ``defaultconfig.d`` and not to the list would never be missed
    when it went; a name left in the list after its file was removed would refuse
    every installation.
    """
    shipped = sorted(path.name for path in (_PACKAGE / _SHIPPED.parent / "defaultconfig.d").glob("*.toml"))
    assert list(SHIPPED_COMPANION_FILES) == shipped
