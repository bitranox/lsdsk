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
    from pathlib import Path

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
            [sys.executable, "-m", "lsdsk", command, "--help"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
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
            [sys.executable, "-m", "lsdsk", *args], capture_output=True, encoding="utf-8", errors="replace", check=False
        )
        envelope = envelope_in(completed.stdout)
        missing = {"ok", "command", "data", "skipped"} - set(envelope)
        assert not missing, f"{command} emitted no envelope, missing {sorted(missing)}"
        assert envelope["command"] == command
        # Parsing it back through the model is what proves the shape is the
        # contract rather than four keys that happen to have the right names.
        ActionEnvelope.model_validate(envelope)


@pytest.mark.os_agnostic
def test_the_structured_config_never_prints_a_secret(tmp_path: Path) -> None:
    """The envelope must not become a way to read .env in full.

    The human view redacts through the configuration library. The JSON branch
    builds its own payload, so it has to call the same redaction, and nothing
    else in the suite would notice if it stopped.

    The secret is PLANTED here rather than borrowed from whatever ``.env`` the
    machine happens to have. Configuration reads ``.env`` from the working
    directory, so this passed on a developer box carrying real credentials and
    failed on every CI runner, where no ``.env`` exists, the control fired, and
    the test correctly reported that it had proved nothing. Planting one also
    stops the suite reading the developer's own secrets to test redaction.
    """
    from lib_layered_config import REDACTED_PLACEHOLDER

    secret = "not-a-real-secret-planted-by-the-test"
    (tmp_path / ".env").write_text(f"DEMO_API_TOKEN={secret}\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "lsdsk", "config", "--format", "json"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=tmp_path,
    )
    data = envelope_in(completed.stdout)["data"]

    sensitive = [key for key in data if any(word in key.lower() for word in ("token", "secret", "password", "key"))]
    assert sensitive, "the control: no sensitive-looking key was present, so this asserted nothing"
    assert "demo_api_token" in sensitive, f"the planted key never reached the config: {sorted(data)}"
    for key in sensitive:
        assert data[key] == REDACTED_PLACEHOLDER, f"{key} was not redacted in the structured output"
    # The placeholder being present does not prove the value is gone: a payload
    # could carry both. Only its absence from the whole stream proves that.
    assert secret not in completed.stdout, "the secret's value survived somewhere in the output"


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


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("stdout_tty", "stdin_tty", "expect"),
    [
        pytest.param(True, True, "tui", id="somebody is sitting at it"),
        pytest.param(False, False, "report", id="a pipe gets the printed page"),
        # `lsdsk < /dev/null` at a terminal. Textual reads key events from
        # stdin, so a redirected stdin leaves a full-screen view nobody can
        # quit however interactive the output side looks.
        pytest.param(True, False, "report", id="output is a terminal but nobody can type"),
    ],
)
def test_the_default_view_follows_whether_anything_can_be_typed_at(
    monkeypatch: pytest.MonkeyPatch, stdout_tty: bool, stdin_tty: bool, expect: str
) -> None:
    """A bare ``lsdsk`` opens the TUI on a terminal and prints the page off one.

    The whole point of the switch is that a full-screen application cannot run
    into a pipe: it would draw nothing a script could read and would take the
    terminal's exit code with it. So `lsdsk | grep`, `lsdsk > file` and every CI
    log must keep getting the page.

    The terminal is patched rather than injected because it IS the external edge
    here - there is no seam to substitute, the question is literally what the
    operating system says about the file descriptor.
    """
    import contextlib
    import sys as _sys
    from types import SimpleNamespace

    from lsdsk.adapters.cli.commands import scan

    # The logging runtime is a real external edge and refuses to bind
    # without an init() the CLI entry point normally performs.
    def no_binding(*_args: object, **_kwargs: object) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    monkeypatch.setattr("lib_log_rich.runtime.bind", no_binding)

    from lsdsk.domain.history import History
    from lsdsk.domain.models import Finding, Inventory

    machine = Inventory("probe")
    read = SimpleNamespace(history=History(hostname="probe"), writable=False)
    called: list[str] = []

    def note_report(*_args: object, **_kwargs: object) -> None:
        called.append("report")

    def give_inventory(*_args: object, **_kwargs: object) -> tuple[Inventory, list[Finding]]:
        return machine, []

    def give_history(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return read

    class StubApp:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            called.append("tui")

        def run(self) -> None:
            """The real one blocks on the terminal; this test is about which ran."""

    monkeypatch.setattr(_sys.stdout, "isatty", lambda: stdout_tty, raising=False)
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: stdin_tty, raising=False)
    monkeypatch.setattr(scan, "run_default_report", note_report)
    monkeypatch.setattr("lsdsk.adapters.tui.LsdskApp", StubApp)
    # analyse and read_history are imported inside the function, so they are
    # attributes of their defining module rather than of scan.
    monkeypatch.setattr("lsdsk.adapters.cli.commands.history.analyse", give_inventory)
    monkeypatch.setattr("lsdsk.adapters.cli.commands.history.read_history", give_history)

    if expect == "tui":
        with pytest.raises(SystemExit):
            scan.run_default_view(None)
    else:
        scan.run_default_view(None)

    assert called == [expect], f"expected the {expect} path, got {called}"


def test_report_prints_the_page_even_when_both_ends_look_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--report has to win over the terminal test, or it is not an escape hatch.

    The terminal test cannot see the case it is wrong about: IPython's ``!``
    runs the child under pexpect, so a notebook cell presents a pseudo-terminal
    on both ends and is indistinguishable from a person at a shell. Both ends
    are therefore claimed to be terminals HERE, which is exactly the state in
    which the flag has to still print.
    """
    import sys as _sys

    from lsdsk.adapters.cli.commands import scan

    called: list[str] = []

    def note_report(*_args: object, **_kwargs: object) -> None:
        called.append("report")

    class RefuseToOpen:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("--report opened the interactive view")

    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(scan, "run_default_report", note_report)
    monkeypatch.setattr("lsdsk.adapters.tui.LsdskApp", RefuseToOpen)

    scan.run_default_view(None, force_report=True)

    assert called == ["report"]


def test_report_before_a_subcommand_is_refused_rather_than_ignored(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """A flag that does nothing where it was typed must say so.

    --report picks the view a bare ``lsdsk`` uses, so before a subcommand it has
    nothing to pick. Accepting it silently would tell a reader their choice
    landed when the subcommand's own output was never in question.
    """
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(cli, ["--report", "info"], obj=production_factory)

    assert result.exit_code != 0
    assert "--report" in result.output
    assert "subcommand" in result.output
