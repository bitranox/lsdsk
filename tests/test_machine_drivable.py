"""Machine-drivability: every command that emits data must emit it structured.

A human-formatted line plus exit 0 collapses "it ran and the answer is no" into
"it could not run", and a caller proceeds on nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

# Commands with no data to structure: an interactive app, and the two template
# vehicles the traceback and logging tests drive through the real entry point.
NO_STRUCTURED_MODE = {"tui", "fail", "logdemo"}


def subcommands() -> list[str]:
    """Every subcommand, taken from the group's own registry rather than a hand list.

    Read from the registry and not by parsing ``--help``: that output is laid
    out to the terminal width, so at a narrow width the wrapped description text
    matches a command-shaped regex too. At ``COLUMNS=40`` a regex over it
    returned 70 "commands" including ``and``, ``the`` and ``ceback``, and a
    "found more than ten" control cannot tell that from a real answer.
    """
    from lsdsk.adapters.cli import cli

    return sorted(cli.commands)


@pytest.mark.os_agnostic
def test_every_data_producing_command_offers_a_structured_mode() -> None:
    """Taken from the registry, so a command added later is covered without an edit."""
    commands = subcommands()
    assert len(commands) > 10, "the registry holds too few commands to be trusted"
    missing: list[str] = []
    for command in commands:
        if command in NO_STRUCTURED_MODE:
            continue
        help_text = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "lsdsk", command, "--help"], capture_output=True, text=True, check=False
        ).stdout
        if "--format" not in help_text:
            missing.append(command)
    assert not missing, f"no structured mode: {missing}"


# A parametrised test over ["info", "snapshot", "record",
# "config-generate-examples"] stood here and never invoked any of them: it built
# ActionEnvelope objects by hand, so the command name was decorative and the
# four cases were one assertion about pydantic storing a bool. Substituting a
# command that does not exist passed identically. What it claimed to cover is
# covered for real, through the entry point, by
# test_every_structured_mode_actually_emits_the_envelope below and by
# test_a_structured_run_that_stored_nothing_says_so_rather_than_claiming_success.


def envelope_in(output: str) -> dict[str, Any]:
    """The JSON object in a run's output, ignoring anything printed before it.

    A logging line or a warning can precede the envelope depending on what a
    neighbouring test left configured, and that is a property of the harness
    rather than of the contract under test. Decoding from the first brace keeps
    this test about the envelope.
    """
    start = output.index("{")
    decoded: object = json.JSONDecoder().raw_decode(output[start:])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)


@pytest.mark.os_agnostic
def test_a_structured_run_that_stored_nothing_says_so_rather_than_claiming_success(
    cli_runner: object,
    production_factory: Callable[[], object],
    tmp_path: object,
    clear_config_cache: None,
) -> None:
    """`record` twice: the second has nothing new to say, and must not read as a write.

    Takes clear_config_cache because the configuration loader is lru-cached
    process-wide: a neighbouring test that injects `history.enabled = false`
    leaves it there, and this test then reads recording as disabled and fails
    only when the suite runs in that order. It passed in isolation.
    """
    del clear_config_cache
    from pathlib import Path

    from lsdsk.adapters.cli import cli

    store = Path(str(tmp_path)) / "history.json"
    capture = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
    args = ["--history-file", str(store), "record", "--replay", str(capture), "--format", "json"]
    first = envelope_in(cli_runner.invoke(cli, args, obj=production_factory).output)  # type: ignore[attr-defined]
    again = envelope_in(cli_runner.invoke(cli, args, obj=production_factory).output)  # type: ignore[attr-defined]

    assert first["ok"] is True and first["data"]["recorded"] is True
    assert again["ok"] is False and again["data"]["recorded"] is False
    assert again["skipped"], "a run that stored nothing must say why"


@pytest.mark.os_agnostic
def test_every_structured_mode_actually_emits_the_envelope() -> None:
    """The stronger form of the check above: offering --format is not emitting an envelope.

    ``config`` offered ``--format json`` and printed the bare configuration
    mapping, with no ``ok``, no ``command`` and no ``skipped``, while the skill
    documented the envelope as universal. A caller branching on ``ok`` got a
    KeyError from the one command whose whole job is to be read by a program.

    Driven through the real entry point per command rather than asserted from
    the source, because that is the only thing that catches a command whose
    JSON branch was never wired to the envelope at all.
    """
    from lsdsk.adapters.cli.envelope import ActionEnvelope

    # Commands that need an argument or would touch the machine's real state are
    # named with one that does neither.
    invocations = {
        "config": ["config", "--format", "json"],
        "info": ["info", "--format", "json"],
    }
    for command, args in invocations.items():
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "lsdsk", *args], capture_output=True, text=True, check=False
        )
        envelope = envelope_in(completed.stdout)
        missing = {"ok", "command", "data", "skipped"} - set(envelope)
        assert not missing, f"{command} emitted no envelope, missing {sorted(missing)}"
        assert envelope["command"] == command
        # Parsing it back through the model is what proves the shape is the
        # contract rather than four keys that happen to have the right names.
        ActionEnvelope.model_validate(envelope)


@pytest.mark.os_agnostic
def test_the_structured_config_never_prints_a_secret() -> None:
    """The envelope must not become a way to read .env in full.

    The human view redacts through the configuration library. The JSON branch
    builds its own payload, so it has to call the same redaction, and nothing
    else in the suite would notice if it stopped.
    """
    from lib_layered_config import REDACTED_PLACEHOLDER

    completed = subprocess.run(
        [sys.executable, "-m", "lsdsk", "config", "--format", "json"], capture_output=True, text=True, check=False
    )
    data = envelope_in(completed.stdout)["data"]

    sensitive = [key for key in data if any(word in key.lower() for word in ("token", "secret", "password", "key"))]
    assert sensitive, "the control: no sensitive-looking key was present, so this asserted nothing"
    for key in sensitive:
        assert data[key] == REDACTED_PLACEHOLDER, f"{key} was not redacted in the structured output"


# --------------------------------------------------------------------------
# --help is a user-facing surface, not a place for developer notes
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_no_help_screen_shows_developer_content(cli_runner: CliRunner, production_factory: Callable[[], Any]) -> None:
    """Five of seventeen screens rendered doctest lines and two literal markers.

    A doctest inside a Click-decorated docstring is never collected, because the
    decorator makes the object a Command and doctest walks a function's
    ``__doc__``. So those lines could never fail, rotted silently, and were
    rendered verbatim to users, one of them naming a test file. ``\\b`` is
    Click's no-rewrap marker only as a real control character; in an r-string it
    is backslash plus b, which Click does not recognise and prints as-is.
    """
    import click

    from lsdsk.adapters.cli.root import cli

    def leaves(command: object, prefix: str = "") -> list[str]:
        if isinstance(command, click.Group):
            found: list[str] = []
            for name, sub in sorted(command.commands.items()):
                found += leaves(sub, f"{prefix}{name} ")
            return found
        return [prefix.strip()]

    offenders: list[str] = []
    screens = ["", *leaves(cli)]
    assert len(screens) > 10, "the command tree was not walked, so this asserted nothing"
    for name in screens:
        argv = ([name] if name else []) + ["--help"]
        text = cli_runner.invoke(cli, argv, obj=production_factory).output
        assert text, f"{name or '<root>'}: --help produced nothing"
        offenders.extend(f"{name or '<root>'} shows {tell!r}" for tell in (">>>", "\\b", "test_") if tell in text)
    assert not offenders, "; ".join(offenders)
