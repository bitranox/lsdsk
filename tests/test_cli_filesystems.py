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


def test_the_zfs_capture_replays_each_pool_s_fragmentation(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    envelope = _envelope(cli_runner, production_factory, FIXTURES / "linux-zfs-filesystems.json")
    pools = {row["name"]: row["free_space"] for row in envelope["data"]["filesystems"] if row["kind"] == "pool"}
    assert set(pools) == {"rpool", "tank"}
    for space in pools.values():
        assert set(space) == {
            "fragmentation_percent",
            "free_extent_count",
            "free_extent_bytes",
            "largest_free_extent_bytes",
            "histogram",
        }
        assert 0 <= space["fragmentation_percent"] <= 100
        assert (space["free_extent_count"], space["histogram"]) == (None, [])
    assert envelope["skipped"] == []


def test_a_capture_taken_before_fragmentation_was_read_replays_with_no_free_space_and_no_refusal(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    reading = json.loads((FIXTURES / "linux-zfs-filesystems.json").read_text(encoding="utf-8"))
    assert "zfs_fragmentation" in reading  # control: the committed capture carries what is removed
    for key in ("zfs_fragmentation", "zfs_fragmentation_error", "free_extents"):
        reading.pop(key, None)
    path = tmp_path / "lsdsk-1.8.0.json"
    path.write_text(json.dumps(reading), encoding="utf-8")
    envelope = _envelope(cli_runner, production_factory, path)
    assert envelope["data"]["filesystems"]
    assert all(row["free_space"] is None for row in envelope["data"]["filesystems"])
    assert (envelope["skipped"], envelope["ok"]) == ([], True)


def _zfs_fixture_with(tmp_path: Path, name: str, change: Callable[[dict[str, Any]], None]) -> Path:
    reading: dict[str, Any] = json.loads((FIXTURES / "linux-zfs-filesystems.json").read_text(encoding="utf-8"))
    change(reading)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(reading), encoding="utf-8")
    return path


def _tank_behind_luks(reading: dict[str, Any]) -> None:
    """Put tank's one member behind a dm-crypt mapping, as a ZFS-on-LUKS host records it."""
    member = reading["signatures"].pop("259:1")
    reading["signatures"]["259:1"] = {"fs_type": "crypto_LUKS"}
    reading["signatures"]["253:0"] = member
    reading["block"]["nvme0n1"]["partitions"]["nvme0n1p1"]["holders"] = ["dm-0"]
    reading["stacked"] = {"dm-0": {"dev": "253:0", "dm_name": "cryptank", "dm_uuid": "CRYPT-", "holders": []}}


def test_a_pool_on_dm_crypt_replays_as_its_pool_row(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    plain = _envelope(cli_runner, production_factory, FIXTURES / "linux-zfs-filesystems.json")
    luks = _envelope(cli_runner, production_factory, _zfs_fixture_with(tmp_path, "luks", _tank_behind_luks))

    def pools(envelope: dict[str, Any]) -> list[tuple[str, list[str], int | None]]:
        rows = envelope["data"]["filesystems"]
        return [(row["name"], row["disks"], row["used_bytes"]) for row in rows if row["kind"] == "pool"]

    # Control: the committed capture draws tank on nvme0n1.
    assert ("tank", ["nvme0n1"], 154454237184) in pools(plain)
    assert pools(luks) == pools(plain)
    assert (luks["skipped"], luks["ok"]) == ([], True)


def _ghost_pool(reading: dict[str, Any]) -> None:
    reading["zfs_pools"]["ghost"] = {"size": 1000, "alloc": 990}


def test_a_listed_pool_no_disk_reaches_is_named_in_skipped_and_makes_ok_false(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    envelope = _envelope(cli_runner, production_factory, _zfs_fixture_with(tmp_path, "ghost", _ghost_pool))

    assert envelope["skipped"] == [
        "zfs-pool-disks: filesystems - pool ghost: no member was traced to a disk this scan inventoried"
    ]
    assert envelope["ok"] is False
    assert "ghost" not in [row["name"] for row in envelope["data"]["filesystems"]]


def _filepool(reading: dict[str, Any]) -> None:
    """A pool on files beside the fixture's own two, as ``zpool create filepool /srv/pool.img`` leaves a host."""
    reading["zfs_pools"]["filepool"] = {"size": 10737418240, "alloc": 9663676416}
    reading["zfs_fragmentation"]["filepool"] = 5
    reading["mounts"].append({"dev": "0:77", "fstype": "zfs", "mountpoint": "/filepool", "source": "filepool"})


def _with_layouts(reading: dict[str, Any]) -> None:
    """The fixture's pools as the pool configs place them: rpool on the two SATA partitions, tank on the NVMe one."""
    reading["zfs_pool_layouts"] = {
        "rpool": {"guid": "1", "vdevs": [{"kind": "disk", "dev": "8:3"}, {"kind": "disk", "dev": "8:19"}]},
        "tank": {"guid": "2", "vdevs": [{"kind": "disk", "dev": "259:1"}]},
    }
    if "filepool" in reading["zfs_pools"]:
        reading["zfs_pool_layouts"]["filepool"] = {"guid": "3", "vdevs": [{"kind": "file"}]}


def test_a_pool_on_files_is_no_row_and_no_refusal(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    def filepool_with_layouts(reading: dict[str, Any]) -> None:
        _filepool(reading)
        _with_layouts(reading)

    plain = _envelope(cli_runner, production_factory, FIXTURES / "linux-zfs-filesystems.json")
    laid_out = _envelope(cli_runner, production_factory, _zfs_fixture_with(tmp_path, "layouts", _with_layouts))
    files = _envelope(cli_runner, production_factory, _zfs_fixture_with(tmp_path, "files", filepool_with_layouts))
    unplaced = _envelope(cli_runner, production_factory, _zfs_fixture_with(tmp_path, "unplaced", _filepool))

    # Controls: the committed capture, and the same capture with its pools' layouts, both read clean and agree.
    assert (plain["skipped"], plain["ok"]) == ([], True)
    assert laid_out["data"]["filesystems"] == plain["data"]["filesystems"]
    assert (laid_out["skipped"], laid_out["ok"]) == ([], True)
    # The pool's config says its one member is a file: there was no disk of its own to find.
    assert (files["skipped"], files["ok"]) == ([], True)
    assert files["data"]["filesystems"] == plain["data"]["filesystems"]
    # Opposite arm: recorded before the layouts were read, nothing says what the pool sits on, so it is refused.
    assert unplaced["skipped"] == [
        "zfs-pool-disks: filesystems - pool filepool: no member was traced to a disk this scan inventoried"
    ]


def _stale_label(reading: dict[str, Any]) -> None:
    """A reused partition still carrying the label of a pool this machine never imported, as ZFS leaves it."""
    reading["signatures"]["259:2"] = {"fs_type": "zfs_member", "fs_label": "oldpool"}


def _disk_uses(
    cli_runner: CliRunner, production_factory: Callable[[], Any], capture: Path, node: str
) -> list[tuple[str, str]]:
    result = cli_runner.invoke(
        cli_mod.cli, ["disks", "--replay", str(capture), "--format", "json"], obj=production_factory
    )
    disks = json.loads(result.output)["data"]["disks"]
    usage = next(disk["usage"] for disk in disks if disk["node"] == node)
    return [(use["kind"], use["name"]) for use in usage["uses"]]


def test_a_label_naming_a_pool_the_listing_does_not_hold_is_no_pool_row(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    plain = _envelope(cli_runner, production_factory, FIXTURES / "linux-zfs-filesystems.json")
    stale_capture = _zfs_fixture_with(tmp_path, "stale", _stale_label)
    stale = _envelope(cli_runner, production_factory, stale_capture)

    # The pool listing was read whole and holds no oldpool, so the label is not an imported pool and gets no row.
    assert "oldpool" not in [row["name"] for row in stale["data"]["filesystems"]]
    assert stale["data"]["filesystems"] == plain["data"]["filesystems"]
    assert (stale["skipped"], stale["ok"]) == ([], True)
    # used by still names what the partition carries: the label is a fact about the disk either way.
    assert ("zfs", "oldpool") in _disk_uses(cli_runner, production_factory, stale_capture, "nvme0n1")


def test_a_label_beside_a_listing_that_could_not_read_every_pool_keeps_its_unread_row(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    def stale_with_a_failed_pool(reading: dict[str, Any]) -> None:
        _stale_label(reading)
        reading["zfs_pools_error"] = "pool oldpool: Input/output error"

    envelope = _envelope(
        cli_runner, production_factory, _zfs_fixture_with(tmp_path, "failed", stale_with_a_failed_pool)
    )

    # A listing that names a pool it could not read leaves that pool absent from the map, so the label may be it.
    row = next(row for row in envelope["data"]["filesystems"] if row["name"] == "oldpool")
    assert (row["kind"], row["disks"], row["size_bytes"]) == ("pool", ["nvme0n1"], None)
    assert envelope["skipped"] == ["zfs-pools: filesystems - pool oldpool: Input/output error"]
