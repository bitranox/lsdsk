"""A live Windows run and the replay of its own snapshot name and order volumes alike.

The reader keys volumes by their place in the enumeration (``"0"``, ``"1"``,
...) and a live run holds them in that order, while a snapshot is written with
sorted keys, which puts ``"10"`` before ``"2"``. From the eleventh volume on,
anything that walked the mapping in its stored order - a refused volume's
name, the filesystem rows, the drive letters in ``used by`` - came out
different on the replay than on the run it was taken from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-chipset-switch.json"
#: The windows-chipset-switch disk every planted volume sits on.
_DISK_NUMBER, _NODE = 0, "PhysicalDrive0"
#: How many volumes the capture carries; the first ordinal sorting out of place as text is the eleventh.
_VOLUMES = 12
#: The one volume whose paths were refused, so it is named by its place rather than a letter.
_REFUSED = 10


def _volume(index: int) -> dict[str, Any]:
    if index == _REFUSED:
        return {"paths": [], "paths_error": "could not read the volume's paths (error 5)", "disks": [_DISK_NUMBER]}
    gib = 2**30
    return {
        "paths": [f"{chr(ord('D') + index)}:\\"],
        "disks": [_DISK_NUMBER],
        "drive_type": 3,
        "esp": False,
        "total_bytes": 100 * gib,
        "free_bytes": 40 * gib,
        "available_bytes": 40 * gib,
        "fs_type": "NTFS",
        "read_only": False,
    }


def _capture() -> dict[str, Any]:
    capture = cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))
    capture["volumes"] = {str(index): _volume(index) for index in range(_VOLUMES)}
    capture["windows_volume"] = None
    capture["volume_space_read"] = True
    return capture


def _envelope(path: Path, command: str, runner: CliRunner, factory: Callable[[], object]) -> dict[str, Any]:
    from lsdsk.adapters.cli import cli

    output = runner.invoke(cli, [command, "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)


def _what_a_reader_sees(path: Path, runner: CliRunner, factory: Callable[[], object]) -> dict[str, Any]:
    filesystems = _envelope(path, "filesystems", runner, factory)
    disks = _envelope(path, "disks", runner, factory)
    disk = next(disk for disk in disks["data"]["disks"] if disk["node"] == _NODE)
    return {
        "skipped": filesystems["skipped"],
        "rows": [row["name"] for row in filesystems["data"]["filesystems"]],
        "used_by": [use["mounts"] for use in disk["usage"]["uses"]],
    }


@pytest.mark.parametrize("command", ["filesystems", "disks"])
def test_a_snapshot_with_sorted_keys_replays_as_the_live_run_it_was_taken_from(
    command: str, tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    capture = _capture()
    live = tmp_path / "live.json"
    live.write_text(json.dumps(capture), encoding="utf-8")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(capture, indent=2, sort_keys=True), encoding="utf-8")

    assert list(json.loads(snapshot.read_text(encoding="utf-8"))["volumes"])[:3] == ["0", "1", "10"], (
        "the precondition: the snapshot stores the ordinals in text order"
    )
    assert (
        _envelope(live, command, cli_runner, production_factory)["data"]
        == _envelope(snapshot, command, cli_runner, production_factory)["data"]
    )


def test_volumes_are_named_and_ordered_by_their_place_in_the_enumeration(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Both stored orders give the enumeration's own: the refused volume is the eleventh, letters run D to O."""
    capture = _capture()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(capture, sort_keys=True), encoding="utf-8")
    letters = [f"{chr(ord('D') + index)}:\\" for index in range(_VOLUMES) if index != _REFUSED]

    seen = _what_a_reader_sees(snapshot, cli_runner, production_factory)

    assert any(f"- volume {_REFUSED} on {_NODE}: " in line for line in seen["skipped"]), seen["skipped"]
    assert seen["rows"] == letters
    assert seen["used_by"] == [letters]
