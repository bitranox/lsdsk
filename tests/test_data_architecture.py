"""Tests for the typed boundaries: the replay envelope in, the JSON envelope out.

Both are contracts with something outside this program, so both are pinned here
by their exact wire form rather than by the types that happen to produce it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw.snapshot import CaptureEnvelope, load
from lsdsk.domain.enums import CliCommand, Platform
from lsdsk.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-nvme-board.json"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("member", "wire"),
    [
        (Platform.LINUX, "linux"),
        (Platform.WINDOWS, "win32"),
        (CliCommand.TOPOLOGY, "topology"),
        (CliCommand.SLOTS, "slots"),
    ],
)
def test_enum_members_keep_their_wire_form(member: str, wire: str) -> None:
    """Verify interpolation yields the value, not the member name.

    These cross a boundary as strings: a capture records its platform and a
    consumer reads the envelope's command. A member that formats as
    ``Platform.LINUX`` would break both, and would do it on some interpreter
    versions and not others.
    """
    assert f"{member}" == wire
    assert member == wire


@pytest.mark.os_agnostic
def test_a_malformed_snapshot_is_rejected_at_the_boundary(tmp_path: Path) -> None:
    """Verify replay input is validated where it enters, not deep in a builder.

    ``--replay`` takes a file from any machine. Without validation here a bad
    file surfaces as a KeyError inside a platform builder, which reads as a bug
    in lsdsk rather than as a bad input file.
    """
    unknown_platform = tmp_path / "unknown.json"
    unknown_platform.write_text(json.dumps({"schema": 1, "platform": "solaris"}), encoding="utf-8")
    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text(json.dumps({"schema": 1, "platform": "linux", "hostname": []}), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not a snapshot lsdsk understands"):
        load(unknown_platform)
    with pytest.raises(ConfigurationError, match="not a snapshot lsdsk understands"):
        load(wrong_type)


@pytest.mark.os_agnostic
def test_a_real_snapshot_still_loads() -> None:
    """Verify the new validation does not reject what the reader writes."""
    assert load(FIXTURE).hostname == "linux-nvme-board"


@pytest.mark.os_agnostic
def test_the_envelope_accepts_keys_it_does_not_model() -> None:
    """Verify the outer model validates without freezing the capture's contents.

    Only the envelope's own keys are a schema. Everything below is keyed by data
    the foreign machine chose, so an unmodelled key must pass through rather than
    fail a snapshot that a newer reader wrote.
    """
    envelope = CaptureEnvelope.model_validate(
        {
            "schema": 1,
            "platform": "linux",
            "hostname": "example",
            "kernel": "6.1.0",
            "pci": {},
            "something_new": 42,
        }
    )

    assert envelope.platform is Platform.LINUX
    assert envelope.schema_version == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "command",
    # Taken from the enum rather than listed by hand: a hand-written list means a
    # new command is simply not covered, and nothing says so.
    [member.value for member in CliCommand],
)
def test_each_command_names_itself_in_the_envelope(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    command: str,
) -> None:
    """Verify the envelope says which command produced it.

    The builder is shared, so a literal in it made every command claim to be
    ``scan``: a consumer scripting against the output could not tell one from
    another.
    """
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(cli, [command, "--replay", str(FIXTURE), "--format", "json"], obj=production_factory)
    payload = json.loads(result.stdout)

    assert payload["command"] == command
    assert payload["ok"] is True


@pytest.mark.os_agnostic
def test_the_envelope_serialises_enums_as_their_values(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify no enum reaches the wire as its repr.

    A consumer matches on ``"warning"``, never on ``"Severity.WARNING"``, and the
    difference is invisible until something downstream fails to match.
    """
    from lsdsk.adapters.cli import cli

    result = cli_runner.invoke(cli, ["topology", "--replay", str(FIXTURE), "--format", "json"], obj=production_factory)
    text = result.stdout

    for enum_name in ("ControllerKind.", "Severity.", "BusType.", "DiskKind.", "Environment.", "Platform."):
        assert enum_name not in text, f"{enum_name} leaked into the envelope as a repr"
    payload = json.loads(text)
    assert payload["data"]["controllers"][0]["kind"] == "ahci"


@pytest.mark.os_agnostic
def test_every_tui_page_has_a_command_of_the_same_name() -> None:
    """Verify the two surfaces stay one vocabulary.

    A page called one thing and the command for it called another makes the
    reader translate between them, and nothing but this check stops the two
    drifting: each is registered in its own place.
    """
    from lsdsk.adapters.cli import cli
    from lsdsk.adapters.tui.app import LsdskApp

    commands = set(cli.commands)

    missing = [page for page in LsdskApp.PAGES if page not in commands]

    assert not missing, f"TUI pages with no command of the same name: {missing}"


@pytest.mark.os_agnostic
def test_every_page_name_is_a_command_enum_member() -> None:
    """Verify the envelope can name every view that produces one."""
    from lsdsk.adapters.tui.app import LsdskApp

    named = {member.value for member in CliCommand}

    missing = [page for page in LsdskApp.PAGES if page not in named]

    assert not missing, f"pages absent from CliCommand: {missing}"


@pytest.mark.os_agnostic
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows chmod only toggles read-only")
def test_a_snapshot_is_written_owner_only(tmp_path: Path) -> None:
    """Verify a capture is not left world-readable.

    It names the machine, its kernel and every drive's serial number, and the run
    that produces the most complete one is a privileged run. At the ambient umask
    that file lands group- and world-readable.
    """
    from lsdsk.adapters.hw.snapshot import SNAPSHOT_FILE_MODE, save

    target = tmp_path / "capture.json"
    save({"schema": 1, "platform": "linux", "hostname": "example"}, target)

    assert target.stat().st_mode & 0o777 == SNAPSHOT_FILE_MODE
    assert not target.stat().st_mode & 0o077, "no group or world access"


@pytest.mark.os_agnostic
def test_examples_in_click_docstrings_name_commands_that_exist() -> None:
    """Verify an unrunnable example still tells the truth.

    A doctest inside a Click-decorated docstring is never collected: the
    docstring belongs to the Command object, not to a function, so pytest's
    scanner never reaches it. Six of them exist and cannot fail, which is exactly
    how one came to invoke a command that had been deleted while the suite stayed
    green. Nothing else checks them, so this does.
    """
    import ast
    import pathlib
    import re

    from lsdsk.adapters.cli import cli

    known = set(cli.commands)
    offenders: list[str] = []
    for path in pathlib.Path("src/lsdsk").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            doc = ast.get_docstring(node) or ""
            invoked = re.findall(r'runner\.invoke\(\s*cli\s*,\s*\[\s*"([a-z-]+)"', doc)
            offenders.extend(f"{path}::{node.name} invokes {name!r}" for name in invoked if name not in known)

    assert not offenders, f"docstring examples naming commands that do not exist: {offenders}"
