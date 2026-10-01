"""A reading the machine refused is recorded, and named to the caller.

``ok`` exists so a program need not inspect every field, and this is the case
where that promise failed: an AHCI port count comes from mapping BAR5, which is
refused on some hosts even to root, and a drive behind some RAID drivers refuses
SMART passthrough the same way. The field went null, ``ok`` stayed true, and the
run was indistinguishable from a complete one.

The rule is keyed on a refusal the reader RECORDED, never on a field being null.
Both platform readers already write the OS error text per device; what dropped it
was the typed capture models, which named only the payload keys. Keying on null
instead was implemented once and reverted: a null port count is also what an NVMe
controller and a legitimately zero AHCI bitmap produce, so it fired on every
healthy capture, and a rule that fires on healthy hardware is worse than the gap.
``test_a_complete_privileged_run_reports_ok_on_every_committed_capture`` is that
direction, and it is the half that keeps this honest.
"""

from __future__ import annotations

import json
import mmap
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest

from lsdsk.adapters.hw.linux.capture import AtaBlobs, NvmeBlobs, UsbDeviceEntry
from lsdsk.adapters.hw.windows.capture import DiskEntry, HealthBlobs

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from click.testing import CliRunner
    from pydantic import BaseModel

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# Planted in the capture as the text the OS gave, and asserted in the envelope:
# the caller gets the reason, not a flag saying something was missing.
REFUSAL = "[Errno 1] Operation not permitted (planted)"

# A skipif CONDITION is evaluated at IMPORT time, before any marker can skip
# anything, and ``os.geteuid`` does not exist on Windows - so a bare call in the
# decorator raises AttributeError on the Windows runner and takes down collection
# of this whole FILE rather than skipping one test. Asking whether the platform
# has euids at all keeps the question answerable everywhere; ``os_linux`` is what
# actually keeps the test off Windows.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def envelope_of(capture: Path, runner: CliRunner, factory: Callable[[], object]) -> dict[str, Any]:
    """Replay one capture through ``health --format json`` and decode its envelope.

    Args:
        capture: The capture file to replay.
        runner: The Click runner fixture.
        factory: The production service factory fixture.

    Returns:
        The decoded envelope.
    """
    from lsdsk.adapters.cli import cli

    output = runner.invoke(cli, ["health", "--replay", str(capture), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)


def capture_with(fixture: str, mutate: Callable[[dict[str, Any]], None], destination: Path) -> Path:
    """Copy a committed capture, plant a recorded refusal in it and write it out.

    A committed capture is used as the base rather than a hand-built mapping so
    everything around the planted refusal is a real reading: a synthetic capture
    would prove the model accepts the key, not that a real run reports it.

    Args:
        fixture: File name under ``tests/fixtures/hw``.
        mutate: Plants the refusal in the decoded capture.
        destination: Directory to write the modified copy into.

    Returns:
        The path written.
    """
    data = cast("dict[str, Any]", json.loads((FIXTURES / fixture).read_text(encoding="utf-8")))
    mutate(data)
    path = destination / fixture
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def refusal_lines(envelope: dict[str, Any]) -> list[str]:
    """The skipped entries that carry the planted reason."""
    return [entry for entry in envelope["skipped"] if REFUSAL in entry]


def _refusal_fields(model: type[BaseModel], renamed: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """Every refusal field a capture model carries, paired with its reading name.

    Read from the model's own ``model_fields`` rather than hand-listed, so a
    refusal field added to a capture model is covered here without this file
    changing. ``error`` (the whole device could not be opened) reads as
    ``device``, matching what every builder passes to ``refusals_of``; every
    other ``*_error`` field reads as its own name with underscores turned to
    hyphens, which is the same rule.

    Args:
        model: A capture model that carries one or more refusal fields.
        renamed: The fields whose reading is named for what the refusal leaves
            unread rather than for the field: a USB device's ``bos_error`` is
            the reason the disk's ``usb-link`` was not read at one end.

    Returns:
        One ``(field_name, reading_name)`` pair per refusal field.
    """
    fields: list[tuple[str, str]] = []
    for name in model.model_fields:
        if renamed is not None and name in renamed:
            fields.append((name, renamed[name]))
        elif name == "error":
            fields.append((name, "device"))
        elif name.endswith("_error"):
            fields.append((name, name.removesuffix("_error").replace("_", "-")))
    return fields


def _plant_refusal(entry: dict[str, Any], field: str) -> None:
    """Plant one refusal field into a payload entry, as a real reader would.

    A reader writes a payload field or its ``*_error`` twin, never both, so the
    companion payload is dropped rather than left sitting beside a reason for
    its own absence. Planting ``error`` (the whole device refused) clears
    everything else on the entry, because nothing else was read either.

    Args:
        entry: The JSON object for one device's readings, mutated in place.
        field: The refusal field to plant, from :func:`_refusal_fields`.
    """
    if field == "error":
        for key in list(entry):
            del entry[key]
        entry["error"] = REFUSAL
        return
    entry.pop(field.removesuffix("_error"), None)
    entry[field] = REFUSAL


@dataclass(frozen=True)
class _RefusalCase:
    """One refusal field to plant, and where in a capture it lives.

    Attributes:
        kind: Which container in the capture holds the field - see
            :func:`_container_for`.
        fixture: The committed capture to copy and mutate.
        field: The capture-model field name to plant.
        reading: The reading name it must surface as, in ``skipped`` and in
            ``readings_refused``.
    """

    kind: str
    fixture: str
    field: str
    reading: str


def _container_for(data: dict[str, Any], kind: str) -> tuple[dict[str, Any], str]:
    """Find the JSON object one refusal field is planted into, and its subject.

    Args:
        data: The decoded capture.
        kind: Which shape of capture this is, from :class:`_RefusalCase`.

    Returns:
        The object to plant a refusal field into, and the node or path the
        planted device is keyed by in the capture (used only to pick a stable,
        already-existing device rather than to invent one).
    """
    if kind == "linux-ata":
        node = sorted(data["ata"])[0]
        return data["ata"][node], node
    if kind == "linux-nvme":
        node = sorted(data["nvme"])[0]
        return data["nvme"][node], node
    if kind == "linux-usb":
        # The disk's own device, the deepest on its chain: a refusal anywhere on
        # the chain leaves the link unread, and the disk's end is the one every
        # USB capture has.
        path = max(data["usb"], key=len)
        return data["usb"][path], path
    if kind == "windows-health":
        path = sorted(data["disks"])[0]
        return data["disks"][path]["ata"], path
    if kind == "windows-device":
        path = sorted(data["disks"])[0]
        return data["disks"][path], path
    msg = f"unknown case kind: {kind}"
    raise AssertionError(msg)


_CASES: tuple[_RefusalCase, ...] = (
    *(
        _RefusalCase(kind="linux-ata", fixture="linux-sas-hba.json", field=field, reading=reading)
        for field, reading in _refusal_fields(AtaBlobs)
    ),
    *(
        _RefusalCase(kind="linux-nvme", fixture="linux-nvme-board.json", field=field, reading=reading)
        for field, reading in _refusal_fields(NvmeBlobs)
    ),
    *(
        _RefusalCase(kind="linux-usb", fixture="linux-usb-ehci.json", field=field, reading=reading)
        for field, reading in _refusal_fields(UsbDeviceEntry, renamed={"bos_error": "usb-link"})
    ),
    *(
        _RefusalCase(kind="windows-health", fixture="windows-ahci.json", field=field, reading=reading)
        for field, reading in _refusal_fields(HealthBlobs)
    ),
    *(
        _RefusalCase(kind="windows-device", fixture="windows-ahci.json", field=field, reading=reading)
        for field, reading in _refusal_fields(DiskEntry)
    ),
)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("case", _CASES, ids=[f"{case.kind}:{case.field}" for case in _CASES])
def test_every_refusal_field_reaches_skipped_and_readings_refused(
    case: _RefusalCase,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """Every refusal field a capture model carries surfaces both ways.

    Parametrized over every ``*_error`` field :func:`_refusal_fields` reads off
    the disk capture models (never hand-listed), so a field one builder
    forgets to pass into ``refusals_of`` fails here rather than surviving a
    mutation pass silently. The read direction matters as much as the write:
    a field dropped between the typed capture and ``readings_refused`` is
    invisible to a test that only plants the JSON and checks it parses.
    """

    def plant(data: dict[str, Any]) -> None:
        entry, _ = _container_for(data, case.kind)
        _plant_refusal(entry, case.field)

    capture = capture_with(case.fixture, plant, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    named = refusal_lines(envelope)
    assert any(entry.startswith(f"{case.reading}: ") for entry in named), (
        f"{case.reading} is not in skipped: {envelope['skipped']}"
    )

    devices = [*envelope["data"]["disks"], *envelope["data"]["virtual_disks"]]
    carried = [
        refused
        for device in devices
        for refused in device["readings_refused"]
        if refused["reading"] == case.reading and refused["reason"] == REFUSAL
    ]
    assert carried, f"no disk carries {case.reading} in readings_refused: {devices}"


@pytest.mark.os_agnostic
def test_a_virtual_disks_refusal_is_named_in_skipped(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """A kernel-virtual device's refusal is named too, not only a physical one.

    ``_refusals_named`` walks ``(*inventory.disks, *inventory.virtual_disks)``,
    and the virtual half has no other test proving it is read: dropping it from
    that tuple would still pass every other test in this file, because none of
    them plants a refusal on a device the reader put under the kernel's
    virtual tree. ``loop0`` is such a device in ``linux-minimal.json``, and it
    is built through the same ``_build_ata_disk`` as a physical drive, so an
    ``ata`` entry keyed by its node plants a refusal on it exactly as it would
    on ``sda``.
    """

    def plant(data: dict[str, Any]) -> None:
        data["ata"]["loop0"] = {"error": REFUSAL}

    capture = capture_with("linux-minimal.json", plant, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    named = refusal_lines(envelope)
    assert any("loop0" in entry and entry.startswith("device: ") for entry in named), (
        f"the virtual device's refusal is not in skipped: {envelope['skipped']}"
    )

    virtual = [disk for disk in envelope["data"]["virtual_disks"] if disk["node"] == "loop0"]
    assert virtual, f"loop0 is not among the virtual disks: {envelope['data']['virtual_disks']}"
    assert virtual[0]["readings_refused"] == [{"reading": "device", "reason": REFUSAL}], virtual[0]["readings_refused"]


@pytest.mark.os_agnostic
def test_a_refused_smart_read_is_named_although_the_run_was_privileged(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The drive-behind-a-RAID-driver case: root, and the drive still says no."""

    def plant(data: dict[str, Any]) -> None:
        node = sorted(data["ata"])[0]
        data["ata"][node].pop("smart_data", None)
        data["ata"][node]["smart_data_error"] = REFUSAL

    capture = capture_with("linux-sas-hba.json", plant, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert envelope["ok"] is False, "a run that was refused a reading is not complete"
    named = refusal_lines(envelope)
    assert named, f"the refusal is not in skipped: {envelope['skipped']}"
    assert any("sd" in entry or "nvme" in entry for entry in named), f"no device is named: {named}"


@pytest.mark.os_agnostic
def test_a_device_that_could_not_be_opened_is_named(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The whole device refused, not one command on it: the reader writes ``error``."""

    def plant(data: dict[str, Any]) -> None:
        node = sorted(data["ata"])[0]
        data["ata"][node] = {"error": REFUSAL}

    capture = capture_with("linux-sas-hba.json", plant, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert envelope["ok"] is False
    assert refusal_lines(envelope), f"the refusal is not in skipped: {envelope['skipped']}"


@pytest.mark.os_agnostic
def test_a_refused_ahci_port_count_is_named(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The headline case: the register mapping is refused even to root.

    The registers are removed as well as the refusal planted, because that is
    what a refused read looks like: no values, and a reason for their absence.
    """

    def plant(data: dict[str, Any]) -> None:
        address = "0000:00:1f.2"
        data["pci"][address].pop("ahci", None)
        data["pci"][address]["ahci_error"] = REFUSAL

    capture = capture_with("linux-sas-hba.json", plant, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert envelope["ok"] is False
    named = refusal_lines(envelope)
    assert named, f"the refusal is not in skipped: {envelope['skipped']}"
    assert any("0000:00:1f.2" in entry for entry in named), f"the controller is not named: {named}"


@pytest.mark.os_agnostic
def test_a_refused_windows_passthrough_is_named(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The same rule on the other platform, whose reader records the same text.

    Windows is where the refusal is ordinary rather than exotic: a device opened
    without Administrator cannot be asked for IDENTIFY at all, and the reader
    already writes that sentence per drive.

    ``ok`` is deliberately not the assertion here: this capture is a QEMU guest,
    so it is already false for the hypervisor line and would pass whatever this
    change did. The refusal has to be found in ``skipped`` and on the drive.
    """

    def plant(data: dict[str, Any]) -> None:
        path = sorted(data["disks"])[0]
        data["disks"][path]["ata"] = {"identify_error": REFUSAL}

    capture = capture_with("windows-ahci.json", plant, tmp_path)
    envelope = envelope_of(capture, cli_runner, production_factory)

    assert refusal_lines(envelope), f"the refusal is not in skipped: {envelope['skipped']}"
    carried = [refused for disk in envelope["data"]["disks"] for refused in disk["readings_refused"]]
    assert carried == [{"reading": "identify", "reason": REFUSAL}], carried


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture", sorted(path.name for path in FIXTURES.glob("*.json")))
def test_a_complete_privileged_run_reports_ok_on_every_committed_capture(
    fixture: str,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """The direction the reverted attempt broke: healthy hardware stays complete.

    Every committed capture was taken as root and recorded no refusal, so none
    may grow a refusal entry. Asserted two ways rather than on ``skipped == []``,
    which windows-ahci legitimately fails: it is a QEMU guest, so the
    hypervisor-suppression line is there for a reason that has nothing to do with
    this rule. A refusal entry is recognised by naming its SUBJECT, which the
    privilege and hypervisor lines never do and every refusal line always does.

    Keyed on the captures themselves rather than a list, so a capture added later
    is covered.
    """
    envelope = envelope_of(FIXTURES / fixture, cli_runner, production_factory)
    data = envelope["data"]

    carried = [
        device["path"] or device["node"]
        for device in (*data["disks"], *data["virtual_disks"])
        if device.get("readings_refused")
    ]
    carried += [controller["address"] for controller in data["controllers"] if controller.get("readings_refused")]
    assert not carried, f"{fixture} carries a refusal it never recorded: {carried}"

    subjects = [device["path"] for device in data["disks"]]
    subjects += [controller["address"] for controller in data["controllers"]]
    named = [entry for entry in envelope["skipped"] if any(subject in entry for subject in subjects)]
    assert not named, f"{fixture} names a device in skipped without a recorded refusal: {named}"


@pytest.mark.os_linux
@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root is allowed to open the region, so nothing is refused")
def test_a_denied_register_mapping_is_recorded_with_the_reason_the_kernel_gave(tmp_path: Path) -> None:
    """The reader's own half: a region it may not open comes back as a refusal.

    Driven against a file the test makes unreadable rather than against real
    hardware, because the branch under test is the kernel saying no - which is
    what an unprivileged run and a locked-down root run both meet, and what no
    committed capture can carry. Skipped as root because root is allowed to open
    it, so the arm would pass by reading zeroes instead of by being refused.
    """
    from lsdsk.adapters.hw.linux.reader import read_ahci_capabilities

    device = tmp_path / "0000:00:1f.2"
    device.mkdir()
    (device / "resource5").write_bytes(bytes(0x1000))
    (device / "resource5").chmod(0o000)

    reading = read_ahci_capabilities(device)

    assert reading.registers is None
    assert reading.refused is not None and "denied" in reading.refused.lower(), reading.refused


@pytest.mark.os_linux
def test_a_controller_with_no_such_region_records_no_refusal(tmp_path: Path) -> None:
    """The discrimination that keeps this from crying wolf.

    A controller exposing no BAR5 has nothing to give, and the scan is not
    incomplete for it. Without this arm the refusal branch could report every
    controller in the machine and still look correct.
    """
    from lsdsk.adapters.hw.linux.reader import read_ahci_capabilities

    device = tmp_path / "0000:00:17.0"
    device.mkdir()

    reading = read_ahci_capabilities(device)

    assert reading.registers is None
    assert reading.refused is None


@pytest.mark.os_linux
def test_a_region_too_small_to_hold_the_registers_records_no_refusal(tmp_path: Path) -> None:
    """A BAR5 shorter than the two registers is a region with nothing to give, not a refusal.

    The missing-file arm above does not reach this branch: it stops at ``stat``.
    The control maps a region of the full span with both registers planted and
    requires them back, so the subject cannot pass by the reader returning
    nothing for every region.
    """
    from lsdsk.adapters.hw.decode import ahci
    from lsdsk.adapters.hw.linux.reader import read_ahci_capabilities

    full = tmp_path / "0000:00:1f.2"
    full.mkdir()
    registers = bytearray(0x1000)
    registers[ahci.CAPABILITY_OFFSET : ahci.CAPABILITY_OFFSET + 4] = (0xE7234F05).to_bytes(4, "little")
    registers[ahci.PORTS_IMPLEMENTED_OFFSET : ahci.PORTS_IMPLEMENTED_OFFSET + 4] = (0x3).to_bytes(4, "little")
    (full / "resource5").write_bytes(bytes(registers))
    control = read_ahci_capabilities(full)
    assert control.registers == {"capability": 0xE7234F05, "ports_implemented": 0x3}, control
    assert control.refused is None

    short = tmp_path / "0000:00:17.0"
    short.mkdir()
    (short / "resource5").write_bytes(bytes(ahci.REGISTER_SPAN - 1))

    reading = read_ahci_capabilities(short)

    assert reading.registers is None
    assert reading.refused is None


@pytest.mark.os_linux
def test_a_mapping_the_kernel_denies_is_recorded_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A region that opens but will not map is a refusal, not a controller with no BAR5.

    The open-denied arm above cannot reach this branch, and it is skipped as root,
    which is exactly the user that meets a denied MAPPING under kernel lockdown.
    ``mmap.mmap`` is replaced because the kernel is the only thing that refuses a
    mapping of a file the caller could open; it is the external edge here.
    """
    from lsdsk.adapters.hw.linux.reader import read_ahci_capabilities

    device = tmp_path / "0000:00:1f.2"
    device.mkdir()
    (device / "resource5").write_bytes(bytes(0x1000))

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(mmap, "mmap", refuse)

    reading = read_ahci_capabilities(device)

    assert reading.registers is None
    assert reading.refused is not None and "not permitted" in reading.refused, reading.refused
