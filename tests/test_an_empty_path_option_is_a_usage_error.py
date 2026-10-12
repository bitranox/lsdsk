"""A path option given the empty string is refused at the parser, not acted on.

``Path("")`` is the current directory, so ``snapshot -o ""`` failed late with an
errno about ``.`` and exit 74, and ``config-generate-examples --destination ""``
exited 0 after writing into whatever directory it ran from (and, with ``--force``,
over files there). ``--replay ""`` was already a usage error. One shared path type
now refuses the empty string for every option that names a path, while
``--history-file ""`` keeps its documented meaning of "not given".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import rich_click as click

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.typed_click import NonEmptyPath
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Iterator

CAPTURE = str(Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json")

#: Every option that names a path the command acts on, as the command line spells it.
PATH_OPTIONS = [
    pytest.param(["snapshot", "-o", ""], "snapshot", id="snapshot -o"),
    pytest.param(["snapshot", "--output", ""], "snapshot", id="snapshot --output"),
    pytest.param(["config-generate-examples", "--destination", ""], "config-generate-examples", id="destination"),
    pytest.param(["--replay", "", "disks"], "lsdsk", id="root --replay"),
    pytest.param(["disks", "--replay", ""], "disks", id="disks --replay"),
    pytest.param(["--env-file", "", "disks", "--replay", CAPTURE], "lsdsk", id="--env-file"),
]


@pytest.fixture(autouse=True)
def quiet_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run in an empty directory, so anything a refusal let through shows up as a file in it."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    get_config.cache_clear()
    yield work
    get_config.cache_clear()


@pytest.mark.parametrize(("argv", "command"), PATH_OPTIONS)
def test_an_empty_path_is_a_usage_error_and_creates_nothing(
    argv: list[str], command: str, quiet_working_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2, and nothing is written into the directory the command ran from."""
    code = cli_mod.main(argv, services_factory=build_production)

    assert code == 2, capsys.readouterr().err
    assert list(quiet_working_directory.iterdir()) == []


@pytest.mark.parametrize(("argv", "command"), PATH_OPTIONS)
def test_an_empty_path_answers_in_the_json_usage_envelope_when_json_was_asked_for(
    argv: list[str], command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same refusal every other usage error gives a ``--format json`` caller."""
    code = cli_mod.main([*argv, "--format", "json"], services_factory=build_production)

    out = capsys.readouterr().out
    assert code == 2
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["command"] == command
    assert envelope["error"]["type"] == "USAGE_ERROR"


def test_a_forced_empty_destination_still_writes_nothing(
    quiet_working_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--force`` is what turned the empty destination from harmless into overwriting."""
    code = cli_mod.main(["config-generate-examples", "--destination", "", "--force"], services_factory=build_production)

    assert code == 2, capsys.readouterr().err
    assert list(quiet_working_directory.iterdir()) == []


def test_an_empty_history_file_still_means_not_given(
    quiet_working_directory: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: ``--history-file ""`` is "use the default", so one shared type must not refuse it."""
    monkeypatch.setenv("XDG_STATE_HOME", str(quiet_working_directory / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(quiet_working_directory / "state"))
    monkeypatch.setenv("HOME", str(quiet_working_directory))

    code = cli_mod.main(
        ["--history-file", "", "--no-record", "--replay", CAPTURE, "disks", "--format", "json"],
        services_factory=build_production,
    )

    assert code != 2, capsys.readouterr().err


def test_a_named_destination_is_still_accepted(
    quiet_working_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other control: refusing the empty string must not refuse a path that names something."""
    target = quiet_working_directory / "examples"

    code = cli_mod.main(["snapshot", "-o", str(target / "capture.json")], services_factory=build_production)

    assert code != 2, os.fspath(target) + capsys.readouterr().err


def _walk(command: click.Command) -> Iterator[click.Parameter]:
    yield from command.params
    if isinstance(command, click.Group):
        for child in command.commands.values():
            yield from _walk(child)


def test_every_path_option_in_the_command_tree_is_the_shared_non_empty_type() -> None:
    """A path option added later cannot forget the refusal, because the type is the refusal.

    Walked from the root group rather than from a list of options, so an option
    nobody wrote down is still found; the non-empty control keeps the walk from
    passing over an empty tree.
    """
    paths = [param for param in _walk(cli_mod.cli) if isinstance(param.type, click.Path)]

    assert len(paths) >= 6, [param.name for param in paths]
    assert [param.name for param in paths if not isinstance(param.type, NonEmptyPath)] == []
