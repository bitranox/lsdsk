"""``config --profile X`` and ``config-deploy --profile X`` load profile X once, as ``--profile X`` does.

The root group used to load the configuration WITHOUT the profile first, judge it, start logging
from it, and only then let the subcommand reload X. So the subcommand spelling answered for a
configuration it was not asked about: a broken file outside the profile stopped it with 78 while
``--profile X config`` ran, warnings about files the run never used were printed, a refused
logging value in X was never reported because logging had started from the other load, and a
profile named in both places was warned about twice. The root group now reads the subcommand's
own ``--profile`` before it loads anything, so there is one load, of the profile that was asked
for, whichever place named it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

PROFILE = "x"

#: A file the TOML reader refuses.
BROKEN = "[display\n"

#: The same request spelt with the global option and with each subcommand's own.
_BOTH_SPELLINGS = (
    pytest.param(["--profile", PROFILE, "config"], id="global-config"),
    pytest.param(["config", "--profile", PROFILE], id="config"),
    pytest.param(["--profile", PROFILE, "config-deploy", "--target", "user"], id="global-deploy"),
    pytest.param(["config-deploy", "--target", "user", "--profile", PROFILE], id="config-deploy"),
)

_SUBCOMMAND_SPELLINGS = (
    pytest.param(["config", "--profile", PROFILE], id="config"),
    pytest.param(["config-deploy", "--target", "user", "--profile", PROFILE], id="config-deploy"),
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def _profile_file(user_config_dir: Path, profile: str = PROFILE) -> Path:
    return user_config_dir / "profile" / profile / "config.toml"


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the real entry point with the production wiring; the code, stdout and stderr."""
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _times(line: str, err: str) -> int:
    return sum(1 for written in err.splitlines() if written.startswith(line))


@pytest.mark.parametrize("argv", _BOTH_SPELLINGS)
def test_a_broken_file_outside_the_profile_does_not_stop_a_run_of_the_profile(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A profile replaces the configuration directories, so the unprofiled file is never read."""
    _write(user_config_dir / "config.toml", BROKEN)
    _write(_profile_file(user_config_dir), "[display]\nwwn_width = 9\n")

    exit_code, _out, err = _run(argv, capsys)

    assert exit_code == 0, err
    assert "not valid TOML" not in err, err


def test_the_files_outside_the_profile_are_not_reported_on(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo in a file the run does not read is not this run's to report."""
    _write(user_config_dir / "config.toml", "[thresholds]\nwear_warnning_percent = 5\n")
    _write(_profile_file(user_config_dir), "[display]\nwwn_width = 9\n")

    exit_code, _out, err = _run(["config", "--profile", PROFILE], capsys)

    assert exit_code == 0, err
    assert "wear_warnning_percent" not in err, err


def test_the_control_reports_the_same_typo_when_its_file_is_read(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control for the test above: the warning exists, and fires when that file is in use."""
    _write(user_config_dir / "config.toml", "[thresholds]\nwear_warnning_percent = 5\n")

    exit_code, _out, err = _run(["config"], capsys)

    assert exit_code == 0, err
    assert _times("Warning: ignoring thresholds.wear_warnning_percent:", err) == 1, err


def test_the_subcommand_s_profile_is_the_one_loaded_when_both_name_one(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--profile a config --profile x`` shows x, and a broken file of a stops nothing."""
    _write(_profile_file(user_config_dir, "a"), BROKEN)
    _write(_profile_file(user_config_dir), "[display]\nwwn_width = 9\n")

    exit_code, out, err = _run(
        ["--profile", "a", "config", "--profile", PROFILE, "--section", "display.wwn_width", "--format", "json"],
        capsys,
    )

    assert exit_code == 0, err
    assert json.loads(out)["data"] == {"display.wwn_width": 9}


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--profile", "nosuch", "config", "--profile", "nosuch"], id="config"),
        pytest.param(
            ["--profile", "nosuch", "config-deploy", "--target", "user", "--profile", "nosuch"], id="config-deploy"
        ),
    ],
)
def test_a_profile_named_in_both_places_that_named_nothing_is_warned_about_once(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_config_dir.mkdir(parents=True, exist_ok=True)

    exit_code, _out, err = _run(argv, capsys)

    assert exit_code == 0, err
    assert _times("Warning: profile nosuch named nothing", err) == 1, err


@pytest.mark.parametrize("argv", _BOTH_SPELLINGS)
def test_a_refused_logging_value_in_the_profile_is_reported_in_every_spelling(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Logging starts from the configuration the run uses, so X's own refused value is named."""
    _write(_profile_file(user_config_dir), '[lib_log_rich]\nconsole_level = "LOUD"\n')

    exit_code, _out, err = _run(argv, capsys)

    assert exit_code == 0, err
    assert _times("Warning: ignoring lib_log_rich.console_level=LOUD", err) == 1, err


@pytest.mark.parametrize("argv", _BOTH_SPELLINGS)
def test_two_refusals_at_once_leave_the_same_code_in_every_spelling(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed file of X and a --set section given as one value: the file is met first, as 78."""
    _write(_profile_file(user_config_dir), BROKEN)

    exit_code, _out, err = _run(["--set", "lib_layered_config.default_permissions=5", *argv], capsys)

    assert exit_code == ExitCode.CONFIG_ERROR, err


@pytest.mark.parametrize("command", [["config"], ["config-deploy", "--target", "user"]])
@pytest.mark.parametrize("help_option", ["-h", "--help"])
def test_help_after_a_subcommand_profile_still_prints_help_and_loads_nothing_of_it(
    command: list[str],
    help_option: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Asking for help is not asking to use the profile, so it is neither loaded nor warned about."""
    _write(_profile_file(user_config_dir), BROKEN)

    exit_code, out, err = _run([*command, "--profile", PROFILE, help_option], capsys)

    assert exit_code == 0, err
    assert "--profile" in out
    assert "named nothing" not in err, err
    assert "not valid TOML" not in err, err


@pytest.mark.parametrize("argv", _SUBCOMMAND_SPELLINGS)
def test_a_subcommand_profile_with_no_value_is_still_a_usage_error(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_config_dir.mkdir(parents=True, exist_ok=True)

    exit_code, _out, err = _run(argv[:-1], capsys)

    assert exit_code == 2, err
    assert "requires an argument" in err, err
