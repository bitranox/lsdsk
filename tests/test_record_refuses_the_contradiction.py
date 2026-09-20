"""`--no-record record` asks the recording command not to record.

On every other command the flag means something sensible - judge the counters
against what is on disk without adding this reading to it. On `record`, whose
only job is to add this reading, it means nothing can happen, and the run
reported success: exit 0, no store written, and in human mode, which is the form
a timer runs, not a word on either stream. A scheduled sampler that inherited
the flag from a wrapper would never record and never say so.

It is refused, on the precedent one file away: `snapshot` refuses a global
`--replay` at exit 22 with a sentence saying what to do instead, for the same
reason - the caller asked for two things that cannot both be true, and guessing
which they meant is how a job runs wrong for a year.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

FIXTURE = "tests/fixtures/hw/linux-sas-hba.json"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("output_format", ["human", "json"])
def test_no_record_with_record_is_refused_rather_than_quietly_obeyed(
    output_format: str, cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """In both forms, because the human one is the one a timer runs."""
    store = tmp_path / "history.json"
    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "--no-record", "record", "--format", output_format, "--replay", FIXTURE],
        obj=production_factory,
    )

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    said = " ".join((result.stderr or "").split())
    assert "--no-record" in said, said or "(nothing on stderr)"
    assert "record" in said
    assert not store.exists(), "a refused run must not have written the store"


@pytest.mark.os_agnostic
def test_the_refusal_names_what_to_do_instead(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """A refusal that only says no leaves the caller where they were."""
    result = cli_runner.invoke(
        cli,
        ["--history-file", str(tmp_path / "history.json"), "--no-record", "record", "--replay", FIXTURE],
        obj=production_factory,
    )
    said = " ".join((result.stderr or "").split())
    assert "trend" in said or "drop" in said or "without" in said, said


@pytest.mark.os_agnostic
def test_the_refusal_is_an_error_envelope_and_not_a_result_a_caller_could_act_on(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """A machine caller gets JSON on failure too, and it must say failure.

    Asserted on stdout alone and by DECODING it, because stderr carries the
    prose and would make any parse fail whatever stdout held - and because
    reading the refusal as a result is the one way this could still be silent
    to a program.
    """
    result = cli_runner.invoke(
        cli,
        [
            "--history-file",
            str(tmp_path / "history.json"),
            "--no-record",
            "record",
            "--format",
            "json",
            "--replay",
            FIXTURE,
        ],
        obj=production_factory,
    )
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False, envelope
    assert envelope["error"]["type"] == "INVALID_ARGUMENT", envelope
    assert "--no-record" in envelope["error"]["message"], envelope
    assert "data" not in envelope, envelope


@pytest.mark.os_agnostic
def test_the_flag_still_means_what_it_means_on_every_other_command(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The control, and the reason this is a refusal rather than a removal.

    ``--no-record`` on a reporting command is not a contradiction at all: it
    judges the counters against the store without adding to it. If this went
    red the refusal would have been put on the flag instead of on the pairing.
    """
    result = cli_runner.invoke(
        cli,
        ["--history-file", str(tmp_path / "history.json"), "--no-record", "trend", "--replay", FIXTURE],
        obj=production_factory,
    )
    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.GENERAL_ERROR), result.output


@pytest.mark.os_agnostic
def test_record_without_the_flag_is_untouched(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The other control: the refusal must be about the pairing, not the command."""
    store = tmp_path / "history.json"
    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "record", "--format", "json", "--replay", FIXTURE],
        obj=production_factory,
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert json.loads(result.stdout)["command"] == "record"
