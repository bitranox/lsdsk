"""A [lib_log_rich] value the logging library refuses must not stop the diagnosis.

Logging is set up by the root group before any command runs, so a value the
library refused used to escape from there and end EVERY command: ``--set
lib_log_rich.console_level=WARN`` left 22 with a bare ``ValueError: Unknown log
level: 'WARN'`` and no envelope, and a wrong-typed value left pydantic's own
report with its URL. The rule the rest of the configuration follows - a value the
tool cannot use falls back and says so - applies here too: the shipped logging
setting is used instead, one warning names the key, and the exit code is the
findings'.

A ``--set`` value nested deep enough to exhaust the interpreter's stack is a
second door to the same outcome, and it is refused as the usage error it is, with
the depth the file and environment layers are already held to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lib_log_rich.runtime
import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.overrides import MAX_OVERRIDE_DEPTH

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from click.testing import CliRunner, Result

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


def _disks(cli_runner: CliRunner, factory: Callable[[], Any], *before: str) -> Result:
    """Run ``disks --format json`` over a committed capture, with `before` as root options."""
    return cli_runner.invoke(
        cli, [*before, "--replay", str(CAPTURE), "--no-record", "disks", "--format", "json"], obj=factory
    )


def _said(result: Result) -> str:
    """Standard error on one line, so a wrapped sentence can be searched."""
    return " ".join((result.stderr or "").split())


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> Iterator[None]:
    """Drop the cached configuration on both sides of every test here.

    The loader caches per process, so the environment test's refused level would
    otherwise reach the next test's control, which then warns for a value it was
    never given.
    """
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
def control(cli_runner: CliRunner, production_factory: Callable[[], Any]) -> Result:
    """The same command with nothing malformed, whose exit code the others must keep."""
    result = _disks(cli_runner, production_factory)
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.shutdown()
    assert "lib_log_rich" not in _said(result), f"the control warned about logging: {_said(result)}"
    return result


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("override", "reported"),
    [
        ("lib_log_rich.console_level=WARN", "lib_log_rich.console_level=WARN"),
        ("lib_log_rich.queue_maxsize=abc", "lib_log_rich.queue_maxsize=abc"),
        ('lib_log_rich.console_styles={"a":1}', "lib_log_rich.console_styles="),
        ("lib_log_rich.console_stream=sideways", "lib_log_rich.console_stream=sideways"),
    ],
)
def test_a_refused_logging_value_falls_back_and_the_diagnosis_still_runs(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    control: Result,
    override: str,
    reported: str,
) -> None:
    """The run ends where the control ends, with an envelope and one warning naming the key."""
    result = _disks(cli_runner, production_factory, "--set", override)

    said = _said(result)
    assert result.exit_code == control.exit_code, f"left {result.exit_code}: {said[-400:]}"
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "disks"
    assert f"Warning: ignoring {reported}" in said, said
    assert said.count("lib_log_rich") == 1, f"warned more than once, or named more than the key: {said}"
    assert "errors.pydantic.dev" not in said, f"the library's URL is not a message for a reader: {said}"
    assert "validation error for" not in said, said
    assert lib_log_rich.runtime.is_initialised(), "the fallback left the run with no logging at all"


@pytest.mark.os_agnostic
def test_the_fallback_names_the_shipped_value_in_force_instead(
    cli_runner: CliRunner, production_factory: Callable[[], Any], control: Result
) -> None:
    """The sentence ends with what judged the run instead, as every other fallback's does."""
    del control
    result = _disks(cli_runner, production_factory, "--set", "lib_log_rich.console_level=WARN")
    assert "Using INFO." in _said(result), _said(result)


@pytest.mark.os_agnostic
def test_a_refused_logging_value_from_the_environment_falls_back_too(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    control: Result,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment layer reaches the same place a ``--set`` does, and is answered the same way."""
    get_config.cache_clear()
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__CONSOLE_LEVEL", "NOPE")
    result = _disks(cli_runner, production_factory)

    said = _said(result)
    assert result.exit_code == control.exit_code, f"left {result.exit_code}: {said[-400:]}"
    json.loads(result.stdout)
    assert "Warning: ignoring lib_log_rich.console_level=NOPE" in said, said


@pytest.mark.os_agnostic
def test_a_variable_the_logging_library_reads_itself_is_set_aside_rather_than_ending_the_run(
    cli_runner: CliRunner, production_factory: Callable[[], Any], control: Result, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LOG_CONSOLE_LEVEL`` outranks every setting, the shipped ones included.

    So no fallback inside this tool's configuration can repair it: the variable
    is set aside for the start and named, and the variable is back afterwards.
    """
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "NOPE")
    monkeypatch.setenv("LOG_UNRELATED_SETTING", "kept")
    result = _disks(cli_runner, production_factory)

    said = _said(result)
    assert result.exit_code == control.exit_code, f"left {result.exit_code}: {said[-400:]}"
    json.loads(result.stdout)
    assert "Warning: ignoring LOG_CONSOLE_LEVEL=NOPE" in said, said
    assert "LOG_UNRELATED_SETTING" not in said, f"named a variable the refusal is not about: {said}"
    assert os.environ.get("LOG_CONSOLE_LEVEL") == "NOPE", "the variable was not put back"
    assert lib_log_rich.runtime.is_initialised()


def _nested(depth: int) -> str:
    """A JSON object nested `depth` levels deep."""
    return '{"a":' * depth + "1" + "}" * depth


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["lib_log_rich.console_styles", "bogus.key", "display.wwn_width"])
def test_a_set_value_nested_past_the_limit_is_a_usage_error(
    cli_runner: CliRunner, production_factory: Callable[[], Any], key: str
) -> None:
    """900 levels exhausted the stack in the merge, whichever section it named, and left 70."""
    result = _disks(cli_runner, production_factory, "--set", f"{key}={_nested(900)}")

    said = _said(result)
    assert result.exit_code == ExitCode.USAGE_ERROR, f"left {result.exit_code}: {said[-400:]}"
    assert "RecursionError" not in said, said
    assert "nest" in said, f"the refusal does not say what is wrong: {said}"
    assert key in said, f"the refusal does not name the key: {said}"
    assert len(said) < 400, f"the refusal repeats the value it refused: {len(said)} characters"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("depth", "refused"), [(20, False), (MAX_OVERRIDE_DEPTH - 2, False), (MAX_OVERRIDE_DEPTH - 1, True)]
)
def test_the_limit_counts_the_section_and_key_as_the_library_does(
    cli_runner: CliRunner, production_factory: Callable[[], Any], control: Result, depth: int, refused: bool
) -> None:
    """``bogus.key`` is two levels, so the value may hold the rest and no more.

    ``config`` is the command that walks the whole merged configuration again to
    redact and print it, so a value accepted here must survive that walk too.
    """
    del control
    result = cli_runner.invoke(
        cli, ["--set", f"bogus.key={_nested(depth)}", "config", "--format", "json"], obj=production_factory
    )
    said = _said(result)
    if refused:
        assert result.exit_code == ExitCode.USAGE_ERROR, f"left {result.exit_code}: {said[-400:]}"
    else:
        assert result.exit_code == 0, f"left {result.exit_code}: {said[-400:]}"
        json.loads(result.stdout)
