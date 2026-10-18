"""Logging takes only the ``LOG_*`` lines of a ``.env``, and only from the ``.env`` the run reads.

lib_log_rich's ``enable_dotenv()`` copied EVERY line of the nearest ``.env`` into the process
environment, so a later configuration load read an app-prefixed line as the ENVIRONMENT layer:
``LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE=0o604`` in a working directory's
``.env`` was invisible to ``lsdsk config`` (the dotenv layer files it under an unowned table) and
yet ``config-deploy`` wrote 0o604. It also searched from the working directory whatever
``--env-file`` named, so a ``LOG_*`` line in that file never reached logging.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.logging import setup
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

PREFIXED_MODE = "LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE"
UNRELATED = "LSDSK_TEST_UNRELATED_DOTENV_LINE"
LEVEL = "LOG_CONSOLE_LEVEL"


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    get_config.cache_clear()
    try:
        code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    return code, capsys.readouterr().err


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Each variable a ``.env`` here names starts unset, and is removed again afterwards.

    ``delenv`` of a variable that is not set records nothing to undo, so a value the code under
    test sets would outlive the test and reach every later one (a ``LOG_CONSOLE_LEVEL`` left at
    WARNING hides their INFO lines). Setting it first makes monkeypatch record the original state.
    """
    for name in (PREFIXED_MODE, UNRELATED, LEVEL):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return monkeypatch


@pytest.mark.os_posix
def test_a_prefixed_line_in_the_working_directory_s_dotenv_does_not_decide_a_deployed_mode(
    user_config_dir: Path,
    tmp_path: Path,
    clean_environment: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(f"{PREFIXED_MODE}=0o604\n{UNRELATED}=1\n", encoding="utf-8")
    clean_environment.chdir(project)
    user_config_dir.mkdir(parents=True, exist_ok=True)

    code, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert stat.S_IMODE((user_config_dir / "config.toml").stat().st_mode) == 0o600, err
    assert PREFIXED_MODE not in os.environ
    assert UNRELATED not in os.environ


def test_only_log_lines_of_the_dotenv_reach_the_environment(
    tmp_path: Path, clean_environment: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"{LEVEL}=WARNING\n{PREFIXED_MODE}=0o604\n{UNRELATED}=1\n", encoding="utf-8")
    clean_environment.chdir(tmp_path)

    setup.load_log_variables(None)

    assert os.environ.get(LEVEL) == "WARNING"
    assert PREFIXED_MODE not in os.environ
    assert UNRELATED not in os.environ


def test_a_log_variable_already_set_is_not_replaced(tmp_path: Path, clean_environment: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(f"{LEVEL}=WARNING\n", encoding="utf-8")
    clean_environment.chdir(tmp_path)
    clean_environment.setenv(LEVEL, "ERROR")

    setup.load_log_variables(None)

    assert os.environ[LEVEL] == "ERROR"


def test_the_named_env_file_is_read_instead_of_the_working_directory_s(
    tmp_path: Path, clean_environment: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"{LEVEL}=WARNING\n", encoding="utf-8")
    named = tmp_path / "elsewhere" / "named.env"
    named.parent.mkdir()
    named.write_text(f"{LEVEL}=ERROR\n", encoding="utf-8")
    clean_environment.chdir(tmp_path)

    setup.load_log_variables(str(named))

    assert os.environ[LEVEL] == "ERROR"


def test_a_log_line_in_the_named_env_file_reaches_logging(
    tmp_path: Path,
    clean_environment: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end: a level logging refuses is only reported if logging read the line at all."""
    named = tmp_path / "named.env"
    named.write_text(f"{LEVEL}=nope\n", encoding="utf-8")
    (tmp_path / "cwd").mkdir()
    clean_environment.chdir(tmp_path / "cwd")

    code, err = _run(["--env-file", str(named), "config"], capsys)

    assert code == 0, err
    assert f"ignoring environment variable {LEVEL}=nope" in err, err


def test_the_upward_search_stops_at_the_project_root(tmp_path: Path, clean_environment: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(f"{LEVEL}=WARNING\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "pyproject.toml").write_text("", encoding="utf-8")
    clean_environment.chdir(project / "src")

    setup.load_log_variables(None)

    assert LEVEL not in os.environ


def test_the_nearest_dotenv_above_the_working_directory_is_found(
    tmp_path: Path, clean_environment: pytest.MonkeyPatch
) -> None:
    """The control for the project-root stop: below a root, the search does climb."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "pyproject.toml").write_text("", encoding="utf-8")
    (project / ".env").write_text(f"{LEVEL}=WARNING\n", encoding="utf-8")
    clean_environment.chdir(project / "src")

    setup.load_log_variables(None)

    assert os.environ.get(LEVEL) == "WARNING"


def test_a_dotenv_that_is_not_utf_8_is_passed_over(tmp_path: Path, clean_environment: pytest.MonkeyPatch) -> None:
    """The configuration loader reports such a file; logging must not fail first over it."""
    (tmp_path / ".env").write_bytes(f"{LEVEL}=WARN\xe4\n".encode("latin-1"))
    clean_environment.chdir(tmp_path)

    setup.load_log_variables(None)

    assert LEVEL not in os.environ
