"""A Storage Spaces virtual disk reports the gap it leaves, and leaves none once its drives are read.

Measured on a Windows 11 guest with a two-way mirror over three SATA disks: the Space is
STORAGE_BUS_TYPE 0x10, ``spaces``, and refuses IDENTIFY and both SMART reads (Win32 error 50 to
the ATA ioctl, ILLEGAL REQUEST to SAT). Windows moves the pooled drives themselves from the disk
interface class to the hidden one, where they answer IDENTIFY and SMART as before (ADR 0007).

The Space's own refusals describe a virtual disk declining a drive's commands, which is nothing
anyone could act on. When none of its drives was read and linked to it, they are replaced by one
``pool-members`` refusal that says what was not read, and it counts: such a scan is incomplete
(user decision 2026-10-19, which rejected exempting the Space the way a mounted image file is).
When a drive was linked, the Space reports nothing and each drive reports its own readings
(user decision 2026-10-19, ADR 0007).
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


# ---------------------------------------------------------------------------
# The pooled drives read (ADR 0007): a drive under GUID_DEVINTERFACE_HIDDEN_DISK
# names its Space among its removal relations.
# ---------------------------------------------------------------------------

_WITH_VOLUMES = "windows-chipset-switch.json"
#: The Space as CM_Get_Device_IDW names it, and the same device as a pooled drive's removal
#: relations name it - both spellings measured on one guest. Instance identifiers compare
#: without regard to case on Windows.
_SPACE_INSTANCE = "STORAGE\\DISK\\{69486059-9506-4070-8B3A-C7A565EE19AB}"
_SPACE_AS_NAMED = "STORAGE\\Disk\\{69486059-9506-4070-8b3a-c7a565ee19ab}"
_POOLED_PATH = "\\\\?\\scsi#disk&ven_qemu&prod_harddisk#6&2b320803&0&010000#{7fccc86c-228a-40ad-8a58-f590af7bfdce}"
_POOLED_NODE = "PhysicalDrive5"


def _plant_pool(*, names: str) -> Callable[[dict[str, Any]], None]:
    """Add the Space, one pooled drive whose removal relations name `names`, and a letter on the Space.

    The pooled drive is the capture's own SATA drive under a hidden interface, so it carries
    a real IDENTIFY and SMART reading.
    """

    def plant(data: dict[str, Any]) -> None:
        _plant_space("spaces")(data)
        data["disks"][_SPACE_PATH]["instance"] = _SPACE_INSTANCE
        sata = next(entry for entry in data["disks"].values() if entry.get("node") == "PhysicalDrive0")
        data["disks"][_POOLED_PATH] = {
            **sata,
            "path": _POOLED_PATH,
            "node": _POOLED_NODE,
            "instance": "SCSI\\DISK&VEN_QEMU&PROD_HARDDISK\\6&2B320803&0&010000",
            "hidden": True,
            "removal_relations": ["STORAGE\\Volume\\{ce701527-cbff-11f1-8212-806e6f6e6963}#0000000000004400", names],
        }
        data["volumes"]["7"] = {"disks": [4], "drive_type": 3, "esp": False, "paths": ["F:\\"]}

    return plant


def _disk_in(envelope: dict[str, Any], node: str) -> dict[str, Any]:
    data = envelope["data"]
    found = [disk for disk in (*data["disks"], *data["virtual_disks"]) if disk["node"] == node]
    assert len(found) == 1, f"{node} is not in the envelope exactly once: {found}"
    return found[0]


@pytest.mark.os_agnostic
def test_a_space_whose_pooled_drive_was_read_leaves_nothing_unread(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    capture = capture_with(_WITH_VOLUMES, _plant_pool(names=_SPACE_AS_NAMED), tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert _disk_in(envelope, _SPACE_NODE)["readings_refused"] == []
    assert not [entry for entry in envelope["skipped"] if entry.startswith("pool-members:")], envelope["skipped"]


@pytest.mark.os_agnostic
def test_a_pooled_drive_is_listed_with_its_health_and_used_by_its_space(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    capture = capture_with(_WITH_VOLUMES, _plant_pool(names=_SPACE_AS_NAMED), tmp_path)
    pooled = _disk_in(envelope_of(capture, cli_runner, production_factory), _POOLED_NODE)

    assert pooled["usage"]["uses"] == [{"kind": "space", "name": _SPACE_NODE, "mounts": ["F:\\"]}]
    assert pooled["health"]["power_on_hours"] is not None, "the pooled drive's SMART was not read"


@pytest.mark.os_agnostic
def test_a_hidden_drive_naming_another_space_links_to_nothing(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The control: the link is the instance the Space carries, not any Space on the machine."""
    capture = capture_with(
        _WITH_VOLUMES, _plant_pool(names="STORAGE\\DISK\\{00000000-0000-0000-0000-000000000000}"), tmp_path
    )
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert [entry["reading"] for entry in _disk_in(envelope, _SPACE_NODE)["readings_refused"]] == ["pool-members"]
    uses = _disk_in(envelope, _POOLED_NODE)["usage"]["uses"]
    assert all(use["kind"] != "space" for use in uses), uses


# ---------------------------------------------------------------------------
# The committed capture: a Windows 11 guest with three SATA drives in one
# two-way mirror Space holding D:, read elevated by the reader that lists the
# hidden disk class.
# ---------------------------------------------------------------------------

_REAL = "windows-storage-spaces.json"
_REAL_SPACE = "PhysicalDrive4"
_REAL_POOLED = ("PhysicalDrive1", "PhysicalDrive2", "PhysicalDrive3")


@pytest.mark.os_agnostic
def test_every_pooled_drive_in_the_real_capture_is_read_and_used_by_its_space(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    envelope = envelope_of(capture_with(_REAL, lambda _data: None, tmp_path), cli_runner, production_factory)

    for node in _REAL_POOLED:
        pooled = _disk_in(envelope, node)
        assert pooled["usage"]["uses"] == [{"kind": "space", "name": _REAL_SPACE, "mounts": ["D:\\"]}], node
        assert pooled["health"]["power_on_hours"] is not None, f"{node}'s SMART was not read"
        assert pooled["readings_refused"] == [], node


@pytest.mark.os_agnostic
def test_the_real_capture_leaves_no_pool_member_unread(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The one skipped line left is the run-wide one every virtual machine carries."""
    envelope = envelope_of(capture_with(_REAL, lambda _data: None, tmp_path), cli_runner, production_factory)

    assert _disk_in(envelope, _REAL_SPACE)["readings_refused"] == []
    assert envelope["skipped"] == ["physical-link-rules: suppressed, the hypervisor invents these values"]


@pytest.mark.os_agnostic
def test_the_real_capture_without_its_pooled_drives_names_the_gap_again(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The control: what a reader that never listed the hidden class would have produced."""

    def drop_hidden(data: dict[str, Any]) -> None:
        data["disks"] = {path: entry for path, entry in data["disks"].items() if not entry.get("hidden")}

    envelope = envelope_of(capture_with(_REAL, drop_hidden, tmp_path), cli_runner, production_factory)

    assert [entry["reading"] for entry in _disk_in(envelope, _REAL_SPACE)["readings_refused"]] == ["pool-members"]
    assert envelope["ok"] is False


# ---------------------------------------------------------------------------
# A drive whose removal relations name two Spaces backs both of them. A pool
# holding several Spaces spreads each over its drives, so one drive can name
# more than one; which it names first is the order Windows listed them in,
# which nothing documents, so the answer may not depend on it.
# ---------------------------------------------------------------------------

_SECOND_SPACE_GUID = "11111111-2222-3333-4444-555555555555"
_SECOND_SPACE = "PhysicalDrive5"
_SECOND_SPACE_PATH = f"\\\\?\\storage#disk#{{{_SECOND_SPACE_GUID}}}#{{53f56307-b6bf-11d0-94f2-00a0c91efb8b}}"


def _plant_second_space(*, named_first: bool) -> Callable[[dict[str, Any]], None]:
    """Add a second Space to the real capture, lettered E:, and name it in every pooled drive's relations.

    Args:
        named_first: Whether each pooled drive names the new Space before the one it already backs.
    """

    def plant(data: dict[str, Any]) -> None:
        space = next(entry for entry in data["disks"].values() if entry.get("node") == _REAL_SPACE)
        data["disks"][_SECOND_SPACE_PATH] = {
            **space,
            "path": _SECOND_SPACE_PATH,
            "node": _SECOND_SPACE,
            "instance": f"STORAGE\\DISK\\{{{_SECOND_SPACE_GUID.upper()}}}",
        }
        data["volumes"]["9"] = {"disks": [5], "drive_type": 3, "esp": False, "paths": ["E:\\"]}
        relation = f"STORAGE\\Disk\\{{{_SECOND_SPACE_GUID}}}"
        for entry in data["disks"].values():
            if entry.get("hidden"):
                relations: list[str] = entry["removal_relations"]
                relations.insert(0 if named_first else len(relations), relation)

    return plant


@pytest.mark.parametrize("named_first", [pytest.param(True, id="named first"), pytest.param(False, id="named last")])
def test_a_pooled_drive_naming_two_spaces_is_used_by_both_whichever_it_names_first(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
    named_first: bool,
) -> None:
    capture = capture_with(_REAL, _plant_second_space(named_first=named_first), tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    for space in (_REAL_SPACE, _SECOND_SPACE):
        assert _disk_in(envelope, space)["readings_refused"] == [], space
    assert not [entry for entry in envelope["skipped"] if entry.startswith("pool-members:")], envelope["skipped"]
    for node in _REAL_POOLED:
        assert _disk_in(envelope, node)["usage"]["uses"] == [
            {"kind": "space", "name": _REAL_SPACE, "mounts": ["D:\\"]},
            {"kind": "space", "name": _SECOND_SPACE, "mounts": ["E:\\"]},
        ], node


def test_a_space_named_twice_by_one_drive_is_one_use_of_it(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """A relation listed twice, in either spelling, names one Space, so the drive has one use of it."""

    def name_twice(data: dict[str, Any]) -> None:
        for entry in data["disks"].values():
            if entry.get("hidden"):
                entry["removal_relations"].append(entry["removal_relations"][-1].upper())

    envelope = envelope_of(capture_with(_REAL, name_twice, tmp_path), cli_runner, production_factory)

    for node in _REAL_POOLED:
        assert _disk_in(envelope, node)["usage"]["uses"] == [
            {"kind": "space", "name": _REAL_SPACE, "mounts": ["D:\\"]}
        ], node


# ---------------------------------------------------------------------------
# What may stand on each side of a link: only a Space is linked TO, and only a
# hidden drive is linked FROM. The capture is untrusted input, so each guard is
# held by a relation that names something the guard exists to turn away.
# ---------------------------------------------------------------------------


def test_a_pooled_drive_naming_a_disk_that_is_not_a_space_is_not_used_by_it(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The boot disk has an instance identifier too, but Windows names no transport of it a Space."""

    def name_the_boot_disk(data: dict[str, Any]) -> None:
        boot = next(entry for entry in data["disks"].values() if entry.get("node") == "PhysicalDrive0")
        pooled = next(entry for entry in data["disks"].values() if entry.get("node") == _REAL_POOLED[0])
        pooled["removal_relations"].append(boot["instance"])

    envelope = envelope_of(capture_with(_REAL, name_the_boot_disk, tmp_path), cli_runner, production_factory)

    assert _disk_in(envelope, _REAL_POOLED[0])["usage"]["uses"] == [
        {"kind": "space", "name": _REAL_SPACE, "mounts": ["D:\\"]}
    ]


def test_a_disk_listed_in_the_ordinary_class_naming_a_space_is_not_one_of_its_drives(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """Only a drive Windows hid is in a pool; relations on an ordinary disk link it to nothing."""

    def relate_the_boot_disk(data: dict[str, Any]) -> None:
        space = next(entry for entry in data["disks"].values() if entry.get("node") == _REAL_SPACE)
        boot = next(entry for entry in data["disks"].values() if entry.get("node") == "PhysicalDrive0")
        boot["removal_relations"] = [space["instance"]]

    envelope = envelope_of(capture_with(_REAL, relate_the_boot_disk, tmp_path), cli_runner, production_factory)

    assert _disk_in(envelope, "PhysicalDrive0")["usage"]["uses"] == [{"kind": "letter", "name": "", "mounts": ["C:\\"]}]
