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
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

# Planted in the capture as the text the OS gave, and asserted in the envelope:
# the caller gets the reason, not a flag saying something was missing.
REFUSAL = "[Errno 1] Operation not permitted (planted)"


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
@pytest.mark.skipif(os.geteuid() == 0, reason="root is allowed to open the region, so nothing is refused")
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
