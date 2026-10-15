"""An int permission mode that could be octal digits stops the run; every other mode is the deploy's to judge.

lib_layered_config 7.1 reads an unquoted ``.env`` value such as ``755`` as the int 755, which is
ambiguous: octal digits or a numeric mode (755 decimal is 0o1363)? lsdsk lets almost no configured
value stop a run, but this ambiguity is the one exception: it is a startup configuration error
(exit 78) naming the text spelling to use instead. No other mode is judged by lsdsk at all:
``config-deploy`` hands the section to the library, which refuses what it cannot use
(``test_config_deploy_mode_has_one_decider.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.permissions import PermissionReading, read_permission_defaults
from lsdsk.adapters.config.values import AmbiguousModeError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

_HINT = "write it as text: 0755 or 0o755"
_KEY = "LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__APP_DIRECTORY"


def _reading_of(app_directory: object) -> PermissionReading:
    section = {"lib_layered_config": {"default_permissions": {"app_directory": app_directory}}}
    return read_permission_defaults(Config(section, {}))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("ambiguous", [755, 644, 420, 0])
def test_an_int_of_octal_digits_only_is_an_error_naming_the_text_spelling(ambiguous: int) -> None:
    with pytest.raises(AmbiguousModeError) as raised:
        _reading_of(ambiguous)

    assert raised.value.dotted == "lib_layered_config.default_permissions.app_directory"
    assert _HINT in str(raised.value)


@pytest.mark.parametrize("unambiguous", [0o750, "0755", "755", "0o755", True, "abc", -5])
def test_any_other_mode_is_left_to_the_deploy_without_a_word(unambiguous: object) -> None:
    """An int with an 8 or 9, text and a bool neither stop the run nor draw a fallback claim here."""
    reading = _reading_of(unambiguous)

    assert reading.rejected == ()
    assert reading.defaults.enabled is True


@pytest.mark.os_agnostic
def test_the_shipped_default_config_loads_without_a_refusal(clear_config_cache: None) -> None:
    reading = read_permission_defaults(get_config())

    assert reading.rejected == ()
    assert reading.defaults.enabled is True


@pytest.mark.os_agnostic
@pytest.mark.parametrize("ambiguous", ["755", "644", "420", "0"])
def test_a_dotenv_int_mode_stops_the_real_command_with_the_config_exit_code(
    ambiguous: str,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    clear_config_cache: None,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{_KEY}={ambiguous}\n", encoding="utf-8")

    result = cli_runner.invoke(cli, ["--env-file", str(dotenv), "config"], obj=production_factory)

    assert result.exit_code == ExitCode.CONFIG_ERROR == 78, result.output[-300:]
    said = " ".join((result.stderr or "").split())
    assert said.startswith("Error:") and _HINT in said, said


@pytest.mark.os_agnostic
@pytest.mark.parametrize("accepted", ["0750", "0o750", "488"])
def test_a_dotenv_text_or_eight_nine_mode_still_runs(
    accepted: str,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    clear_config_cache: None,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{_KEY}={accepted}\n", encoding="utf-8")

    result = cli_runner.invoke(cli, ["--env-file", str(dotenv), "config"], obj=production_factory)

    assert result.exit_code == 0, (result.stderr or "")[-300:]
    assert _HINT not in (result.stderr or "")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("other", ["true", "-5", "rwx"])
def test_every_other_dotenv_mode_lets_the_run_go_on_without_a_fallback_claim(
    other: str,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    clear_config_cache: None,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{_KEY}={other}\n", encoding="utf-8")

    reading = read_permission_defaults(get_config(dotenv_path=str(dotenv)))
    assert reading.rejected == ()

    result = cli_runner.invoke(cli, ["--env-file", str(dotenv), "config"], obj=production_factory)
    assert result.exit_code == 0, (result.stderr or "")[-300:]
