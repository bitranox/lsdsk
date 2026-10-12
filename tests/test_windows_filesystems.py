"""The Windows volumes that live on an inventory disk become filesystem rows."""

from __future__ import annotations

from typing import Any

from lsdsk.adapters.hw.windows.capture import WindowsCapture
from lsdsk.adapters.hw.windows.filesystems import build_filesystems
from lsdsk.domain.enums import Environment, FilesystemKind

BARE_METAL = Environment.BARE_METAL


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
    assert (c.size_bytes, c.used_bytes, c.free_bytes, c.fs_type) == (100, 60, 30, "NTFS")
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
    capture = _one_disk({"0": _volume(paths=("D:\\",), disks=(0,), total_bytes=100, available_bytes=30)})
    rows, _ = build_filesystems(capture, BARE_METAL)
    assert rows is not None
    assert (rows[0].size_bytes, rows[0].used_bytes, rows[0].free_bytes) == (100, None, 30)


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
