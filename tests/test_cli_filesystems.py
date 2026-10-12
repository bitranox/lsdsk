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
