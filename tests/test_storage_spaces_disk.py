"""A Storage Spaces virtual disk names the gap it really leaves: the pool's drives went unread.

Measured on a Windows 11 guest with a two-way mirror over three SATA disks: the Space is
STORAGE_BUS_TYPE 0x10, ``spaces``, and refuses IDENTIFY and both SMART reads (Win32 error 50 to
the ATA ioctl, ILLEGAL REQUEST to SAT). Windows also hides the pooled drives themselves - they
left the disk enumeration while pooled and came back once the pool was removed - so no drive
under the Space is ever asked for its health.

The Space's own refusals describe a virtual disk declining a drive's commands, which is nothing
anyone could act on, so they are replaced by one ``pool-members`` refusal that says what was
not read. It still counts: a scan that read none of a pool's drives is incomplete (user
decision 2026-10-19, which rejected exempting the Space the way a mounted image file is).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from test_refused_readings import capture_with, envelope_of

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

# Recorded verbatim by the Windows reader for the Space on that guest.
_REFUSED = (
    "passthrough refused, Win32 error 50; "
    "SAT refused, SCSI status 0x02: sense key 0x5 ILLEGAL REQUEST, ASC/ASCQ 0x24/0x00"
)
_SPACE_PATH = "\\\\?\\storage#disk#{00000000-0000-4000-8000-000000000010}#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
_SPACE_NODE = "PhysicalDrive4"
_BASE = "windows-usb-uas.json"


def _plant_space(bus_type: str) -> Callable[[dict[str, Any]], None]:
    """Add a disk shaped like the measured Storage Space, under the transport name given."""

    def plant(data: dict[str, Any]) -> None:
        data["disks"][_SPACE_PATH] = {
            "ancestors": ["ROOT\\SPACEPORT\\0000", "HTREE\\ROOT\\0"],
            "parent": "ROOT\\SPACEPORT\\0000",
            "ata": {"identify_error": _REFUSED, "smart_data_error": _REFUSED, "smart_thresholds_error": _REFUSED},
            "device": {"bus_type": bus_type, "model": "Storage Space", "rev": "0.1", "vendor": "Msft"},
            "node": _SPACE_NODE,
            "passthrough": True,
            "path": _SPACE_PATH,
            "rotating": True,
            "size_bytes": 6442450944,
        }

    return plant


def _space_in(envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    found = [disk for disk in (*data["disks"], *data["virtual_disks"]) if disk["node"] == _SPACE_NODE]
    assert len(found) == 1, f"the planted disk is not in the envelope exactly once: {found}"
    return found[0]


@pytest.mark.os_agnostic
def test_a_storage_space_names_its_unread_pool_drives_instead_of_its_own_refusals(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    envelope = envelope_of(capture_with(_BASE, _plant_space("spaces"), tmp_path), cli_runner, production_factory)

    refused = _space_in(envelope)["readings_refused"]
    assert [entry["reading"] for entry in refused] == ["pool-members"], refused
    assert "Storage Spaces" in refused[0]["reason"]
    assert not [entry for entry in envelope["skipped"] if _REFUSED in entry], envelope["skipped"]
    assert [entry for entry in envelope["skipped"] if entry.startswith(f"pool-members: {_SPACE_NODE} - ")]
    assert envelope["ok"] is False, "a scan that read none of a pool's drives is incomplete"


@pytest.mark.os_agnostic
def test_the_same_refusal_on_a_hypervisor_disk_is_still_its_own(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The rewording follows the transport: plain ``virtual`` (0x0E) keeps its three refusals."""
    envelope = envelope_of(capture_with(_BASE, _plant_space("virtual"), tmp_path), cli_runner, production_factory)

    refused = _space_in(envelope)["readings_refused"]
    assert [entry["reading"] for entry in refused] == ["identify", "smart-data", "smart-thresholds"], refused
