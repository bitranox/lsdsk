"""A deploy with every target already in place is `ok: false` at exit `0`.

COMMANDS.md documents this as the one `ok: false` that the two commands writing
configuration files leave at exit zero - every write they could not make leaves
`13` or `74` - so the claim is held here through the real command line, for both.
Writing nothing because the files exist is the expected outcome rather than a
failure; `ok` says the run changed nothing, and `skipped` says why.
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
def test_examples_already_in_place_are_ok_false_at_exit_zero(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The first run writes and is `ok`; the second writes nothing, says why, and still exits `0`."""
    argv = ["config-generate-examples", "--destination", str(tmp_path / "examples"), "--format", "json"]
    first, second = _twice(cli_runner, production_factory, argv)

    assert first.exit_code == 0, first.output[-300:]
    assert json.loads(first.stdout)["ok"] is True, "the control: a run that wrote its files is ok"
    assert second.exit_code == 0, second.output[-300:]
    envelope = json.loads(second.stdout)
    assert envelope["ok"] is False
    assert envelope["skipped"] == ["every example file already exists; --force overwrites"]


@pytest.mark.os_posix
def test_a_deploy_already_in_place_is_ok_false_at_exit_zero(
    cli_runner: CliRunner, production_factory: Callable[[], Any], user_config_dir: Path
) -> None:
    """The same for ``config-deploy``, into this test's own user configuration directory."""
    del user_config_dir
    argv = ["config-deploy", "--target", "user", "--format", "json"]
    first, second = _twice(cli_runner, production_factory, argv)

    assert first.exit_code == 0, first.output[-300:]
    assert json.loads(first.stdout)["ok"] is True, "the control: a deploy that wrote its files is ok"
    assert second.exit_code == 0, second.output[-300:]
    envelope = json.loads(second.stdout)
    assert envelope["ok"] is False
    assert envelope["skipped"] == ["every target file already exists; --force overwrites"]
