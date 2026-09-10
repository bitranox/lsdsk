"""A capture carries drive identity and the machine's name, and says so when written.

``snapshot --help`` calls a capture a reproducible bug report, which is an
invitation to attach it somewhere public, and nothing at that point said what
the file holds. The notice goes to stderr so stdout stays exactly what a script
already parses: ``Wrote <path>`` in human mode, the envelope in JSON mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.hw import snapshot as snapshot_adapter

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json"


def _read_a_committed_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand a committed capture in for the hardware read.

    ``read_current_machine`` reads sysfs, ioctls or SetupAPI, which is the true
    external edge of this command: a CI runner has no drives worth reading and
    a macOS one cannot read hardware at all. The capture is the production
    reader's own output, so everything after the read runs for real.
    """
    capture: dict[str, Any] = json.loads(CAPTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(snapshot_adapter, "read_current_machine", lambda: capture)


@pytest.mark.os_agnostic
def test_a_capture_says_on_stderr_what_it_carries(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The human run keeps its stdout line and names the identity on stderr."""
    _read_a_committed_capture(monkeypatch)
    target = tmp_path / "capture.json"

    result = cli_runner.invoke(cli, ["snapshot", "-o", str(target)], obj=production_factory)

    assert result.exit_code == 0, result.output
    assert target.exists()
    assert result.stdout.splitlines()[-1] == f"Wrote {target}", result.stdout
    assert "serial number" not in result.stdout.lower(), "the notice leaked into stdout"
    notice = result.stderr.lower()
    assert "serial number" in notice, result.stderr
    assert "hostname" in notice, result.stderr


@pytest.mark.os_agnostic
def test_a_structured_capture_keeps_the_notice_out_of_the_envelope(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A program reading the envelope must still get only the envelope."""
    _read_a_committed_capture(monkeypatch)
    target = tmp_path / "capture.json"

    result = cli_runner.invoke(cli, ["snapshot", "-o", str(target), "--format", "json"], obj=production_factory)

    assert result.exit_code == 0, result.output
    start = result.stdout.index("{")
    decoded, _ = json.JSONDecoder().raw_decode(result.stdout[start:])
    envelope = cast("dict[str, Any]", decoded)
    assert envelope["ok"] is True
    assert "serial number" not in result.stdout.lower(), "the notice leaked into the envelope stream"
    assert "serial number" in result.stderr.lower(), result.stderr


@pytest.mark.os_agnostic
def test_snapshot_help_says_what_a_capture_carries(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """``--help`` is where somebody stands before attaching a capture to a report."""
    result = cli_runner.invoke(cli, ["snapshot", "--help"], obj=production_factory)

    # "serial number", not "serial": the help already says "re-serialising",
    # which made a bare substring check pass against text that says nothing.
    text = " ".join(result.output.split()).lower()
    assert "serial number" in text, result.output
    assert "hostname" in text, result.output
