"""Two snapshot branches no other test reached: a replay that is not an object, and the in-place chmod."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.hw.snapshot import save
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Iterator

    from click.testing import CliRunner

_CAPTURE: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("body", ["[1, 2, 3]", '"a string"', "42", "null"])
def test_a_replay_that_is_valid_json_but_not_an_object_is_refused_by_name(
    tmp_path: Path, cli_runner: CliRunner, body: str
) -> None:
    """``load`` parses it, then refuses anything that is not a JSON object before validating fields."""
    not_an_object = tmp_path / "array.json"
    not_an_object.write_text(body, encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["disks", "--replay", str(not_an_object)], obj=build_production)

    assert result.exit_code == ExitCode.CONFIG_ERROR, result.output
    said = " ".join(result.stderr.split())
    assert "does not contain a snapshot object" in said, said


class _PathThatCannotBeNarrowed(type(Path())):
    """A real path whose ``chmod`` is refused, as it is for a file somebody else owns."""

    def chmod(self, mode: int, *, follow_symlinks: bool = True) -> None:
        message = "not the owner"
        raise PermissionError(message)


@pytest.fixture
def directory_taking_no_new_files(tmp_path: Path) -> Iterator[Path]:
    """A directory whose existing files are writable but which cannot hold a temporary file."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root creates files in a read-only directory, so the atomic write never falls back")
    directory = tmp_path / "closed"
    directory.mkdir()
    yield directory
    directory.chmod(stat.S_IRWXU)


@pytest.mark.os_posix
def test_the_in_place_write_narrows_a_file_it_made_to_owner_only(directory_taking_no_new_files: Path) -> None:
    """Falling back to a write in place still leaves the capture 0600, not at the ambient mode."""
    target = directory_taking_no_new_files / "capture.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o666)
    directory_taking_no_new_files.chmod(stat.S_IRUSR | stat.S_IXUSR)

    save(_CAPTURE, target)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert '"hostname": "box"' in target.read_text(encoding="utf-8")


@pytest.mark.os_posix
def test_a_refused_narrowing_does_not_fail_a_capture_that_was_written(directory_taking_no_new_files: Path) -> None:
    """The chmod is best effort: the capture is on disk, so its refusal must not raise."""
    target = _PathThatCannotBeNarrowed(directory_taking_no_new_files / "capture.json")
    target.write_text("old", encoding="utf-8")
    directory_taking_no_new_files.chmod(stat.S_IRUSR | stat.S_IXUSR)

    save(_CAPTURE, target)

    assert '"hostname": "box"' in target.read_text(encoding="utf-8")
