"""A disk interface whose path could not be read is a skipped reading, never a disk.

The reader keeps a record for such an interface so the scan cannot look complete.
That record is not a drive: counting it in the header and drawing an ``unknown``
row told the reader the machine had a disk nobody had read, while the findings
page said "No problems found".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.cli.commands.scan import (
    _skipped_readings,  # pyright: ignore[reportPrivateUsage] - the seam under test
)
from lsdsk.adapters.hw.snapshot import build_from
from lsdsk.adapters.render.report import privilege_note, render_header
from lsdsk.domain.enums import Environment
from lsdsk.domain.models import Inventory, RefusedReading

if TYPE_CHECKING:
    from collections.abc import Callable

_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


def _inventory_with_an_unreadable_interface() -> Inventory:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["disks"] = {
        "unreadable disk interface 1": {
            "path": "unreadable disk interface 1",
            "parent": None,
            "ancestors": [],
            "error": "could not read the device interface path, Win32 error 5",
            "interface_unreadable": True,
        }
    }
    return build_from(payload)


@pytest.mark.os_agnostic
def test_the_unread_interface_is_not_counted_or_drawn_as_a_disk() -> None:
    inventory = _inventory_with_an_unreadable_interface()
    assert inventory.disks == ()
    assert len(inventory.unread_interfaces) == 1


@pytest.mark.os_agnostic
def test_the_header_says_a_disk_interface_was_not_read(rendered: Callable[..., str]) -> None:
    output = rendered(render_header(_inventory_with_an_unreadable_interface()))
    assert " 0 disks " in output
    assert "1 disk interface not read" in output
    assert "Win32 error 5" in output


@pytest.mark.os_agnostic
def test_the_machine_readable_skipped_list_still_names_it() -> None:
    skipped = _skipped_readings(_inventory_with_an_unreadable_interface())
    assert any("Win32 error 5" in line for line in skipped), skipped


@pytest.mark.os_agnostic
def test_a_fully_read_machine_says_nothing_about_unread_interfaces(rendered: Callable[..., str]) -> None:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    output = rendered(render_header(build_from(payload)))
    assert "not read" not in output.replace("Not read", "")


def _stand_in_without_the_flag() -> dict[str, Any]:
    """The stand-in as the 1.7.2 reader wrote it, before ``interface_unreadable`` existed."""
    return {
        "path": "unreadable disk interface 1",
        "parent": None,
        "ancestors": [],
        "error": "could not read the device interface path, Win32 error 5",
    }


@pytest.mark.os_agnostic
def test_a_stand_in_written_before_the_flag_existed_is_still_not_a_disk() -> None:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["disks"] = {"unreadable disk interface 1": _stand_in_without_the_flag()}

    inventory = build_from(payload)

    assert inventory.disks == ()
    assert len(inventory.unread_interfaces) == 1


@pytest.mark.os_agnostic
def test_a_real_disk_whose_open_failed_is_still_a_refused_device_not_a_stand_in() -> None:
    """Control: an erroring record with a real interface path keeps the old reading."""
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["disks"] = {r"\\?\scsi#disk&ven_x#1": {**_stand_in_without_the_flag(), "path": r"\\?\scsi#disk&ven_x#1"}}

    inventory = build_from(payload)

    assert len(inventory.disks) == 1
    assert inventory.unread_interfaces == ()


@pytest.mark.os_agnostic
def test_an_elevated_run_that_listed_only_unreadable_interfaces_does_not_blame_missing_device_nodes() -> None:
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["devices_accessible"] = False  # what the reader records when no record is an opened disk
    payload["disks"] = {"unreadable disk interface 1": {**_stand_in_without_the_flag(), "interface_unreadable": True}}

    inventory = build_from(payload)
    skipped = _skipped_readings(inventory)

    assert "no device nodes" not in privilege_note(inventory)
    assert not any("no device nodes" in line for line in skipped), skipped
    assert any("Win32 error 5" in line for line in skipped), skipped


@pytest.mark.os_agnostic
def test_a_run_where_every_open_failed_still_says_no_device_nodes() -> None:
    """Control: with a real disk refused and none opened, the old explanation stands."""
    payload = cast("dict[str, Any]", json.loads(_FIXTURE.read_text(encoding="utf-8")))
    payload["elevated"] = False
    payload["devices_accessible"] = False
    payload["disks"] = {r"\\?\scsi#disk&ven_x#1": {**_stand_in_without_the_flag(), "path": r"\\?\scsi#disk&ven_x#1"}}

    inventory = build_from(payload)

    assert "no device nodes" in privilege_note(inventory)
    assert any("no device nodes" in line for line in _skipped_readings(inventory))


def _a_caveat_free_inventory(*reasons: str) -> Inventory:
    """An elevated bare-metal run that read every drive but the interfaces named, so nothing else prints a line."""
    return Inventory(
        hostname="h",
        environment=Environment.BARE_METAL,
        privileged=True,
        devices_accessible=True,
        unread_interfaces=tuple(RefusedReading(reading="disk interface", reason=reason) for reason in reasons),
    )


@pytest.mark.os_agnostic
def test_the_unread_line_is_the_only_extra_line_on_a_run_with_no_other_caveat(rendered: Callable[..., str]) -> None:
    """The QEMU capture carries a hypervisor caveat that keeps the header a group anyway, so it cannot show this."""
    lines = rendered(render_header(_a_caveat_free_inventory("Win32 error 5"))).splitlines()

    assert len(lines) == 2, lines
    assert lines[1].strip() == "1 disk interface not read: Win32 error 5."


@pytest.mark.os_agnostic
def test_the_unread_line_names_every_reason_and_pluralises_the_noun(rendered: Callable[..., str]) -> None:
    output = rendered(render_header(_a_caveat_free_inventory("Win32 error 5", "Win32 error 32")))

    assert "2 disk interfaces not read: Win32 error 5; Win32 error 32." in output


@pytest.mark.os_agnostic
def test_a_caveat_free_run_with_every_interface_read_prints_the_banner_alone(rendered: Callable[..., str]) -> None:
    """The control: with nothing unread the same inventory is one line, so the test above is about the note."""
    assert len(rendered(render_header(_a_caveat_free_inventory())).splitlines()) == 1
