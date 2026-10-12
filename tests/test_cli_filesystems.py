"""The ``filesystems`` command, its JSON list and the refusals its envelope names."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lsdsk.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
FIXTURE = FIXTURES / "linux-nvme-board.json"


def _refusing_capture(tmp_path: Path) -> Path:
    """A capture whose one mount, /data, refused its statvfs reading."""
    reading: dict[str, Any] = json.loads((FIXTURES / "linux-minimal.json").read_text(encoding="utf-8"))
    reading["block"]["sda"]["dev"] = "8:0"
    reading["block"]["sda"]["partitions"] = {"sda1": {"dev": "8:1"}}
    reading["mounts"] = [{"dev": "8:1", "mountpoint": "/data", "fstype": "ext4"}]
    reading["swaps"] = []
    reading["stacked"] = {}
    reading["signatures"] = {}
    reading["filesystem_space"] = {"8:1": {"error": "Permission denied"}}
    path = tmp_path / "refusing.json"
    path.write_text(json.dumps(reading), encoding="utf-8")
    return path


def test_the_command_prints_its_section(cli_runner: CliRunner, production_factory: Callable[[], Any]) -> None:
    result = cli_runner.invoke(cli_mod.cli, ["filesystems", "--replay", str(FIXTURE)], obj=production_factory)

    assert result.exit_code in (0, 1), result.output
    assert "Filesystems on" in result.output


def test_the_json_envelope_names_the_command_and_carries_the_list(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    result = cli_runner.invoke(
        cli_mod.cli, ["filesystems", "--replay", str(FIXTURE), "--format", "json"], obj=production_factory
    )

    envelope = json.loads(result.output)
    assert envelope["command"] == "filesystems"
    assert "filesystems" in envelope["data"]


def test_a_refused_row_is_named_in_skipped_with_its_subject_and_reason(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    result = cli_runner.invoke(
        cli_mod.cli,
        ["filesystems", "--replay", str(_refusing_capture(tmp_path)), "--format", "json"],
        obj=production_factory,
    )

    envelope = json.loads(result.output)
    assert "statvfs: /data - Permission denied" in envelope["skipped"]
    assert envelope["ok"] is False
    assert [one["name"] for one in envelope["data"]["filesystems"]] == ["/data"]


def _envelope(cli_runner: CliRunner, production_factory: Callable[[], Any], capture: Path) -> dict[str, Any]:
    result = cli_runner.invoke(
        cli_mod.cli, ["filesystems", "--replay", str(capture), "--format", "json"], obj=production_factory
    )
    envelope: dict[str, Any] = json.loads(result.output)
    return envelope


def _without_space_reading(tmp_path: Path) -> Path:
    """The ZFS fixture as lsdsk 1.7.x wrote it: mounts and pool members, no space or pool listing."""
    reading: dict[str, Any] = json.loads((FIXTURES / "linux-zfs-filesystems.json").read_text(encoding="utf-8"))
    for key in ("filesystem_space", "zfs_pools", "zfs_pools_error"):
        reading.pop(key, None)
    path = tmp_path / "before-space.json"
    path.write_text(json.dumps(reading), encoding="utf-8")
    return path


def test_a_linux_capture_taken_before_space_was_read_replays_as_not_read(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    capture = _without_space_reading(tmp_path)
    # Control: the capture still carries what the false refusal keyed on.
    reading = json.loads(capture.read_text(encoding="utf-8"))
    assert reading["mounts"]
    assert any(one.get("fs_type") == "zfs_member" for one in reading["signatures"].values())

    envelope = _envelope(cli_runner, production_factory, capture)

    assert envelope["data"]["filesystems"] is None
    # What the capture replayed as before filesystems existed: nothing skipped.
    assert envelope["skipped"] == []
    assert envelope["ok"] is True


def test_a_windows_capture_taken_before_space_was_read_replays_as_not_read(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    capture = FIXTURES / "windows-chipset-switch.json"
    # Control: the capture has volumes with paths, so only the missing space fields decide.
    volumes = json.loads(capture.read_text(encoding="utf-8"))["volumes"]
    assert any(one["paths"] for one in volumes.values())

    envelope = _envelope(cli_runner, production_factory, capture)

    assert envelope["data"]["filesystems"] is None
    assert envelope["skipped"] == []
    assert envelope["ok"] is True


def _refused_pool_listing(tmp_path: Path) -> Path:
    """The ZFS fixture with its pool listing refused: pool members on its disks, no pools."""
    reading: dict[str, Any] = json.loads((FIXTURES / "linux-zfs-filesystems.json").read_text(encoding="utf-8"))
    reading["zfs_pools"] = None
    reading["zfs_pools_error"] = "Permission denied"
    path = tmp_path / "refused-pools.json"
    path.write_text(json.dumps(reading), encoding="utf-8")
    return path


def test_a_refused_pool_listing_is_named_in_skipped_and_makes_ok_false(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    # Control: the capture as committed lists its pools, so nothing is skipped
    # and the line below can only come from the refusal.
    listed = _envelope(cli_runner, production_factory, FIXTURES / "linux-zfs-filesystems.json")
    assert listed["skipped"] == []
    assert listed["ok"] is True

    envelope = _envelope(cli_runner, production_factory, _refused_pool_listing(tmp_path))

    assert envelope["skipped"] == ["zfs-pools: filesystems - Permission denied"]
    assert envelope["ok"] is False
    # The pool rows stay, unread, rather than vanishing with the listing.
    pools = [one for one in envelope["data"]["filesystems"] if one["kind"] == "pool"]
    assert pools
    assert all(one["size_bytes"] is None for one in pools)
