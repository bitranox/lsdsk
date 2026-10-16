"""``config --profile X`` warns about profile X's files exactly as ``--profile X config`` does, once each.

The root group warns about a config-file key nothing reads and a value nothing can use, against
the configuration IT loaded. In the subcommand form that is the configuration without the
profile, and the reload of profile X said nothing: a typo in X's own file and a value X's file
gave that fell back were reported by one spelling of the request and not by the other. The root
group now loads the profile the subcommand names, once, so each warning is said once whichever
place named the profile - and an environment, ``.env`` or ``--set`` warning is not said twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

PROFILE = "x"

#: What each source contributes, and the warning line it must produce exactly once.
FILE_TYPO = "Warning: ignoring thresholds.wear_warnning_percent: [thresholds] has no such key."
FILE_REFUSED = "Warning: ignoring display.wwn_width=abc:"
ENV_REFUSED = "Warning: ignoring display.summary_limit=nope:"
SET_REFUSED = "Warning: ignoring display.piped_width=wide:"

_SPELLINGS = (
    pytest.param(["--profile", PROFILE, "config"], id="global-config"),
    pytest.param(["config", "--profile", PROFILE], id="config"),
    pytest.param(["--profile", PROFILE, "config-deploy", "--target", "user"], id="global-deploy"),
    pytest.param(["config-deploy", "--target", "user", "--profile", PROFILE], id="config-deploy"),
    # Both at once: one load, so nothing is reported twice.
    pytest.param(["--profile", PROFILE, "config", "--profile", PROFILE], id="both-config"),
)


def _seed_profile(user_config_dir: Path) -> None:
    directory = user_config_dir / "profile" / PROFILE
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(
        '[thresholds]\nwear_warnning_percent = 5\n\n[display]\nwwn_width = "abc"\n',
        encoding="utf-8",
        newline="\n",
    )


def _stderr_of(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> str:
    get_config.cache_clear()
    try:
        cli_mod.main(["--set", "display.piped_width=wide", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    return capsys.readouterr().err


def _times(line: str, err: str) -> int:
    return sum(1 for written in err.splitlines() if written.startswith(line))


@pytest.mark.parametrize("argv", _SPELLINGS)
def test_a_typo_in_the_profile_s_own_file_is_reported_once(
    argv: list[str],
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_profile(user_config_dir)
    monkeypatch.setenv("LSDSK___DISPLAY__SUMMARY_LIMIT", "nope")

    err = _stderr_of(argv, capsys)

    assert _times(FILE_TYPO, err) == 1, err


@pytest.mark.parametrize("argv", _SPELLINGS)
def test_a_value_the_profile_s_own_file_gave_that_fell_back_is_reported_once(
    argv: list[str],
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_profile(user_config_dir)
    monkeypatch.setenv("LSDSK___DISPLAY__SUMMARY_LIMIT", "nope")

    err = _stderr_of(argv, capsys)

    assert _times(FILE_REFUSED, err) == 1, err


@pytest.mark.parametrize("argv", _SPELLINGS)
def test_an_environment_or_set_value_is_still_reported_only_once(
    argv: list[str],
    user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The root reported these already, and they are in both configurations alike."""
    _seed_profile(user_config_dir)
    monkeypatch.setenv("LSDSK___DISPLAY__SUMMARY_LIMIT", "nope")

    err = _stderr_of(argv, capsys)

    assert (_times(ENV_REFUSED, err), _times(SET_REFUSED, err)) == (1, 1), err


DOTENV_REFUSED = "Warning: ignoring display.summary_limit=never:"


@pytest.mark.parametrize("argv", _SPELLINGS)
def test_a_value_a_dotenv_file_gave_that_fell_back_is_reported_only_once(
    argv: list[str],
    tmp_path: Path,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ``.env`` file is a file too, and it is not one of profile X's.

    It is read with and without the profile alike, so whichever load reported it, a second
    report of the same line would be the same warning said twice.
    """
    _seed_profile(user_config_dir)
    dotenv = tmp_path / "chosen.env"
    dotenv.write_text("DISPLAY__SUMMARY_LIMIT=never\n", encoding="utf-8", newline="\n")

    err = _stderr_of(["--env-file", str(dotenv), *argv], capsys)

    assert _times(DOTENV_REFUSED, err) == 1, err
