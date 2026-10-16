"""``config-deploy`` says which configured permission value it does not use, and never contradicts its refusal.

``config`` shows every mode the merged configuration holds as the value in force. The deploy
reads the section from its own layers, by rules of its own, so three kinds of value were inert
and lsdsk said nothing about two of them and the wrong thing about the third:

* a mode a ``.env`` file gives: the deploy reads the configuration files and the environment,
  never a ``.env`` file, so the mode ``config`` reported decided nothing;
* an ``app_*`` or ``host_*`` mode in a USER configuration file: the deploy drops those, because
  that file belongs to whoever owns the home directory, and only logged it;
* the section given as one value in a file: lsdsk warned "Using the shipped section" and the
  deploy then refused the run with 22 over that same value.

Each inert value gets one warning naming the key and where it came from. The section warning
is held back until the deploy has run, so a refused deploy answers with its refusal alone.
"""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

SECTION = "lib_layered_config.default_permissions"
SHIPPED_SECTION_SENTENCE = f"Warning: ignoring {SECTION}=5: not a table. Using the shipped [{SECTION}] section."


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    get_config.cache_clear()
    try:
        code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    return code, capsys.readouterr().err


def _seed_user_file(user_config_dir: Path, body: str) -> Path:
    user_config_dir.mkdir(parents=True, exist_ok=True)
    path = user_config_dir / "config.toml"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def _lines_naming(key: str, err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith(f"Warning: ignoring {SECTION}.{key}=")]


def test_a_mode_a_dotenv_file_gives_is_named_as_unused(
    user_config_dir: Path,
    tmp_path: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dotenv = tmp_path / "modes.env"
    dotenv.write_text("LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE=0o604\n", encoding="utf-8")
    user_config_dir.mkdir(parents=True, exist_ok=True)

    code, err = _run(["--env-file", str(dotenv), "config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    named = _lines_naming("user_file", err)
    assert len(named) == 1, err
    assert str(dotenv) in named[0]
    assert ".env" in named[0]


@pytest.mark.os_posix
def test_the_deploy_really_does_leave_a_dotenv_mode_unused(
    user_config_dir: Path,
    tmp_path: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The premise of the warning, on the platforms that apply a mode at all."""
    dotenv = tmp_path / "modes.env"
    dotenv.write_text("LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE=0o604\n", encoding="utf-8")
    user_config_dir.mkdir(parents=True, exist_ok=True)

    code, err = _run(["--env-file", str(dotenv), "config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert stat.S_IMODE((user_config_dir / "config.toml").stat().st_mode) == 0o600


def test_a_mode_the_environment_gives_is_not_named(
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: the deploy reads the environment, so the same mode there is in use."""
    monkeypatch.setenv("LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE", "0o640")
    user_config_dir.mkdir(parents=True, exist_ok=True)

    code, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert _lines_naming("user_file", err) == [], err


def test_an_app_mode_in_a_user_file_is_named_as_unused(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_file = _seed_user_file(user_config_dir, f'[{SECTION}]\napp_file = "0o640"\nuser_file = "0o600"\n')

    code, err = _run(["config-deploy", "--target", "app"], capsys)

    assert code == 0, err
    named = _lines_naming("app_file", err)
    assert len(named) == 1, err
    assert str(user_file) in named[0]
    # A user file may set its own layer's modes, so that one is not named.
    assert _lines_naming("user_file", err) == [], err


def test_an_app_mode_from_the_environment_is_not_named(
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: the environment is one of the layers the deploy takes an app mode from."""
    monkeypatch.setenv("LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__APP_FILE", "0o640")
    user_config_dir.mkdir(parents=True, exist_ok=True)

    code, err = _run(["config-deploy", "--target", "app"], capsys)

    assert code == 0, err
    assert _lines_naming("app_file", err) == [], err


@pytest.mark.os_posix
def test_a_refused_deploy_does_not_first_claim_the_shipped_section_is_used(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """POSIX only: Windows applies no mode, so its deploy never reads the section to refuse it."""
    _seed_user_file(user_config_dir, "[lib_layered_config]\ndefault_permissions = 5\n")

    code, err = _run(["config-deploy", "--target", "app"], capsys)

    assert code == ExitCode.INVALID_ARGUMENT, err
    assert f"{SECTION}: must be a table" in err
    assert SHIPPED_SECTION_SENTENCE not in err


def test_a_deploy_that_does_not_read_the_section_still_says_it_was_ignored(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: a user deploy skips the file it writes, so it deploys and the warning is true."""
    _seed_user_file(user_config_dir, "[lib_layered_config]\ndefault_permissions = 5\n")

    code, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert err.splitlines().count(SHIPPED_SECTION_SENTENCE) == 1, err
