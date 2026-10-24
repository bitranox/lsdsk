"""The elevated copy reads no ``.env`` from the directory it was started in, unless one is named.

The interactive view relaunches itself as root or Administrator in the
directory the person typed in, so relative paths keep meaning the same. A
plain run searches that directory and its parents for a ``.env``, and the
copy did too: whoever could leave a ``.env`` in that directory - a shared
``/tmp`` subdirectory, a checkout someone else writes to - chose root's
history store path, its log levels and a Graylog destination. The copy now
reads no ``.env`` at all unless ``--env-file`` names one, which the person typed
and the relaunch passes on unchanged.

Driven through the real CLI with the production services, from a working
directory holding a planted ``.env``; nothing is elevated. The plain run is
the control that proves the planted file is found when it is supposed to be.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

PLANTED = "planted-history.json"


def _planted_dotenv(directory: Path) -> Path:
    """A ``.env`` that moves the history store and the console log level."""
    dotenv = directory / ".env"
    dotenv.write_bytes(f"HISTORY__PATH={directory / PLANTED}\nLOG_CONSOLE_LEVEL=DEBUG\n".encode())
    return dotenv


def _history_path_after(cli_runner: CliRunner, factory: Callable[[], AppServices], options: list[str]) -> object:
    """The history path the run's configuration settled on."""
    result = cli_runner.invoke(cli, [*options, "config", "--format", "json"], obj=factory)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]["history"]["path"]


def _no_log_level_set_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start with ``LOG_CONSOLE_LEVEL`` unset and have it restored afterwards, whoever sets it.

    Set first and then deleted: deleting a variable that is absent records
    nothing to undo, so a value the run copies in from a ``.env`` would leak
    into every later test.
    """
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "placeholder")
    monkeypatch.delenv("LOG_CONSOLE_LEVEL")


@pytest.mark.usefixtures("clear_config_cache")
@pytest.mark.parametrize(
    ("options", "reads_the_planted_file"),
    [
        pytest.param([], True, id="a plain run searches the working directory"),
        pytest.param(["--elevated-relaunch"], False, id="the elevated copy does not"),
    ],
)
def test_the_elevated_copy_takes_no_setting_from_a_dotenv_in_its_working_directory(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: list[str],
    reads_the_planted_file: bool,
) -> None:
    _planted_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    _no_log_level_set_yet(monkeypatch)

    path = _history_path_after(cli_runner, production_factory, options)

    assert (path == str(tmp_path / PLANTED)) is reads_the_planted_file, path
    assert ("LOG_CONSOLE_LEVEL" in os.environ) is reads_the_planted_file, "the logging half read the .env"


@pytest.mark.usefixtures("clear_config_cache")
def test_the_elevated_copy_still_reads_a_dotenv_named_on_its_command_line(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--env-file`` is the person's own choice, typed on the command line the copy was handed."""
    named = tmp_path / "named"
    named.mkdir()
    dotenv = _planted_dotenv(named)
    monkeypatch.chdir(tmp_path)
    _no_log_level_set_yet(monkeypatch)

    path = _history_path_after(cli_runner, production_factory, ["--elevated-relaunch", "--env-file", str(dotenv)])

    assert path == str(named / PLANTED)
    assert os.environ.get("LOG_CONSOLE_LEVEL") == "DEBUG"
