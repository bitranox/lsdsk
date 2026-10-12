"""Each leg of the Windows stand-in test is held by a record that only that leg decides.

A stand-in is the record the reader writes for a disk interface it could not
read. ``is_unread_interface`` recognises one by the reader's mark, or, for a
capture from before the mark existed, by the label AND an error AND no device
node. ``_devices_accessible`` then asks whether EVERY record is one. Each
condition below is held by a capture that differs from a neighbour in that
condition alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from lsdsk.adapters.cli.commands.scan import (
    _skipped_readings,  # pyright: ignore[reportPrivateUsage] - the seam under test
)
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.report import privilege_note

if TYPE_CHECKING:
    from lsdsk.domain.models import Inventory

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"
LABEL = "unreadable disk interface 1"
REAL_PATH = r"\\?\scsi#disk&ven_x#1"
ERROR = "could not read the device interface path, Win32 error 5"


def _record(path: str, **fields: Any) -> dict[str, Any]:
    return {"path": path, "parent": None, "ancestors": [], **fields}


def _inventory(disks: dict[str, Any], *, elevated: bool, devices_accessible: bool = False) -> Inventory:
    payload = cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))
    payload["elevated"] = elevated
    payload["devices_accessible"] = devices_accessible
    payload["disks"] = disks
    return build_from(payload)


def _says_no_device_nodes(inventory: Inventory) -> bool:
    """Whether the run blames the environment, in the header note and in the skipped list alike."""
    in_note = "no device nodes" in privilege_note(inventory)
    in_skipped = any("no device nodes" in line for line in _skipped_readings(inventory))
    assert in_note == in_skipped, (privilege_note(inventory), _skipped_readings(inventory))
    return in_note


def test_a_run_with_one_stand_in_and_one_real_refused_disk_still_says_no_device_nodes() -> None:
    """Only EVERY record being a stand-in excuses the run; one refused real disk means nothing was opened."""
    inventory = _inventory(
        {LABEL: _record(LABEL, error=ERROR, interface_unreadable=True), REAL_PATH: _record(REAL_PATH, error=ERROR)},
        elevated=False,
    )

    assert len(inventory.unread_interfaces) == 1
    assert len(inventory.disks) == 1
    assert _says_no_device_nodes(inventory)


def test_a_run_of_only_old_style_stand_ins_does_not_blame_missing_device_nodes() -> None:
    """The label leg alone: no mark on the record, so the label, the error and the missing node decide."""
    inventory = _inventory({LABEL: _record(LABEL, error=ERROR)}, elevated=False)

    assert len(inventory.unread_interfaces) == 1
    assert not _says_no_device_nodes(inventory)


def test_a_label_with_no_error_is_a_disk_not_a_stand_in() -> None:
    inventory = _inventory({LABEL: _record(LABEL)}, elevated=True)

    assert len(inventory.disks) == 1
    assert inventory.unread_interfaces == ()


def test_a_label_with_an_error_and_a_device_node_is_a_disk_not_a_stand_in() -> None:
    inventory = _inventory({LABEL: _record(LABEL, error=ERROR, node="PhysicalDrive7")}, elevated=True)

    assert len(inventory.disks) == 1
    assert inventory.unread_interfaces == ()


def test_an_error_with_no_node_under_a_real_path_is_a_disk_not_a_stand_in() -> None:
    inventory = _inventory({REAL_PATH: _record(REAL_PATH, error=ERROR)}, elevated=True)

    assert len(inventory.disks) == 1
    assert inventory.unread_interfaces == ()
