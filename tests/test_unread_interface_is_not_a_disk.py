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
from lsdsk.adapters.render.report import render_header

if TYPE_CHECKING:
    from collections.abc import Callable

    from lsdsk.domain.models import Inventory

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
