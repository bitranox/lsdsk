"""A configuration failure and a permission failure leave the codes for them.

Two mismatches, both in the configuration commands, both of which made a caller
read one kind of failure as another.

A malformed configuration FILE escaped as the library's own exception. It
reached the top-level handler, printed `LayerLoadError: Invalid TOML in ...`
with no `Error:` in front of it - the only refusal in this tool without one -
and left the code that means "this tool broke". It is the one failure in the
whole program that is literally a configuration error, and `78`, which a
malformed CAPTURE already gets, was never used for a configuration.

And `config-generate-examples` caught `OSError` wholesale, so a refused
directory left `1` where `config-deploy` and `snapshot` both leave `13` with a
sudo hint for the identical errno on the identical operation.
"""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner


@pytest.fixture
def unreadable_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A configuration home whose user file is not valid TOML."""
    root = tmp_path / "xdg"
    (root / "lsdsk").mkdir(parents=True)
    broken = root / "lsdsk" / "config.toml"
    broken.write_text("this is not = valid toml [[[\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    from lsdsk.adapters.config.loader import get_config

    get_config.cache_clear()
    return broken


@pytest.mark.os_posix
def test_a_malformed_configuration_file_is_a_configuration_error(
    unreadable_config: Path, cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """`78` is `EX_CONFIG`, and this is the configuration failing to load."""
    result = cli_runner.invoke(cli, ["config"], obj=production_factory)

    assert result.exit_code == ExitCode.CONFIG_ERROR, f"left {result.exit_code}: {result.output[-300:]}"
    said = " ".join((result.stderr or "").split())
    assert said.startswith("Error:"), f"the one refusal without the prefix every other one has: {said}"
    assert str(unreadable_config) in said, said
    assert "LayerLoadError" not in said, f"the library's class name is not a message for a reader: {said}"


@pytest.mark.os_posix
def test_the_malformed_file_is_handled_rather_than_left_to_escape(
    unreadable_config: Path, cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """What reaches the top-level handler is reported as a fault in this tool.

    Asserted as "no exception escaped" rather than as "the code is not 70",
    because the test runner reports an escaping exception as exit 1 while the
    real entry point maps it to 70 - so a check on the number passes here
    whatever happens, and this one cannot.
    """
    del unreadable_config
    result = cli_runner.invoke(cli, ["config"], obj=production_factory)
    assert not isinstance(result.exception, BaseException) or isinstance(result.exception, SystemExit), (
        f"the loader's exception escaped the command: {result.exception!r}"
    )


@pytest.mark.os_agnostic
def test_a_readable_configuration_still_loads(cli_runner: CliRunner, production_factory: Callable[[], Any]) -> None:
    """The control for the pair above: nothing here refuses an ordinary run."""
    result = cli_runner.invoke(cli, ["config", "--format", "json"], obj=production_factory)
    assert result.exit_code == ExitCode.SUCCESS, result.output[-300:]
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.os_posix
def test_generate_examples_answers_a_refused_directory_the_way_deploy_does(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """Same operation, same errno, so the same code and the same hint.

    Skipped for root, who can write into a directory with no write bit and would
    see the command succeed.
    """
    if os.geteuid() == 0:
        pytest.skip("root writes into a read-only directory, so there is no refusal to answer")

    refused = tmp_path / "read-only"
    refused.mkdir()
    refused.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = cli_runner.invoke(
            cli, ["config-generate-examples", "--destination", str(refused / "examples")], obj=production_factory
        )
    finally:
        refused.chmod(stat.S_IRWXU)

    assert result.exit_code == ExitCode.PERMISSION_DENIED, f"left {result.exit_code}: {result.output[-300:]}"
    said = " ".join((result.stderr or "").split())
    assert "Permission denied" in said, said
    assert "sudo" in said, f"deploy names sudo for the same failure and this does not: {said}"


@pytest.mark.os_posix
def test_generate_examples_still_reports_a_non_permission_failure_as_one(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The control: the permission arm must not have swallowed every OSError.

    A regular file standing where the destination directory should be raises
    ENOTDIR or EEXIST rather than EACCES, which is how the sibling test for
    ``record`` reaches the same branch portably.
    """
    in_the_way = tmp_path / "not-a-directory"
    in_the_way.write_text("", encoding="utf-8")

    result = cli_runner.invoke(
        cli, ["config-generate-examples", "--destination", str(in_the_way / "examples")], obj=production_factory
    )
    assert result.exit_code == ExitCode.GENERAL_ERROR, f"left {result.exit_code}: {result.output[-300:]}"
