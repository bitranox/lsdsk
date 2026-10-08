"""A configuration section given as a single value must not pass in silence.

``history = false`` (or ``thresholds = "x"``, ``display = 3``) is a section that
is not a table, so none of its keys can be read. The run used the shipped
section instead - which for ``history = false`` means the counter history is
STILL recorded, the opposite of what the file asked for - and said nothing,
while ``lsdsk config`` listed the scalar as if it had been applied. Every other
value this tool cannot use falls back AND says so; a whole section is the same
rule one level up.

The run keeps using the shipped section, because a malformed setting must never
stop somebody diagnosing a failing drive. The warning is the fix.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

CAPTURE = "tests/fixtures/hw/linux-minimal.json"


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> None:
    """The loader caches per process; drop it so each test reads its own file."""
    get_config.cache_clear()


def _write_user_config(user_config_dir_under: Callable[[str], Path], text: str) -> None:
    """Seed the user-level ``config.toml`` this platform's loader reads."""
    config = user_config_dir_under("cfg") / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(text, encoding="utf-8")


def _said(stderr: str) -> str:
    """Standard error on one line, so a wrapped sentence can be searched."""
    return " ".join(stderr.split())


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("line", "section", "shown"),
    [
        ("history = false", "history", "false"),
        ('thresholds = "x"', "thresholds", "x"),
        ("display = 3", "display", "3"),
    ],
)
def test_a_scalar_where_a_section_belongs_is_named_and_the_shipped_section_is_used(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    user_config_dir_under: Callable[[str], Path],
    tmp_path: Path,
    line: str,
    section: str,
    shown: str,
) -> None:
    """One warning names the section and what it fell back to; the run is otherwise the control's."""
    history = tmp_path / "history.json"
    control = cli_runner.invoke(
        cli, ["--replay", CAPTURE, "--history-file", str(history), "disks", "--format", "json"], obj=production_factory
    )
    assert "ignoring" not in control.stderr, control.stderr
    get_config.cache_clear()

    _write_user_config(user_config_dir_under, f"{line}\n")
    result = cli_runner.invoke(
        cli, ["--replay", CAPTURE, "--history-file", str(history), "disks", "--format", "json"], obj=production_factory
    )

    said = _said(result.stderr)
    assert f"Warning: ignoring {section}={shown}: not a table. Using the shipped [{section}] section." in said, said
    assert said.count("ignoring") == 1, f"warned more than once: {said}"
    assert result.exit_code == control.exit_code, said
    assert json.loads(result.stdout)["command"] == "disks"


@pytest.mark.os_agnostic
def test_a_scalar_section_from_the_environment_is_named_too(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The environment layer reaches the same place a file does."""
    monkeypatch.setenv("LSDSK___THRESHOLDS", "x")
    result = cli_runner.invoke(
        cli,
        ["--replay", CAPTURE, "--history-file", str(tmp_path / "h.json"), "disks", "--format", "json"],
        obj=production_factory,
    )
    said = _said(result.stderr)
    assert "Warning: ignoring thresholds=x: not a table." in said, said


@pytest.mark.os_agnostic
def test_a_real_table_and_an_absent_section_are_not_warned_about(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    user_config_dir_under: Callable[[str], Path],
    tmp_path: Path,
) -> None:
    """The control for the arms above: only a non-table is refused, not every section."""
    _write_user_config(user_config_dir_under, "[history]\nenabled = true\n")
    result = cli_runner.invoke(
        cli,
        ["--replay", CAPTURE, "--history-file", str(tmp_path / "h.json"), "disks", "--format", "json"],
        obj=production_factory,
    )
    assert "not a table" not in result.stderr, result.stderr
