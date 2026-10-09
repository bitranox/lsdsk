"""Every way of naming a profile the library refuses answers 22, in both formats."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config, validate_profile
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence


#: Each command line that carries a profile name, with the command it should name.
_PROFILE_COMMAND_LINES = (
    pytest.param(["--profile", "../x", "disks"], "disks", id="global-disks"),
    pytest.param(["--profile", "../x", "config"], "config", id="global-config"),
    pytest.param(["config", "--profile", "../x"], "config", id="config"),
    pytest.param(["config-deploy", "--target", "user", "--profile", "../x"], "config-deploy", id="config-deploy"),
)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("argv", "command"), _PROFILE_COMMAND_LINES)
def test_an_invalid_profile_is_refused_with_22_and_an_envelope_in_json(
    argv: Sequence[str],
    command: str,
    managed_traceback_state: None,
    clear_config_cache: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``config --profile ../x`` used to leave a bare ``ValueError``: rc 70 and an empty stdout."""
    exit_code = cli_mod.main([*argv, "--format", "json"], services_factory=build_production)

    captured = capsys.readouterr()
    assert exit_code == ExitCode.INVALID_ARGUMENT, captured.err
    envelope = json.loads(captured.out)
    assert envelope["ok"] is False
    assert envelope["command"] == command
    assert envelope["error"]["type"] == "INVALID_ARGUMENT"
    assert "profile" in envelope["error"]["message"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("argv", [line.values[0] for line in _PROFILE_COMMAND_LINES])
def test_an_invalid_profile_is_refused_with_22_in_human(
    argv: Sequence[str],
    managed_traceback_state: None,
    clear_config_cache: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The human arm of the same refusal: the sentence on stderr, nothing on stdout."""
    exit_code = cli_mod.main([*argv, "--format", "human"], services_factory=build_production)

    captured = capsys.readouterr()
    assert exit_code == ExitCode.INVALID_ARGUMENT, captured.err
    assert "profile" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", [7, b"x", ["a"]])
def test_a_profile_that_is_not_text_is_a_value_error_not_a_type_error(name: object) -> None:
    """``get_config(profile=7)`` documents ``ValueError`` for every refused name."""
    with pytest.raises(ValueError, match="profile"):
        validate_profile(cast("str", name))
    with pytest.raises(ValueError, match="profile"):
        get_config(profile=cast("str", name))
