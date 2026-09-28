"""``snapshot -o -`` writes the capture to standard output, as every Unix tool reads a dash.

Before this, ``-o -`` exited 0 saying ``Wrote -`` and left a file literally named
``-`` in the working directory, holding every drive's serial number, at a spot
nobody would think to look. A dash is the spelling a reader types when they
want the capture on a pipe - ``ssh host lsdsk snapshot -o - > capture.json`` -
so it now means exactly that. A file that really is called ``-`` is still one
``./-`` away.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.hw import snapshot as snapshot_adapter

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json"


def _read_a_committed_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand a committed capture in for the hardware read, and return it.

    ``read_current_machine`` reads sysfs, ioctls or SetupAPI, which is the true
    external edge of this command. The capture is the production reader's own
    output, so everything after the read - validation and the write these tests
    are about - runs for real.
    """
    capture = cast("dict[str, Any]", json.loads(CAPTURE.read_text(encoding="utf-8")))
    monkeypatch.setattr(snapshot_adapter, "read_current_machine", lambda: capture)
    return capture


@pytest.mark.os_agnostic
def test_a_dash_writes_the_capture_to_standard_output_and_no_file(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdout carries the capture itself, and nothing named ``-`` appears."""
    capture = _read_a_committed_capture(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(cli, ["snapshot", "-o", "-"], obj=production_factory)

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "-").exists(), "a dash still wrote a file literally named '-'"
    assert json.loads(result.stdout) == capture, "stdout is not the capture that was read"
    notice = result.stderr.lower()
    assert "serial number" in notice, result.stderr
    assert "hostname" in notice, result.stderr


@pytest.mark.os_agnostic
def test_a_capture_taken_to_standard_output_replays(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What reaches stdout is a snapshot ``load`` opens, not merely some JSON.

    This is the whole point of the dash: ``ssh host lsdsk snapshot -o - >
    capture.json`` must give a file ``--replay`` takes, with no extra line
    ahead of the document or after it.
    """
    _read_a_committed_capture(monkeypatch)
    monkeypatch.chdir(tmp_path)
    taken = cli_runner.invoke(cli, ["snapshot", "-o", "-"], obj=production_factory)
    assert taken.exit_code == 0, taken.output
    redirected = tmp_path / "capture.json"
    redirected.write_text(taken.stdout, encoding="utf-8")

    replayed = cli_runner.invoke(cli, ["--replay", str(redirected), "--no-record", "disks"], obj=production_factory)
    original = cli_runner.invoke(cli, ["--replay", str(CAPTURE), "--no-record", "disks"], obj=production_factory)

    assert "linux-minimal" in original.stdout, "the control rendered nothing to compare against"
    assert (replayed.exit_code, replayed.stdout) == (original.exit_code, original.stdout), replayed.output


@pytest.mark.os_agnostic
def test_a_dash_with_a_structured_result_is_refused(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capture and the JSON envelope cannot both be stdout.

    Writing both would hand a parser two documents on one stream; dropping
    either would be a silent change of what was asked for. So the pair is
    refused as a bad argument before anything is read.
    """
    _read_a_committed_capture(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(cli, ["snapshot", "-o", "-", "--format", "json"], obj=production_factory)

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert not (tmp_path / "-").exists(), "the refused run still wrote a file named '-'"
    assert "hostname" not in result.stdout, "the capture reached stdout although the run was refused"
    assert "standard output" in result.output, f"the refusal does not say what clashed: {result.output!r}"


@pytest.mark.os_agnostic
def test_a_file_really_named_dash_is_still_reachable(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: only the bare dash means stdout, so ``./-`` is a path.

    Without it, the fix could have taken the whole spelling away - pathlib
    folds ``./-`` into ``-``, so a check made on the converted path cannot
    tell the two apart.
    """
    capture = _read_a_committed_capture(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(cli, ["snapshot", "-o", "./-"], obj=production_factory)

    assert result.exit_code == 0, result.output
    written = tmp_path / "-"
    assert written.is_file(), "an explicit ./- did not write the file it names"
    assert json.loads(written.read_text(encoding="utf-8")) == capture
    assert "hostname" not in result.stdout, "an explicit path also sent the capture to stdout"


@pytest.mark.os_agnostic
def test_a_reading_load_would_refuse_never_reaches_standard_output(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdout gets the same validation a file does.

    A pipe has no temporary file to throw away, so a reading written before it
    was checked would already be in the reader's hands when the check failed.
    """
    malformed: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "6.1", "pci": "x"}
    monkeypatch.setattr(snapshot_adapter, "read_current_machine", lambda: malformed)

    result = cli_runner.invoke(cli, ["snapshot", "-o", "-"], obj=production_factory)

    assert result.exit_code == ExitCode.CONFIG_ERROR, result.output
    assert '"hostname"' not in result.stdout, "a reading load would refuse was written to stdout"


@pytest.mark.os_agnostic
def test_a_file_named_dash_is_reported_by_the_spelling_that_reaches_it(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-o ./-`` must not be reported as ``-``, which is now the spelling of stdout.

    pathlib folds ``./-`` into ``-``, so a report built from the converted path
    said ``Wrote -`` and put ``"path": "-"`` in the envelope: fed back to
    ``--replay`` or to any tool that reads a dash as stdin, it names the wrong
    thing. The report carries the argument as it was typed.
    """
    _read_a_committed_capture(monkeypatch)
    monkeypatch.chdir(tmp_path)

    human = cli_runner.invoke(cli, ["snapshot", "-o", "./-"], obj=production_factory)
    structured = cli_runner.invoke(cli, ["snapshot", "-o", "./-", "--format", "json"], obj=production_factory)

    assert human.exit_code == 0, human.output
    assert human.stdout == "Wrote ./-\n", human.stdout
    assert "Note: ./- holds" in human.stderr, human.stderr
    assert structured.exit_code == 0, structured.output
    reported = cast("dict[str, Any]", json.loads(structured.stdout))["data"]["path"]
    assert reported == "./-", f"the envelope reports {reported!r}"

    replayed = cli_runner.invoke(cli, ["--replay", reported, "--no-record", "disks"], obj=production_factory)
    original = cli_runner.invoke(cli, ["--replay", str(CAPTURE), "--no-record", "disks"], obj=production_factory)
    assert "linux-minimal" in original.stdout, "the control rendered nothing to compare against"
    assert (replayed.exit_code, replayed.stdout) == (original.exit_code, original.stdout), replayed.output


@pytest.mark.os_agnostic
def test_an_ordinary_path_is_still_reported_as_it_was_given(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: the report of a path with nothing to fold stays that path."""
    _read_a_committed_capture(monkeypatch)
    target = tmp_path / "capture.json"

    result = cli_runner.invoke(cli, ["snapshot", "-o", str(target), "--format", "json"], obj=production_factory)

    assert result.exit_code == 0, result.output
    assert cast("dict[str, Any]", json.loads(result.stdout))["data"]["path"] == str(target)


class _LogStream(NamedTuple):
    """How a run chose where its log lines go: ``--set`` overrides and environment variables."""

    overrides: list[str]
    environment: dict[str, str]


def _choose_the_log_stream(monkeypatch: pytest.MonkeyPatch, chosen: _LogStream) -> list[str]:
    """Put `chosen` in place and return the command-line half of it."""
    monkeypatch.delenv("LOG_CONSOLE_STREAM", raising=False)
    for name, value in chosen.environment.items():
        monkeypatch.setenv(name, value)
    return [arg for override in chosen.overrides for arg in ("--set", override)]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("chosen", "named"),
    [
        pytest.param(
            _LogStream(["lib_log_rich.console_stream=stdout"], {}), "lib_log_rich.console_stream", id="config stdout"
        ),
        pytest.param(
            _LogStream(["lib_log_rich.console_stream=BOTH"], {}), "lib_log_rich.console_stream", id="config both"
        ),
        pytest.param(_LogStream([], {"LOG_CONSOLE_STREAM": "stdout"}), "LOG_CONSOLE_STREAM", id="environment stdout"),
    ],
)
def test_a_log_stream_on_standard_output_refuses_a_capture_there(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
    chosen: _LogStream,
    named: str,
) -> None:
    """Log lines and the capture cannot share stdout, for the same reason as ``--format json``.

    Measured before this: ``--set lib_log_rich.console_stream=stdout --set
    lib_log_rich.console_level=debug snapshot -o - > s.json`` exited 0 with a
    log line after the document, and ``--replay s.json`` then refused the file
    at 78 with "Extra data". Refused before the machine is read, naming the
    setting that sent the log there.
    """
    capture = _read_a_committed_capture(monkeypatch)
    reads: list[dict[str, Any]] = []

    def counted_read() -> dict[str, Any]:
        reads.append(capture)
        return capture

    monkeypatch.setattr(snapshot_adapter, "read_current_machine", counted_read)
    overrides = _choose_the_log_stream(monkeypatch, chosen)

    result = cli_runner.invoke(cli, [*overrides, "snapshot", "-o", "-"], obj=production_factory)

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert named in result.stderr, f"the refusal does not name the setting: {result.stderr!r}"
    assert "hostname" not in result.stdout, "the capture reached stdout although the run was refused"
    assert not reads, "the machine was read for a capture that was going to be refused"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("chosen", "output"),
    [
        pytest.param(_LogStream(["lib_log_rich.console_stream=stderr"], {}), "-", id="config stderr"),
        # The library reads the environment variable FIRST, so it decides even
        # against a configured stdout: a check reading the key alone would refuse
        # a run whose log never goes near stdout.
        pytest.param(
            _LogStream(["lib_log_rich.console_stream=stdout"], {"LOG_CONSOLE_STREAM": "stderr"}),
            "-",
            id="environment stderr outranks config stdout",
        ),
        pytest.param(
            _LogStream(["lib_log_rich.console_stream=stdout"], {}),
            "capture.json",
            id="log on stdout, capture to a file",
        ),
    ],
)
def test_a_log_stream_that_leaves_the_capture_alone_is_not_refused(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chosen: _LogStream,
    output: str,
) -> None:
    """The controls: only a log stream that reaches the capture's stdout is refused."""
    capture = _read_a_committed_capture(monkeypatch)
    monkeypatch.chdir(tmp_path)
    overrides = _choose_the_log_stream(monkeypatch, chosen)

    result = cli_runner.invoke(cli, [*overrides, "snapshot", "-o", output], obj=production_factory)

    assert result.exit_code == 0, result.output
    if output == "-":
        assert json.loads(result.stdout) == capture, "stdout is not the capture that was read"
