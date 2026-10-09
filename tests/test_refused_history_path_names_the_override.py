"""A refused ``history.path`` names the file the run really uses.

``read_history_settings`` takes ``path_override`` so the warning for a value the
tool could not use ends ``Using <override>.`` on a run told to use somewhere
else, rather than naming the per-user state file it never touches. The root
group passes the override at the one call that reports the refusal; dropping it
there left the warning false on exactly the runs that pass ``--history-file``,
and no test combined the two.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lsdsk.adapters.cli import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner


def warnings_of(runner: CliRunner, factory: Callable[[], object], *args: str) -> str:
    """What a run wrote to standard error, for one ``info`` invocation."""
    result = runner.invoke(cli, [*args, "--no-record", "info"], obj=factory)
    return result.stderr


def test_a_refused_history_path_names_the_command_line_override_as_the_one_in_force(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """``--history-file X --set history.path=3`` ends 'Using X.', not the state file."""
    override = tmp_path / "chosen.json"

    stderr = warnings_of(cli_runner, production_factory, "--history-file", str(override), "--set", "history.path=3")

    assert "ignoring history.path=3" in stderr, stderr
    assert f"Using {override}." in stderr, stderr
    assert "per-user state file" not in stderr, stderr


def test_a_refused_history_path_without_an_override_names_the_state_file(
    cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The control: with no override the fallback is the per-user state file."""
    stderr = warnings_of(cli_runner, production_factory, "--set", "history.path=3")

    assert "ignoring history.path=3" in stderr, stderr
    assert "Using the per-user state file." in stderr, stderr
