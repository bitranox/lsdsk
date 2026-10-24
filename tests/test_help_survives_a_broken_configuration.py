"""A subcommand's help is printed while a configuration file is broken.

The root group loads the configuration before click parses the subcommand, so a user
``config.toml`` the TOML reader refuses stopped ``lsdsk disks --help`` with 78 before click could
print a word - and ``config-deploy --help`` with it, the page of the command that repairs such a
file. Only the root's own ``--help`` worked, because click answers that before the group's
callback runs. A request for help asks nothing of the configuration, so the group now reads the
subcommand's arguments with its own parser first and, where they ask for help, loads nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import rich_click as click

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.cli.root import cli
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: A file the TOML reader refuses.
BROKEN = "[display\n"


def _help_requests() -> list[object]:
    """Every subcommand with every spelling of help its own context settings name."""
    requests: list[object] = []
    for name, command in sorted(cli.commands.items()):
        context = click.Context(command, info_name=name, **command.context_settings)
        option = command.get_help_option(context)
        assert option is not None, f"the premise: {name} has a help option"
        requests.extend(pytest.param(name, spelling, id=f"{name}{spelling}") for spelling in option.opts)
    return requests


def _break_the_user_config(user_config_dir: Path) -> None:
    user_config_dir.mkdir(parents=True, exist_ok=True)
    (user_config_dir / "config.toml").write_text(BROKEN, encoding="utf-8", newline="\n")


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the real entry point with the production wiring; the code, stdout and stderr."""
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(list(argv), services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.mark.parametrize(("name", "spelling"), _help_requests())
def test_every_subcommand_prints_its_help_while_the_configuration_is_broken(
    name: str,
    spelling: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _break_the_user_config(user_config_dir)

    exit_code, out, err = _run([name, spelling], capsys)

    assert exit_code == 0, err
    assert "Usage:" in out, out
    assert "not valid TOML" not in err, err


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["config-deploy", "--target", "user", "--help"], id="config-deploy-after-options"),
        pytest.param(["disks", "--format", "json", "-h"], id="disks-after-options"),
    ],
)
def test_help_after_the_subcommand_s_options_is_still_help(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _break_the_user_config(user_config_dir)

    exit_code, out, err = _run(argv, capsys)

    assert exit_code == 0, err
    assert "Usage:" in out, out


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(["disks"], ExitCode.CONFIG_ERROR, id="disks"),
        pytest.param(["config-deploy", "--target", "user"], ExitCode.CONFIG_ERROR, id="config-deploy"),
        # A profile NAMED -h is not a request for help: the name is judged, and refused.
        pytest.param(["config", "--profile", "-h"], ExitCode.INVALID_ARGUMENT, id="profile-named-h"),
    ],
)
def test_a_run_that_does_not_ask_for_help_still_refuses(
    argv: list[str],
    expected: ExitCode,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: the help request is what skips the load, nothing else does."""
    _break_the_user_config(user_config_dir)

    exit_code, out, err = _run(argv, capsys)

    assert exit_code == expected, err
    assert "Usage:" not in out, out
