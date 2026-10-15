"""``--set lib_layered_config.default_permissions=<one value>`` is refused by ``config-deploy``.

The deploy reads that section's modes from the configuration files, and lsdsk hands it only the
keys ``--set`` gave inside the table plus the ``enabled`` answer it settled itself. A value in
place of the whole table fitted neither half: lsdsk warned it was "Using the shipped section",
settled ``enabled`` from the shipped default, and the deploy then applied whatever modes the
files configured - so the run did something no sentence on screen described, at exit 0. No
reading of such a value can be honoured consistently, so the deploy refuses it (22) before
anything is written. Every other command still treats it as the warning it was.
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

SECTION = "lib_layered_config.default_permissions"


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the real entry point with the production wiring; the code, stdout and stderr."""
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(list(argv), services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.mark.parametrize("value", ["5", "[1]", "true", "abc"])
def test_the_section_given_as_one_value_refuses_the_deploy(
    value: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, out, err = _run(
        ["--set", f"{SECTION}={value}", "config-deploy", "--target", "user", "--format", "json"], capsys
    )

    assert exit_code == ExitCode.INVALID_ARGUMENT, err
    envelope = json.loads(out)
    assert envelope["command"] == "config-deploy"
    assert envelope["error"]["type"] == "INVALID_ARGUMENT"
    assert SECTION in envelope["error"]["message"] and "table" in envelope["error"]["message"], envelope
    assert "Using the shipped" not in err, err
    assert not (user_config_dir / "config.toml").exists(), "a refused deploy wrote the file"


def test_the_section_given_as_a_table_still_deploys(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: a JSON object IS a table, and its keys reach the deploy as before."""
    exit_code, _out, err = _run(
        ["--set", f'{SECTION}={{"user_file": "0o640"}}', "config-deploy", "--target", "user", "--format", "json"],
        capsys,
    )

    assert exit_code == 0, err
    assert (user_config_dir / "config.toml").is_file()


def test_a_command_that_deploys_nothing_still_only_warns(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only the deploy consumes the section; ``config`` reports it and runs on."""
    del user_config_dir
    exit_code, _out, err = _run(["--set", f"{SECTION}=5", "config", "--section", "display"], capsys)

    assert exit_code == 0, err
