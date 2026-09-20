"""A failing command in machine-readable mode still answers in that format.

A pipeline that asked for JSON and got an empty stream cannot tell a command
that failed from one that produced nothing, and it has no message to report
either: the human prose went to stderr, which is not the stream being parsed.
So every error path that a ``--format json`` run can reach emits exactly one
object on stdout.

The exit code is unchanged by any of this, and each arm asserts it: the code is
the primary answer and the envelope is the detail, not a replacement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json"
#: A real file that is certainly not a capture, so the snapshot reader refuses it.
NOT_A_CAPTURE = Path(__file__).parent.parent / "README.md"

#: Stands in for a path only known at run time. Compared by IDENTITY, so a real
#: argument that happened to have the same text could never be replaced by mistake.
OUTPUT_PLACEHOLDER = "<the output path this test is given>"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("argv", "code", "error_type", "command"),
    [
        pytest.param(
            ["findings", "--replay", str(NOT_A_CAPTURE), "--format", "json"],
            ExitCode.CONFIG_ERROR,
            "CONFIG_ERROR",
            "findings",
            id="a file that is not a capture",
        ),
        pytest.param(
            ["disks", "--replay", str(NOT_A_CAPTURE), "--format", "json"],
            ExitCode.CONFIG_ERROR,
            "CONFIG_ERROR",
            "disks",
            id="the same refusal names its own command",
        ),
        pytest.param(
            ["config", "--section", "nonexistent_section_that_does_not_exist", "--format", "json"],
            ExitCode.INVALID_ARGUMENT,
            "INVALID_ARGUMENT",
            "config",
            id="a section that does not exist",
        ),
        pytest.param(
            # --replay is the ROOT group's option, which is the whole point of
            # this arm: snapshot refuses it rather than ignoring it. Written
            # after the subcommand it is "No such option" and exit 2 from Click,
            # which is a different failure and would not reach the refusal.
            ["--replay", str(CAPTURE), "snapshot", "-o", OUTPUT_PLACEHOLDER, "--format", "json"],
            ExitCode.INVALID_ARGUMENT,
            "INVALID_ARGUMENT",
            "snapshot",
            id="a command refusing an option that does not apply to it",
        ),
    ],
)
def test_a_failing_json_run_emits_one_error_object_on_stdout(
    argv: list[str],
    code: ExitCode,
    error_type: str,
    command: str,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """Measured before this existed: every one of these wrote 0 bytes to stdout."""
    written = str(tmp_path / "capture.json")
    result: Result = cli_runner.invoke(
        cli_mod.cli, [written if part is OUTPUT_PLACEHOLDER else part for part in argv], obj=production_factory
    )

    assert result.exit_code == code, (
        f"the exit code moved: {result.exit_code} is not {int(code)}; stderr was {result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is False, "a failure must be distinguishable from a complete answer by ok alone"
    assert payload["command"] == command, (
        f"the envelope names {payload['command']!r}, so a reader cannot tell which command failed"
    )
    assert payload["error"]["type"] == error_type, (
        f"the typed error name is {payload['error']['type']!r}, which does not match the exit code"
    )
    assert payload["error"]["message"], "an empty message leaves the caller with nothing to report"


@pytest.mark.os_agnostic
def test_a_failing_json_run_still_puts_the_human_sentence_on_stderr(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """The envelope is an addition, not a move.

    A person running the command by hand reads stderr, and a machine reads
    stdout. Moving the sentence rather than duplicating it would have fixed the
    pipeline and broken the person, and nothing in the parametrized arms above
    would have noticed.
    """
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["findings", "--replay", str(NOT_A_CAPTURE), "--format", "json"],
        obj=production_factory,
    )

    assert "Error:" in result.stderr, "the human sentence left stderr, so an interactive user sees nothing"
    payload = json.loads(result.stdout)
    assert payload["error"]["message"] in result.stderr, (
        "the envelope's message and the sentence on stderr have drifted apart, "
        "so a bug report and a log line describe the same failure differently"
    )


@pytest.mark.os_agnostic
def test_a_failing_human_run_writes_nothing_to_stdout(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """The control: the envelope belongs to the machine-readable mode alone.

    Without this, emitting the object unconditionally would pass every arm above
    while putting JSON in front of a person who asked for a report.
    """
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["findings", "--replay", str(NOT_A_CAPTURE)],
        obj=production_factory,
    )

    assert result.exit_code == ExitCode.CONFIG_ERROR
    assert result.stdout == "", f"a human-mode failure put {result.stdout!r} on stdout"
    assert "Error:" in result.stderr
