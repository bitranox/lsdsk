"""What Windows ``read_system`` composes from its readings, driven through fake libraries.

``read_system`` decides from its earlier readings what it asks later and what
the capture records: which volumes' bitmaps an elevated run walks (only those on
a disk the run inventoried), whether a device list SetupAPI cut short reaches
the capture, and whether the volumes' space was read. Each of those is a line of
wiring no single reader's test reaches, so these drive the whole function
through :class:`SystemReaders`, with the device tree built over the same fake
``setupapi``, ``cfgmgr32`` and ``kernel32`` the reader tests use, and replay what
it produced through the real command.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from test_refused_readings import capture_with, envelope_of
from test_windows_device_tree import ERROR_INVALID_DATA, FakeCfgmgr32, FakeSetupApi
from test_windows_volume_reader import FakeVolume, FakeVolumeKernel

from lsdsk.adapters.hw.windows.reader import (
    SystemReaders,
    _DeviceTree,  # pyright: ignore[reportPrivateUsage] - the tree is the seam the fakes reach the reader through
    read_system,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from lsdsk.adapters.hw.windows import winapi as api

#: The one disk the run inventories, and the number its volumes' extents name it by.
_NODE, _NUMBER = "PhysicalDrive0", 0
#: A disk number no inventoried disk carries.
_ELSEWHERE = 5
VOLUME_C = "\\\\?\\Volume{c}\\"
VOLUME_X = "\\\\?\\Volume{x}\\"


def _one_disk(tree: _DeviceTree) -> dict[str, dict[str, Any]]:
    del tree
    return {"\\\\?\\disk0": {"path": "\\\\?\\disk0", "parent": None, "ancestors": [], "node": _NODE}}


def _read(*, kernel: FakeVolumeKernel, setupapi: FakeSetupApi | None = None, elevated: bool = False) -> dict[str, Any]:
    """Run ``read_system`` over the fakes, with one inventoried disk."""
    setup = setupapi or FakeSetupApi()

    def last_error() -> int:
        # The volumes are read after every SetupAPI list, so once kernel32 has
        # failed its error is the latest; before that only SetupAPI's can be.
        return kernel.error or setup.error

    def tree() -> _DeviceTree:
        return _DeviceTree(
            setupapi=cast("api.WinLibrary", setup),
            cfgmgr32=cast("api.WinLibrary", FakeCfgmgr32(device_ids={1: "PCI\\A", 2: "PCI\\B"})),
            kernel32=cast("api.WinLibrary", kernel),
            last_error=last_error,
        )

    readers = SystemReaders(
        tree=tree,
        disks=_one_disk,
        elevated=lambda: elevated,
        environment=dict,
        pci_names=lambda pci: {},
    )
    return read_system(readers)


def _envelope(
    reading: dict[str, Any], command: str, tmp_path: Path, runner: CliRunner, factory: Callable[[], object]
) -> dict[str, Any]:
    """Replay what ``read_system`` produced through one command's JSON output."""
    from lsdsk.adapters.cli import cli

    path = tmp_path / "read.json"
    path.write_text(json.dumps(reading), encoding="utf-8")
    output = runner.invoke(cli, [command, "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)


def _bitmap_volume(disk: int, *, letter: str | None = None) -> FakeVolume:
    """A volume on ``disk`` whose bitmap an elevated run can walk, lettered C on the inventoried disk, else X."""
    name = letter or ("C" if disk == _NUMBER else "X")
    return FakeVolume(paths=(f"{name}:\\",), disks=(disk,), bitmap=[1, 0, 0, 1])


def test_the_volumes_a_run_read_are_rows_on_its_disks(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The run records that it asked its volumes for their space, so the volume on the disk is a row."""
    reading = _read(kernel=FakeVolumeKernel({VOLUME_C: FakeVolume(paths=("C:\\",), disks=(_NUMBER,))}))

    rows = _envelope(reading, "filesystems", tmp_path, cli_runner, production_factory)["data"]["filesystems"]

    assert rows is not None, "the volumes were read, so the section is not 'not read'"
    assert [row["name"] for row in rows] == ["C:\\"]


def test_a_device_list_setupapi_cut_short_reaches_the_capture_and_the_run_says_so(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    setupapi = FakeSetupApi(by_enumerator={"PCI": [1, 2]}, fail_enum_at={1: 1})
    reading = _read(kernel=FakeVolumeKernel({}), setupapi=setupapi)
    reason = (
        f"the list of PCI devices ended early at entry 2 (Win32 error {ERROR_INVALID_DATA}), "
        "so whatever followed was not read"
    )

    envelope = _envelope(reading, "health", tmp_path, cli_runner, production_factory)

    assert reading["lists_cut_short"] == {"PCI devices": reason}
    assert f"PCI devices: device list - {reason}" in envelope["skipped"]
    assert envelope["ok"] is False


def test_a_complete_device_list_records_nothing_cut_short() -> None:
    """The control: the same two devices, ending as SetupAPI ends a list."""
    reading = _read(kernel=FakeVolumeKernel({}), setupapi=FakeSetupApi(by_enumerator={"PCI": [1, 2]}))

    assert reading["lists_cut_short"] == {}
    assert sorted(reading["pci"]) == ["PCI\\A", "PCI\\B"]


def test_an_elevated_run_walks_the_bitmap_only_of_a_volume_on_a_disk_it_inventoried() -> None:
    kernel = FakeVolumeKernel({VOLUME_C: _bitmap_volume(_NUMBER), VOLUME_X: _bitmap_volume(_ELSEWHERE)})

    reading = _read(kernel=kernel, elevated=True)

    walked = {entry["paths"][0]: "free_extents" in entry for entry in reading["volumes"].values()}
    assert walked == {"C:\\": True, "X:\\": False}
    assert reading["volume_bitmaps_read"] is True


def test_an_unelevated_run_walks_no_bitmap() -> None:
    """The control: the same volumes, not elevated, walk nothing and say so."""
    kernel = FakeVolumeKernel({VOLUME_C: _bitmap_volume(_NUMBER), VOLUME_X: _bitmap_volume(_ELSEWHERE)})

    reading = _read(kernel=kernel)

    assert all("free_extents" not in entry for entry in reading["volumes"].values())
    assert reading["volume_bitmaps_read"] is False


#: What the fake's volume enumeration fails with; any error but ERROR_NO_MORE_FILES would do.
ERROR_ACCESS_DENIED = 5
ERROR_NOT_READY = 21


def _volume_lines(envelope: dict[str, Any]) -> list[str]:
    return [line for line in envelope["skipped"] if line.startswith("volume: filesystems - ")]


def test_a_volume_enumeration_refused_at_its_first_call_makes_the_run_incomplete(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    kernel = FakeVolumeKernel({VOLUME_C: _bitmap_volume(_NUMBER)}, first_error=ERROR_ACCESS_DENIED)
    reading = _read(kernel=kernel, elevated=True)

    envelope = _envelope(reading, "filesystems", tmp_path, cli_runner, production_factory)

    assert _volume_lines(envelope) == [
        f"volume: filesystems - could not list the volumes (Win32 error {ERROR_ACCESS_DENIED})"
    ]
    assert envelope["ok"] is False
    assert envelope["data"]["filesystems"] is None


def test_a_volume_enumeration_that_fails_part_way_makes_the_run_incomplete(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    kernel = FakeVolumeKernel(
        {
            VOLUME_C: _bitmap_volume(_NUMBER),
            VOLUME_X: _bitmap_volume(_NUMBER, letter="X"),
        },
        next_error=ERROR_NOT_READY,
    )
    reading = _read(kernel=kernel, elevated=True)

    envelope = _envelope(reading, "disks", tmp_path, cli_runner, production_factory)

    assert _volume_lines(envelope) == [
        f"volume: filesystems - the list of volumes ended early at entry 2 (Win32 error {ERROR_NOT_READY}), "
        "so none of it was used"
    ]
    assert envelope["ok"] is False
    (disk,) = envelope["data"]["disks"]
    assert disk["usage"] is None, "no volume was kept, so what the disk is used by stays undecided"


def test_a_volume_enumeration_that_ends_normally_makes_no_volume_line(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The control: the same volumes, listed to their end, leave the run complete."""
    kernel = FakeVolumeKernel(
        {
            VOLUME_C: _bitmap_volume(_NUMBER),
            VOLUME_X: _bitmap_volume(_NUMBER, letter="X"),
        }
    )
    reading = _read(kernel=kernel, elevated=True)

    envelope = _envelope(reading, "disks", tmp_path, cli_runner, production_factory)

    assert _volume_lines(envelope) == []
    assert envelope["ok"] is True, envelope["skipped"]


def _no_volumes(error: str | None) -> Callable[[dict[str, Any]], None]:
    def plant(data: dict[str, Any]) -> None:
        data.update(volumes=None, windows_volume=None)
        data.pop("volumes_error", None)
        if error is not None:
            data["volumes_error"] = error

    return plant


def test_a_capture_without_the_volumes_record_replays_as_it_always_did(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """A capture taken before the record existed cannot say why it has no volumes, and claims nothing."""
    base = envelope_of(
        capture_with("windows-usb-uas.json", lambda data: None, tmp_path), cli_runner, production_factory
    )
    older = envelope_of(
        capture_with("windows-usb-uas.json", _no_volumes(None), tmp_path), cli_runner, production_factory
    )

    assert _volume_lines(older) == []
    assert older["ok"] is base["ok"] is True


def test_a_recorded_volume_enumeration_failure_is_replayed(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    reason = "could not list the volumes (Win32 error 5)"
    capture = capture_with("windows-usb-uas.json", _no_volumes(reason), tmp_path)

    envelope = envelope_of(capture, cli_runner, production_factory)

    assert _volume_lines(envelope) == [f"volume: filesystems - {reason}"]
    assert envelope["ok"] is False
