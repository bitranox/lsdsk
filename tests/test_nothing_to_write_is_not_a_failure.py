"""A deploy with every target already in place is `ok: true` at exit `0`.

Writing nothing because the files already exist is the expected outcome of running
either configuration-writing command twice, not a failure, so `ok` agrees with the
exit code and `data.outcome` says the run changed nothing. Every write they could
not make leaves `13` or `74` and `ok: false` instead. Held through the real
command line, for both commands.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result


def _twice(cli_runner: CliRunner, factory: Callable[[], Any], argv: list[str]) -> tuple[Result, Result]:
    """Run `argv` once to write the files and once more with them all in place."""
    first = cli_runner.invoke(cli, argv, obj=factory)
    second = cli_runner.invoke(cli, argv, obj=factory)
    return first, second


@pytest.mark.os_agnostic
def test_examples_already_in_place_are_ok_true_at_exit_zero(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The first run writes; the second writes nothing, is still `ok`, and names the outcome."""
    argv = ["config-generate-examples", "--destination", str(tmp_path / "examples"), "--format", "json"]
    first, second = _twice(cli_runner, production_factory, argv)

    first_envelope = json.loads(first.stdout)
    assert first.exit_code == 0, first.output[-300:]
    assert first_envelope["ok"] is True, "the control: a run that wrote its files is ok"
    assert first_envelope["data"]["outcome"] == "written"
    envelope = json.loads(second.stdout)
    assert second.exit_code == 0, second.output[-300:]
    assert envelope["ok"] is (second.exit_code == 0)
    assert envelope["skipped"] == []
    assert envelope["data"]["outcome"] == "already present"
    assert envelope["data"]["generated"] == []


@pytest.mark.os_posix
def test_a_deploy_already_in_place_is_ok_true_at_exit_zero(
    cli_runner: CliRunner, production_factory: Callable[[], Any], user_config_dir: Path
) -> None:
    """The same for ``config-deploy``, into this test's own user configuration directory."""
    del user_config_dir
    argv = ["config-deploy", "--target", "user", "--format", "json"]
    first, second = _twice(cli_runner, production_factory, argv)

    first_envelope = json.loads(first.stdout)
    assert first.exit_code == 0, first.output[-300:]
    assert first_envelope["ok"] is True, "the control: a deploy that wrote its files is ok"
    assert first_envelope["data"]["outcome"] == "written"
    envelope = json.loads(second.stdout)
    assert second.exit_code == 0, second.output[-300:]
    assert envelope["ok"] is (second.exit_code == 0)
    assert envelope["skipped"] == []
    assert envelope["data"]["outcome"] == "already present"
    assert envelope["data"]["deployed"] == []
