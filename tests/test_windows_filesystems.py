"""The Windows volumes that live on an inventory disk become filesystem rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lsdsk.adapters.hw.windows.capture import WindowsCapture
from lsdsk.adapters.hw.windows.filesystems import build_filesystems
from lsdsk.adapters.hw.windows.usage import blocks_not_mounted, resolve_usage
from lsdsk.domain.enums import Environment, FilesystemKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

BARE_METAL = Environment.BARE_METAL
FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _disk(node: str) -> dict[str, Any]:
    return {"node": node}


def _volume(*, paths: tuple[str, ...] = (), disks: tuple[int, ...] = (), **extra: Any) -> dict[str, Any]:
    """A volume as the current reader records it: one with a path names its filesystem unless told it refused."""
    entry: dict[str, Any] = {"paths": list(paths), "disks": list(disks)}
    if paths and "fs_info_error" not in extra:
        entry["fs_type"] = "NTFS"
    entry.update(extra)
    return entry


def _capture(disks: dict[str, Any], **extra: Any) -> WindowsCapture:
    reading: dict[str, Any] = {
        "schema": 2,
        "platform": "win32",
        "hostname": "h",
        "kernel": "10.0",
        "pci": {},
        "disks": disks,
    }
    reading.update(extra)
    return WindowsCapture.model_validate(reading)


def _one_disk(volumes: dict[str, Any]) -> WindowsCapture:
    return _capture({"\\\\?\\p0": _disk("PhysicalDrive0")}, volumes=volumes)


def test_a_volume_on_an_inventory_disk_is_a_row_with_its_space() -> None:
    capture = _one_disk(
        {
            "0": _volume(
                paths=("C:\\", "C:\\mnt\\x\\"),
                disks=(0,),
                total_bytes=100,
                free_bytes=40,
                available_bytes=30,
                fs_type="NTFS",
                read_only=False,
            )
        }
    )
    rows, refused = build_filesystems(capture, BARE_METAL)
    assert refused == ()
    assert rows is not None
    (c,) = rows
    assert (c.kind, c.name, c.other_mounts, c.disks) == (
        FilesystemKind.VOLUME,
        "C:\\",
        ("C:\\mnt\\x\\",),
        ("PhysicalDrive0",),
    )
    assert (c.size_bytes, c.used_bytes, c.free_bytes, c.fs_type) == (100, 70, 30, "NTFS")
    assert (c.inodes_total, c.inodes_free, c.inodes_used_percent) == (None, None, None)


def test_a_volume_without_a_path_or_a_disk_is_not_a_row() -> None:
    capture = _one_disk({"0": _volume(paths=(), disks=(0,)), "1": _volume(paths=("R:\\",), disks=())})
    assert build_filesystems(capture, BARE_METAL)[0] == ()


def test_a_volume_on_a_disk_the_capture_does_not_list_is_not_a_row() -> None:
    capture = _one_disk({"0": _volume(paths=("E:\\",), disks=(7,))})
    assert build_filesystems(capture, BARE_METAL)[0] == ()


def test_a_spanned_volume_names_every_disk_in_order() -> None:
    capture = _capture(
        {"\\\\?\\p0": _disk("PhysicalDrive0"), "\\\\?\\p1": _disk("PhysicalDrive1")},
        volumes={"0": _volume(paths=("D:\\",), disks=(1, 0))},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert rows[0].disks == ("PhysicalDrive0", "PhysicalDrive1")


def test_a_refused_space_call_is_carried_on_its_row() -> None:
    capture = _one_disk({"0": _volume(paths=("D:\\",), disks=(0,), space_error="could not (error 21)")})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(r.reading, r.reason) for r in rows[0].readings_refused] == [("volume-space", "could not (error 21)")]
    assert (rows[0].size_bytes, rows[0].used_bytes, rows[0].free_bytes) == (None, None, None)


def test_a_refused_volume_information_call_is_carried_on_its_row() -> None:
    capture = _one_disk({"0": _volume(paths=("D:\\",), disks=(0,), fs_info_error="no info (error 5)")})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [(r.reading, r.reason) for r in rows[0].readings_refused] == [("volume-info", "no info (error 5)")]
    assert (rows[0].fs_type, rows[0].read_only) == ("", False)


def test_free_above_total_never_gives_a_negative_used_figure() -> None:
    capture = _one_disk(
        {"0": _volume(paths=("D:\\",), disks=(0,), total_bytes=100, free_bytes=150, available_bytes=150)}
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert rows[0].used_bytes == 0


def test_a_figure_that_was_not_read_stays_none_rather_than_zero() -> None:
    capture = _one_disk({"0": _volume(paths=("D:\\",), disks=(0,), total_bytes=100, free_bytes=40)})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert (rows[0].size_bytes, rows[0].used_bytes, rows[0].free_bytes) == (100, None, None)


def test_a_read_only_volume_is_read_only() -> None:
    capture = _one_disk({"0": _volume(paths=("D:\\",), disks=(0,), read_only=True)})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert rows[0].read_only is True


def test_rows_follow_the_disk_order_of_their_first_disk() -> None:
    capture = _capture(
        {"\\\\?\\p0": _disk("PhysicalDrive0"), "\\\\?\\p1": _disk("PhysicalDrive1")},
        volumes={"0": _volume(paths=("E:\\",), disks=(1,)), "1": _volume(paths=("C:\\",), disks=(0,))},
    )
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [r.name for r in rows] == ["C:\\", "E:\\"]


def test_nothing_is_claimed_in_a_container_or_without_volumes() -> None:
    capture = _one_disk({"0": _volume(paths=("C:\\",), disks=(0,))})
    assert build_filesystems(capture, Environment.CONTAINER) == (None, ())
    assert build_filesystems(_capture({"\\\\?\\p0": _disk("PhysicalDrive0")}), BARE_METAL) == (None, ())


def test_volumes_from_a_capture_taken_before_space_was_read_are_not_read() -> None:
    # The volume fields of a capture from before the space reading: paths and
    # extents, and nothing the two space calls write.
    capture = _one_disk({"0": {"paths": ["C:\\"], "disks": [0], "drive_type": 3}})
    assert build_filesystems(capture, BARE_METAL) == (None, ())


def test_one_volume_that_carries_a_space_field_makes_the_volumes_read() -> None:
    for field in ({"total_bytes": 10}, {"space_error": "e"}, {"fs_type": "NTFS"}, {"fs_info_error": "e"}):
        capture = _one_disk({"0": {"paths": ["C:\\"], "disks": [0], **field}, "1": {"paths": ["D:\\"], "disks": [0]}})
        rows, _ = build_filesystems(capture, BARE_METAL)
        assert rows is not None, field
        assert [row.name for row in rows] == ["C:\\", "D:\\"], field


_HISTOGRAM_4K = [0, 0, 0, 4096] + [0] * 17


def _c_drive(*, bitmaps_read: bool | None, **volume: Any) -> WindowsCapture:
    """One NTFS volume C: on PhysicalDrive0, with its space read, as an elevated or unelevated run left it."""
    extra: dict[str, Any] = {} if bitmaps_read is None else {"volume_bitmaps_read": bitmaps_read}
    entry = _volume(paths=("C:\\",), disks=(0,), total_bytes=100, free_bytes=40, available_bytes=30, **volume)
    return _capture({"\\\\?\\p0": _disk("PhysicalDrive0")}, volumes={"0": entry}, **extra)


def test_a_volume_carries_its_free_space() -> None:
    free_extents = {"count": 1, "total": 4096, "largest": 4096, "histogram": _HISTOGRAM_4K}
    rows, unread = build_filesystems(_c_drive(bitmaps_read=True, free_extents=free_extents), BARE_METAL)
    assert rows is not None
    assert rows[0].free_space is not None
    assert (rows[0].free_space.fragmentation_percent, unread) == (93, ())


def test_an_unelevated_capture_says_once_that_fragmentation_needs_administrator() -> None:
    rows, unread = build_filesystems(_c_drive(bitmaps_read=False), BARE_METAL)
    assert rows
    assert [(r.reading, r.reason) for r in unread] == [("fragmentation", "needs Administrator")]


def test_a_capture_taken_before_bitmaps_were_read_says_nothing_about_them() -> None:
    rows, unread = build_filesystems(_c_drive(bitmaps_read=None), BARE_METAL)
    assert rows
    assert unread == ()


def test_a_refused_bitmap_is_carried_on_its_row() -> None:
    reason = "could not read the volume bitmap (error 1)"
    rows, _ = build_filesystems(_c_drive(bitmaps_read=True, free_extents={"error": reason}), BARE_METAL)
    assert rows is not None
    assert ("volume-bitmap", reason) in [(r.reading, r.reason) for r in rows[0].readings_refused]


_CDROM = 5


def _undecidable_volumes() -> dict[str, Any]:
    """C: on the disk, beside the two ways a volume can fail to be placed and two that say nothing here.

    D: would not open, a second volume's paths were not read, an ESP's extents
    were not read, and E: is an optical drive whose extents were not read: the
    first two could each be a row on the disk; the ESP has no path to be one
    by, and the optical drive sits on no disk.
    """
    space: dict[str, Any] = {"total_bytes": 100, "free_bytes": 40, "available_bytes": 30}
    return {
        "0": _volume(paths=("C:\\",), disks=(0,), **space),
        "1": _volume(paths=("D:\\",), drive_type=3, error="could not open the volume (error 5)", **space),
        "2": _volume(disks=(0,), paths_error="could not read the volume's paths (error 234)"),
        "3": _volume(esp=True, error="could not read the volume's disk extents (error 1)"),
        "4": _volume(paths=("E:\\",), drive_type=_CDROM, error="could not read the volume's disk extents (error 1)"),
    }


def test_a_volume_that_could_not_be_placed_is_recorded_rather_than_left_out() -> None:
    rows, unread = build_filesystems(_one_disk(_undecidable_volumes()), BARE_METAL)
    assert rows is not None
    assert [row.name for row in rows] == ["C:\\"]
    assert [(r.reading, r.reason) for r in unread] == [
        ("volume", "D:\\: could not open the volume (error 5)"),
        ("volume", "could not read the volume's paths (error 234)"),
    ]


def test_an_optical_drive_that_could_not_be_placed_is_not_a_refusal() -> None:
    """The control: a volume no disk sits behind could never have been a row, so it is silent."""
    volumes = _undecidable_volumes()
    rows, unread = build_filesystems(_one_disk({"0": volumes["0"], "4": volumes["4"]}), BARE_METAL)
    assert rows is not None
    assert [row.name for row in rows] == ["C:\\"]
    assert unread == ()


def test_the_refusals_of_unplaced_volumes_agree_with_the_used_by_column() -> None:
    """One decider for both views wherever a row is possible: a volume refused here leaves used-by undecided.

    The pathless ESP ("3") is the one volume the views part on, held by its own tests below.
    """
    volumes = _undecidable_volumes()
    for key in ("1", "2", "4"):
        capture = _one_disk({"0": volumes["0"], key: volumes[key]})
        assert capture.volumes is not None
        _, unread = build_filesystems(capture, BARE_METAL)
        assert bool(unread) is blocks_not_mounted(capture.volumes[key]), key


def _replayed(volumes: dict[str, Any], tmp_path: Path, runner: CliRunner, factory: Callable[[], object]) -> Any:
    """Replay windows-usb-uas, a physical machine whose committed capture reports ok, with these volumes."""
    from lsdsk.adapters.cli import cli

    payload = json.loads((FIXTURES / "windows-usb-uas.json").read_text(encoding="utf-8"))
    payload["volumes"] = volumes
    payload["volume_bitmaps_read"] = True
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(cli, ["filesystems", "--replay", str(capture), "--format", "json"], obj=factory)
    return json.JSONDecoder().raw_decode(result.output[result.output.index("{") :])[0]


def test_a_replayed_volume_that_could_not_be_placed_makes_the_run_incomplete(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    envelope = _replayed(_undecidable_volumes(), tmp_path, cli_runner, production_factory)
    assert [row["name"] for row in envelope["data"]["filesystems"]] == ["C:\\"]
    assert envelope["ok"] is False
    assert "volume: filesystems - D:\\: could not open the volume (error 5)" in envelope["skipped"]
    assert "volume: filesystems - could not read the volume's paths (error 234)" in envelope["skipped"]
    assert not any("E:\\" in line for line in envelope["skipped"])


def test_the_same_replay_with_every_volume_placed_is_complete(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The control: the machine and the route are the same, so ok above is the volumes' doing."""
    volumes = _undecidable_volumes()
    envelope = _replayed({"0": volumes["0"], "4": volumes["4"]}, tmp_path, cli_runner, production_factory)
    assert [row["name"] for row in envelope["data"]["filesystems"]] == ["C:\\"]
    assert (envelope["ok"], envelope["skipped"]) == (True, [])


_GIB = 1 << 30


def test_a_quota_limited_volume_reads_the_callers_own_fill() -> None:
    """GetDiskFreeSpaceExW's total and available are the caller's under a quota, its total-free is the volume's.

    A 10 GiB quota with 2 GiB of it left, on a volume with 200 GiB free: the
    row's three figures are one scope, the caller's, so the quota reads 80%
    full rather than used 0 beside a free figure smaller than the size.
    """
    quota = _volume(paths=("D:\\",), disks=(0,), total_bytes=10 * _GIB, free_bytes=200 * _GIB, available_bytes=2 * _GIB)
    rows, _ = build_filesystems(_one_disk({"0": quota}), BARE_METAL)
    assert rows is not None
    (row,) = rows
    assert (row.size_bytes, row.used_bytes, row.free_bytes) == (10 * _GIB, 8 * _GIB, 2 * _GIB)
    assert row.used_percent == 80


def test_a_volume_without_a_quota_reads_the_same_whichever_free_figure_is_subtracted() -> None:
    """The control: with no quota Windows reports one free figure twice, and the row is the volume's own."""
    plain = _volume(paths=("D:\\",), disks=(0,), total_bytes=100, free_bytes=40, available_bytes=40)
    rows, _ = build_filesystems(_one_disk({"0": plain}), BARE_METAL)
    assert rows is not None
    assert (rows[0].size_bytes, rows[0].used_bytes, rows[0].free_bytes) == (100, 60, 40)


def _names(paths: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    rows, _ = build_filesystems(_one_disk({"0": _volume(paths=paths, disks=(0,), total_bytes=1)}), BARE_METAL)
    assert rows is not None
    return rows[0].name, rows[0].other_mounts


def test_a_volume_is_named_by_its_drive_letter_whatever_windows_lists_first() -> None:
    assert _names(("C:\\mnt\\c\\", "D:\\mnt\\d\\", "C:\\")) == ("C:\\", ("C:\\mnt\\c\\", "D:\\mnt\\d\\"))
    assert _names(("D:\\mnt\\z\\", "E:\\", "C:\\mnt\\a\\", "F:\\")) == (
        "E:\\",
        ("F:\\", "D:\\mnt\\z\\", "C:\\mnt\\a\\"),
    )


def test_paths_of_one_kind_keep_the_order_windows_listed() -> None:
    """The control: the ordering moves a drive-letter root forward and reorders nothing else."""
    assert _names(("D:\\mnt\\z\\", "C:\\mnt\\a\\")) == ("D:\\mnt\\z\\", ("C:\\mnt\\a\\",))
    assert _names(("F:\\", "E:\\")) == ("F:\\", ("E:\\",))
    assert _names(("C:\\mnt\\",)) == ("C:\\mnt\\", ())


def test_a_volume_that_could_not_be_placed_is_named_by_its_drive_letter_too() -> None:
    failed = _volume(paths=("D:\\mnt\\d\\", "D:\\"), drive_type=3, error="could not open the volume (error 5)")
    _, unread = build_filesystems(_one_disk({"0": _volume(paths=("C:\\",), disks=(0,)), "1": failed}), BARE_METAL)
    assert [r.reason for r in unread] == ["D:\\: could not open the volume (error 5)"]


def test_an_unelevated_capture_with_no_row_claims_no_fragmentation_refusal() -> None:
    """Fragmentation is a figure of a row: with no row nothing was refused, and the run stays complete."""
    elsewhere = _volume(paths=("E:\\",), disks=(7,), total_bytes=100, free_bytes=40, available_bytes=30)
    capture = _capture({"\\\\?\\p0": _disk("PhysicalDrive0")}, volumes={"0": elsewhere}, volume_bitmaps_read=False)
    assert build_filesystems(capture, BARE_METAL) == ((), ())


def test_an_unelevated_run_reports_a_volume_it_could_not_place_beside_the_fragmentation_refusal() -> None:
    """The default run is unelevated: an unplaced volume must not be lost behind the run-wide refusal."""
    placed = _volume(paths=("C:\\",), disks=(0,), total_bytes=100, free_bytes=40, available_bytes=30)
    unplaced = _volume(disks=(0,), paths_error="could not read the volume's paths (error 234)")
    capture = _capture(
        {"\\\\?\\p0": _disk("PhysicalDrive0")}, volumes={"0": placed, "1": unplaced}, volume_bitmaps_read=False
    )
    rows, unread = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [row.name for row in rows] == ["C:\\"]
    assert [r.reading for r in unread] == ["volume", "fragmentation"]
    assert unread[0].reason.endswith("could not read the volume's paths (error 234)")
    assert unread[1].reason == "needs Administrator"


def test_a_ram_disk_whose_extents_name_an_inventoried_disk_is_not_a_row() -> None:
    """Some RAM disk drivers report extents on a real disk; no disk sits behind one, so it is no row."""
    ram = _volume(paths=("R:\\",), disks=(0,), drive_type=6, total_bytes=100, free_bytes=40, available_bytes=40)
    rows, unread = build_filesystems(_one_disk({"0": _volume(paths=("C:\\",), disks=(0,)), "1": ram}), BARE_METAL)
    assert rows is not None
    assert [row.name for row in rows] == ["C:\\"]
    assert unread == ()


def test_a_pathless_esp_whose_extents_failed_is_no_filesystems_refusal_but_keeps_used_by_undecided() -> None:
    """An ESP has no path by design, so it is never a row and its failure leaves no row missing.

    It still leaves the used-by column undecided, because a known ESP on an
    unknown disk could be any disk's boot mark: the two views ask different
    questions of the same failure.
    """
    esp = _volume(esp=True, error="could not read the volume's disk extents (error 1)")
    capture = _one_disk({"0": _volume(paths=("C:\\",), disks=(0,)), "1": esp})
    rows, unread = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert [row.name for row in rows] == ["C:\\"]
    assert unread == ()
    assert resolve_usage(capture, BARE_METAL)["PhysicalDrive0"] is not None  # C: names the disk


def test_a_pathless_esp_alone_on_its_disk_still_leaves_that_disk_undecided() -> None:
    """The used-by half: with no letter to name the second disk, the failed ESP keeps it undecided."""
    esp = _volume(esp=True, error="could not read the volume's disk extents (error 1)")
    capture = _capture(
        {"\\\\?\\p0": _disk("PhysicalDrive0"), "\\\\?\\p1": _disk("PhysicalDrive1")},
        volumes={"0": _volume(paths=("C:\\",), disks=(0,)), "1": esp},
    )
    assert build_filesystems(capture, BARE_METAL)[1] == ()
    assert resolve_usage(capture, BARE_METAL)["PhysicalDrive1"] is None
