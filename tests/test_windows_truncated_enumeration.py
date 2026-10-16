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


def test_the_human_report_says_a_disk_interface_list_was_cut_short(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    from lsdsk.adapters.cli import cli

    capture = capture_with(_BASE, _cut_short("disk interfaces"), tmp_path)
    output = cli_runner.invoke(cli, ["disks", "--replay", str(capture)], obj=production_factory).output

    assert f"1 disk interface not read: {_reason('disk interfaces')}." in " ".join(output.split())
