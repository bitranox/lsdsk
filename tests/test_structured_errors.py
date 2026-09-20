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


def _refused_command_line(
    argv: list[str],
    production_factory: Callable[[], Any],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    """Drive a malformed command line through the REAL entry point.

    ``CliRunner.invoke`` cannot reach this path: it runs the group in click's
    standalone mode, which prints the usage message and returns the code itself,
    so the handler in :mod:`lsdsk.adapters.cli.main` that this behaviour lives in
    never executes. Every arm below therefore calls ``main`` the way the console
    script does.

    Returns:
        The exit code, what reached stdout, and what reached stderr.
    """
    code = cli_mod.main(argv, services_factory=production_factory)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("argv", "command", "names"),
    [
        pytest.param(["nosuchcommand", "--format", "json"], "lsdsk", "nosuchcommand", id="an unknown command"),
        pytest.param(["disks", "--bogus", "--format", "json"], "disks", "--bogus", id="an unknown option"),
        pytest.param(["disks", "--format=json", "--bogus"], "disks", "--bogus", id="the --format=json form"),
        pytest.param(["disks", "--bogus", "--format", "JSON"], "disks", "--bogus", id="the choice is case-insensitive"),
        pytest.param(["snapshot", "--format", "json"], "snapshot", "--output", id="a missing required option"),
    ],
)
def test_a_usage_error_answers_in_json_when_the_command_line_asked_for_it(
    argv: list[str],
    command: str,
    names: str,
    production_factory: Callable[[], Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed command line is a failure like any other, and JSON was asked for.

    Measured 2026-09-20 before this existed: every one of these wrote 0 bytes to
    stdout, so a ``--format json`` pipeline could not tell a mistyped option from
    a command that produced no data, and the sentence saying which option was
    wrong went to stderr, which is not the stream being parsed.

    The intent is read from the command line itself because there is nowhere else
    it survives: click raises these before any command callback runs, so no
    ``output_format`` parameter has been processed.
    """
    code, out, err = _refused_command_line(argv, production_factory, capsys)

    assert code == 2, f"click's usage code moved: {code}; stderr was {err!r}"
    payload = json.loads(out)
    assert payload["ok"] is False, "a refused command line must be distinguishable from an answer by ok alone"
    assert payload["command"] == command, (
        f"the envelope names {payload['command']!r}, which is not the command click refused"
    )
    assert payload["error"]["type"] == "USAGE_ERROR", (
        f"the typed error name is {payload['error']['type']!r}, which does not name click's exit 2"
    )
    assert names in payload["error"]["message"], (
        f"the message {payload['error']['message']!r} does not say what was wrong with the command line"
    )
    assert names in err, "the usage prose left stderr, so a person running this by hand sees nothing"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("argv", "why"),
    [
        pytest.param(["nosuchcommand"], "no format was asked for at all", id="the shipped human default"),
        pytest.param(["disks", "--bogus", "--format", "human"], "human was asked for", id="human asked for by name"),
        pytest.param(["disks", "--bogus", "--format", "nope"], "the format itself is what is wrong", id="a bad choice"),
        pytest.param(["--", "--format", "json"], "after -- those tokens are arguments", id="past the -- separator"),
    ],
)
def test_a_refused_command_line_keeps_stdout_empty_unless_json_was_asked_for(
    argv: list[str],
    why: str,
    production_factory: Callable[[], Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: without it, emitting unconditionally passes every arm above.

    Three of these four are the ways the sniff can be WRONG rather than absent -
    a format asked for by name, a format that is itself the error, and tokens
    past the separator that click is not reading as options at all.
    """
    code, out, err = _refused_command_line(argv, production_factory, capsys)

    assert code == 2, f"click's usage code moved: {code}; stderr was {err!r}"
    assert out == "", f"stdout carried {out!r} although {why}"
    assert err != "", "the usage message has to reach somebody"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("args", "asked"),
    [
        pytest.param(["disks", "--format", "json"], True, id="the two-token form"),
        pytest.param(["disks", "--format=json"], True, id="the joined form"),
        pytest.param(["disks", "--format", "JSON"], True, id="upper case, as click.Choice accepts"),
        pytest.param(["disks", "--format=JsOn"], True, id="mixed case, joined"),
        pytest.param(["disks"], False, id="nothing asked for"),
        pytest.param(["disks", "--format", "human"], False, id="human by name"),
        pytest.param(["disks", "--format"], False, id="the flag with no value at all"),
        pytest.param(["disks", "--format", "json", "--format", "human"], False, id="the last one wins, as click does"),
        pytest.param(["disks", "--format", "human", "--format", "json"], True, id="the last one wins the other way"),
        pytest.param(["--", "--format", "json"], False, id="past the -- separator"),
        pytest.param(["disks", "--formats", "json"], False, id="a longer option that merely starts the same"),
        pytest.param(["disks", "--set", "a.b=--format", "json"], False, id="the flag's text as another option's value"),
    ],
)
def test_reading_the_json_intent_off_the_command_line(args: list[str], asked: bool) -> None:
    """The sniff itself, which is the one place this design guesses.

    Click owns the value everywhere else, so this function is the only reader of
    ``--format`` that can disagree with the parser. Each arm names a way they
    could, and the two that are genuinely ambiguous - a value that happens to
    read like the flag, and tokens past ``--`` - resolve the conservative way:
    no envelope rather than one nobody asked for.
    """
    from lsdsk.adapters.cli.envelope import asked_for_json

    assert asked_for_json(args) is asked


@pytest.mark.os_agnostic
def test_a_code_no_exit_member_names_is_reported_as_the_code_itself() -> None:
    """``error.type`` stays one decider, and never invents a second word.

    Every code this tool can leave has a member, so this is the arm for a code
    arriving from somewhere else - a ``ClickException`` subclass a library
    defines with its own ``exit_code``. Naming it after the nearest member would
    tell a caller the name and the code agree when they do not.
    """
    from lsdsk.adapters.cli.exit_codes import ExitCode, error_type_for

    assert error_type_for(int(ExitCode.CONFIG_ERROR)) == "CONFIG_ERROR"
    assert error_type_for(2) == "USAGE_ERROR"
    assert error_type_for(99) == "EXIT_99"
