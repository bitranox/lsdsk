"""``config --profile X`` and ``config-deploy --profile X`` load X the way ``--profile X`` does.

The root group loads its configuration through one path that answers a file it cannot read with
78 or 13 and the JSON envelope, warns when a profile named nothing, and refuses a mode that could
be octal digits. The two subcommands that repeat ``--profile`` reloaded the configuration
themselves and did none of it: a malformed file of profile X left ``LayerLoadError`` and exit 70
with no envelope, and ``config-deploy --profile X`` read ``enabled`` from the ROOT configuration
while the deploy wrote X's directories. Each test here pins the subcommand form against the
global form of the same request, so the two cannot drift apart again.
"""

from __future__ import annotations

import json
import os
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

PROFILE = "x"

#: Asked with ``hasattr`` because Windows has no ``geteuid``. Root reads a mode-000 file, so
#: there is no refusal to observe as root.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _seed_profile(user_config_dir: Path, body: str) -> Path:
    """Write ``config.toml`` of profile X where this platform's loader reads it."""
    directory = user_config_dir / "profile" / PROFILE
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "config.toml"
    config.write_text(body, encoding="utf-8", newline="\n")
    return config


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the real entry point with the production wiring; the code, stdout and stderr."""
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(list(argv), services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


#: The same request spelt with the global option and with each subcommand's own.
_BOTH_SPELLINGS = (
    pytest.param(["--profile", PROFILE, "config"], "config", id="global-config"),
    pytest.param(["config", "--profile", PROFILE], "config", id="config"),
    pytest.param(["--profile", PROFILE, "config-deploy", "--target", "user"], "config-deploy", id="global-deploy"),
    pytest.param(["config-deploy", "--target", "user", "--profile", PROFILE], "config-deploy", id="config-deploy"),
)


@pytest.mark.parametrize(("argv", "command"), _BOTH_SPELLINGS)
def test_a_malformed_profile_file_is_a_configuration_error_with_an_envelope(
    argv: list[str],
    command: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``config --profile X`` used to leave ``LayerLoadError`` and exit 70 with an empty stdout."""
    config = _seed_profile(user_config_dir, "[display\n")

    exit_code, out, err = _run([*argv, "--format", "json"], capsys)

    assert exit_code == ExitCode.CONFIG_ERROR, err
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["command"] == command
    assert envelope["error"]["type"] == "CONFIG_ERROR"
    assert str(config) in envelope["error"]["message"]
    # A line of its own rather than the first: in the subcommand form logging is already
    # running, and the library's own record of the failure precedes the refusal.
    assert f"Error: {envelope['error']['message']}" in err.splitlines(), err


@pytest.mark.os_posix
@pytest.mark.parametrize(("argv", "command"), _BOTH_SPELLINGS)
def test_an_unreadable_profile_file_is_refused_as_permission_denied(
    argv: list[str],
    command: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The permission arm of the same mapping: 13, not 70 and not the deploy's 22."""
    if RUNNING_AS_ROOT:
        pytest.skip("root reads a mode-000 file, so there is no refusal to answer")
    config = _seed_profile(user_config_dir, "[display]\n")
    config.chmod(0)
    try:
        exit_code, out, err = _run([*argv, "--format", "json"], capsys)
    finally:
        config.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert exit_code == ExitCode.PERMISSION_DENIED, err
    envelope = json.loads(out)
    assert envelope["command"] == command
    assert envelope["error"]["type"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(("argv", "command"), _BOTH_SPELLINGS)
def test_an_ambiguous_mode_in_the_profile_stops_the_run(
    argv: list[str],
    command: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The one configured value that stops a run stops it whichever spelling named the profile.

    Before, ``config --profile X`` showed the value at exit 0 and ``config-deploy --profile X``
    never asked, so the int reached the deploy, which read it as the decimal mode 0o1363.
    """
    _seed_profile(user_config_dir, "[lib_layered_config.default_permissions]\nuser_file = 644\n")

    exit_code, out, err = _run([*argv, "--format", "json"], capsys)

    assert exit_code == ExitCode.CONFIG_ERROR, err
    envelope = json.loads(out)
    assert envelope["command"] == command
    assert "lib_layered_config.default_permissions.user_file" in envelope["error"]["message"]
    assert not (user_config_dir / "profile" / PROFILE / "config.d").exists(), "a refused deploy wrote files"


def test_config_deploy_reads_enabled_from_the_profile_it_deploys(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``enabled = false`` in X used to be read from the ROOT configuration, which says true."""
    _seed_profile(user_config_dir, "[lib_layered_config.default_permissions]\nenabled = false\n")

    exit_code, out, err = _run(["config-deploy", "--target", "user", "--profile", PROFILE, "--format", "json"], capsys)

    assert exit_code == 0, err
    assert json.loads(out)["data"]["permissions_set"] is False


def test_the_global_profile_already_read_enabled_from_the_profile(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control for the test above: the global spelling of the same request says false."""
    _seed_profile(user_config_dir, "[lib_layered_config.default_permissions]\nenabled = false\n")

    exit_code, out, err = _run(["--profile", PROFILE, "config-deploy", "--target", "user", "--format", "json"], capsys)

    assert exit_code == 0, err
    assert json.loads(out)["data"]["permissions_set"] is False


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--profile", "nosuch", "config"], id="global-config"),
        pytest.param(["config", "--profile", "nosuch"], id="config"),
        pytest.param(["config-deploy", "--target", "user", "--profile", "nosuch"], id="config-deploy"),
    ],
)
def test_a_profile_that_named_nothing_is_warned_about_in_every_spelling(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The subcommand spellings used to load a profile nothing answered to without a word."""
    _seed_profile(user_config_dir, "[display]\nwwn_width = 9\n")

    exit_code, _out, err = _run(argv, capsys)

    said = " ".join(err.split())
    assert exit_code == 0, said
    assert "Warning: profile nosuch named nothing" in said, said


def test_the_warning_names_the_shipped_defaults_as_what_is_in_force(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """It said values were "the one configured without it", which is the unprofiled config.

    A profile replaces the configuration directories, so the unprofiled files are not read
    either: what is in force is the shipped default, unless the environment or --set gives one.
    """
    (user_config_dir / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (user_config_dir / "config.toml").write_text("[display]\nwwn_width = 11\n", encoding="utf-8", newline="\n")

    exit_code, out, err = _run(["--profile", "nosuch", "config", "--section", "display.wwn_width"], capsys)

    said = " ".join(err.split())
    assert exit_code == 0, said
    assert "shipped default" in said, said
    assert "configured without it" not in said, said
    assert "11" not in out, "the unprofiled file was read after all, so the premise of the warning is wrong"


def test_an_empty_profile_is_not_offered_as_its_own_correction(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A profile directory holding nothing readable is the nearest name to itself.

    The warning used to end "Did you mean x?" for ``--profile x``, which sends the reader to
    retype what they already typed rather than to the empty directory.
    """
    (user_config_dir / "profile" / PROFILE).mkdir(parents=True)

    exit_code, _out, err = _run(["--profile", PROFILE, "config"], capsys)

    said = " ".join(err.split())
    assert exit_code == 0, said
    assert f"Warning: profile {PROFILE} named nothing" in said, said
    assert "Did you mean" not in said, said


def test_a_subcommand_profile_keeps_the_explicit_env_file(
    tmp_path: Path,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--env-file F config --profile X`` reloaded X without F, so F's values vanished."""
    _seed_profile(user_config_dir, "[history]\nenabled = true\n")
    dotenv = tmp_path / "chosen.env"
    dotenv.write_text("DISPLAY__WWN_WIDTH=13\n", encoding="utf-8", newline="\n")
    request = ["config", "--section", "display.wwn_width", "--format", "json"]

    sub_code, sub_out, sub_err = _run(["--env-file", str(dotenv), *request, "--profile", PROFILE], capsys)
    global_code, global_out, global_err = _run(["--env-file", str(dotenv), "--profile", PROFILE, *request], capsys)

    assert global_code == 0, global_err
    assert json.loads(global_out)["data"] == {"display.wwn_width": 13}, "the control lost the env file"
    assert sub_code == 0, sub_err
    assert json.loads(sub_out)["data"] == {"display.wwn_width": 13}
