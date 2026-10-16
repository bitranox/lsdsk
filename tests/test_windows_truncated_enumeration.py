"""A Windows device list cut short is a scan that is incomplete, and the scan says so.

The reader enumerates PCI devices, USB devices, USB hubs and disk interfaces
through SetupAPI, which ends a list by failing with ``ERROR_NO_MORE_ITEMS``. A
failure with any other error stops the list early, and the entries past it were
never read: a machine with one PCI device or one disk fewer than it has looks
the same as a machine that has that many. The reader records each list it
stopped early under ``lists_cut_short``, and these tests replay that record
through the real command, so they hold the whole path from capture to envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from test_refused_readings import capture_with, envelope_of

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

_BASE = "windows-usb-uas.json"


def _reason(name: str) -> str:
    """What the reader records for a list it stopped early, worded as it words it."""
    return f"the list of {name} ended early at entry 4 (Win32 error 13), so whatever followed was not read"


def _cut_short(*names: str) -> Callable[[dict[str, Any]], None]:
    def plant(data: dict[str, Any]) -> None:
        data["lists_cut_short"] = {name: _reason(name) for name in names}

    return plant


def test_the_base_capture_is_complete_so_ok_can_tell_the_arms_apart(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The precondition: without a cut-short list the run reports ok, so ok is a live signal."""
    envelope = envelope_of(capture_with(_BASE, lambda data: None, tmp_path), cli_runner, production_factory)

    assert envelope["ok"] is True, envelope["skipped"]


def test_a_device_list_cut_short_is_named_in_skipped_and_turns_ok_false(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    capture = capture_with(_BASE, _cut_short("PCI devices", "USB hubs"), tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert envelope["skipped"] == [
        f"PCI devices: device list - {_reason('PCI devices')}",
        f"USB hubs: device list - {_reason('USB hubs')}",
    ]
    assert envelope["ok"] is False


def test_a_disk_interface_list_cut_short_is_an_unread_disk_interface(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """A lost disk interface is a lost drive, so it takes the path an unreadable interface takes."""
    capture = capture_with(_BASE, _cut_short("disk interfaces"), tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert envelope["skipped"] == [f"device: disk interface - {_reason('disk interfaces')}"]
    assert envelope["ok"] is False


def _human_disks(capture: Path, runner: CliRunner, factory: Callable[[], object]) -> str:
    from lsdsk.adapters.cli import cli

    return " ".join(runner.invoke(cli, ["disks", "--replay", str(capture)], obj=factory).output.split())


def test_the_human_report_says_a_disk_interface_list_was_cut_short(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """A cut list is not one interface: nobody can say how many went unlisted, so it is not counted."""
    capture = capture_with(_BASE, _cut_short("disk interfaces"), tmp_path)
    output = _human_disks(capture, cli_runner, production_factory)

    assert f"Not every disk interface was listed: {_reason('disk interfaces')}." in output
    assert "disk interface not read" not in output


#: What the reader records for one interface whose path it could not read.
_UNREADABLE = "could not read the device interface path, Win32 error 5"


def _cut_short_after_an_unreadable_interface(data: dict[str, Any]) -> None:
    _cut_short("disk interfaces")(data)
    data["disks"]["unreadable disk interface 1"] = {
        "path": "unreadable disk interface 1",
        "ancestors": [],
        "error": _UNREADABLE,
        "interface_unreadable": True,
    }


def test_an_unreadable_interface_is_counted_and_a_cut_list_is_said_apart(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    capture = capture_with(_BASE, _cut_short_after_an_unreadable_interface, tmp_path)
    output = _human_disks(capture, cli_runner, production_factory)

    assert f"1 disk interface not read: {_UNREADABLE}." in output
    assert f"Not every disk interface was listed: {_reason('disk interfaces')}." in output


def test_both_kinds_still_reach_skipped_as_disk_interface_lines(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The envelope is unchanged: one line each, the unreadable interface first."""
    capture = capture_with(_BASE, _cut_short_after_an_unreadable_interface, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert envelope["skipped"] == [
        f"device: disk interface - {_UNREADABLE}",
        f"device: disk interface - {_reason('disk interfaces')}",
    ]
