"""A mounted disk-image file has no ATA identity or SMART to give, so asking is no refused reading.

Windows reports a mounted VHD, VHDX or ISO file as STORAGE_BUS_TYPE 0x0F,
"file-backed virtual". The reader asks every non-NVMe disk for ATA IDENTIFY and
SMART, and a file answers neither, so the capture records the refusal text. That
is the same nothing-to-give case as an AHCI controller with no register region:
the scan is not incomplete for it, and a 32 MiB image someone mounted turned
``ok`` false on an otherwise complete privileged run.

The exemption is keyed on the transport Windows named, never on the error text:
the same Win32 error comes from a real drive behind a controller that blocks
passthrough, which IS an incomplete scan. And it is held to 0x0F alone - a disk
Windows calls plain "virtual" (0x0E) is a hypervisor's disk, the guest's real
storage, whose refused SMART is a real gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from test_refused_readings import capture_with, envelope_of

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

# Recorded verbatim by the Windows reader for a mounted VHD on a real machine.
_REFUSED = "passthrough refused, Win32 error 1; SAT refused, SCSI status 0x02"
_IMAGE_PATH = "\\\\?\\scsi#disk&ven_msft&prod_virtual_disk#2&1f4adffe&0&000001#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
_IMAGE_NODE = "PhysicalDrive9"
_BASE = "windows-usb-uas.json"


def _plant_image(bus_type: str) -> Callable[[dict[str, Any]], None]:
    """Add a disk shaped like a mounted VHD, under the transport name given."""

    def plant(data: dict[str, Any]) -> None:
        data["disks"][_IMAGE_PATH] = {
            "ancestors": ["{8E7BD593-6E6C-4C52-86A6-77175494DD8E}\\MSVHDHBA\\1&3030E83&0&01", "HTREE\\ROOT\\0"],
            "parent": "{8E7BD593-6E6C-4C52-86A6-77175494DD8E}\\MSVHDHBA\\1&3030E83&0&01",
            "ata": {"identify_error": _REFUSED, "smart_data_error": _REFUSED, "smart_thresholds_error": _REFUSED},
            "device": {"bus_type": bus_type, "model": "Virtual Disk", "rev": "1.0", "vendor": "Msft"},
            "node": _IMAGE_NODE,
            "passthrough": True,
            "path": _IMAGE_PATH,
            "rotating": False,
            "size_bytes": 33554432,
        }

    return plant


def _image_in(envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    found = [disk for disk in (*data["disks"], *data["virtual_disks"]) if disk["node"] == _IMAGE_NODE]
    assert len(found) == 1, f"the planted disk is not in the envelope exactly once: {found}"
    return found[0]


@pytest.mark.os_agnostic
def test_the_base_capture_is_complete_so_ok_can_tell_the_arms_apart(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The precondition: without the planted disk the run reports ok, so ok is a live signal."""
    envelope = envelope_of(capture_with(_BASE, lambda data: None, tmp_path), cli_runner, production_factory)

    assert envelope["ok"] is True, envelope["skipped"]


@pytest.mark.os_agnostic
def test_a_mounted_image_file_refusing_ata_is_not_an_incomplete_scan(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """A file-backed virtual disk has no IDENTIFY or SMART, so it carries no refused reading."""
    capture = capture_with(_BASE, _plant_image("file-backed virtual"), tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert _image_in(envelope)["readings_refused"] == []
    assert not [entry for entry in envelope["skipped"] if _REFUSED in entry], envelope["skipped"]
    assert envelope["ok"] is True, envelope["skipped"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "bus_type",
    [
        pytest.param("virtual", id="a hypervisor disk is the guest's real storage"),
        pytest.param("sata", id="a real drive whose controller blocks passthrough"),
    ],
)
def test_the_same_refusal_on_any_other_transport_is_still_named(
    bus_type: str,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The exemption follows the transport, not the error text, and covers 0x0F alone."""
    capture = capture_with(_BASE, _plant_image(bus_type), tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    refused = _image_in(envelope)["readings_refused"]
    assert [entry["reading"] for entry in refused] == ["identify", "smart-data", "smart-thresholds"], refused
    assert [entry for entry in envelope["skipped"] if _REFUSED in entry], envelope["skipped"]
    assert envelope["ok"] is False
