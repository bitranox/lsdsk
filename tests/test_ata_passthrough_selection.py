"""Which disks the Linux reader sends ATA passthrough to.

A native SAS drive is not an ATA device. Sent ATA IDENTIFY and SMART through
SG_IO it refuses all three, the reader records each as a refusal, and a
privileged run on a SAS box reported ``ok: false`` forever over readings that
were never there to take. The drive is now left out of the ATA read - and so
records nothing - when it hangs off a SAS ``end_device`` AND its own sysfs
``vendor`` is not ``ATA``.

Both halves are needed. The vendor alone would skip a USB disk: a SAT bridge
reports the BRIDGE's vendor and still answers ATA passthrough. The end device
alone would skip every SATA drive behind a SAS HBA, which the HBA's SATL
presents with vendor ``ATA`` and which answers IDENTIFY like any other.

The captures here are built by the real reader over a synthetic sysfs tree and
then replayed through the CLI, so the assertion is on the ``skipped`` list a
caller reads. The device nodes are named so no machine has them: the disks the
reader does send through passthrough fail to open and are named in ``skipped``,
which is what proves they were sent, and the SAS drive is not.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.hw.linux.reader import ata_passthrough_nodes, read_ata_blobs, read_block

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

pytestmark = pytest.mark.os_posix

_HBA = "devices/pci0000:00/0000:00:03.0/0000:03:00.0/host6"
_USB = "devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host8/target8:0:0/8:0:0:0"


def _disk(sysfs: Path, device: str, node: str, *, vendor: str | None) -> None:
    """One SCSI disk whose ``device`` directory is ``device``, linked from ``/sys/block``."""
    device_dir = sysfs / device
    target = device_dir / "block" / node
    (target / "queue").mkdir(parents=True)
    (target / "size").write_text("7814037168\n", encoding="utf-8")
    (target / "device").symlink_to(device_dir)
    (device_dir / "model").write_text("A Model\n", encoding="utf-8")
    if vendor is not None:
        (device_dir / "vendor").write_text(f"{vendor}\n", encoding="utf-8")
    block = sysfs / "block"
    block.mkdir(exist_ok=True)
    (block / node).symlink_to(target)


def _behind_sas(port: int) -> str:
    """The SCSI device of a drive on one HBA port."""
    return f"{_HBA}/port-6:{port}/end_device-6:{port}/target6:0:{port}/6:0:{port}:0"


@pytest.fixture
def sysfs(tmp_path: Path) -> Path:
    """A native SAS drive, a SATA drive behind the same HBA, a USB disk, and a SAS drive with no vendor read."""
    root = tmp_path / "sys"
    _disk(root, _behind_sas(0), "sdzza", vendor="SEAGATE ")
    _disk(root, _behind_sas(1), "sdzzb", vendor="ATA     ")
    _disk(root, _USB, "sdzzc", vendor="Samsung ")
    _disk(root, _behind_sas(2), "sdzzd", vendor=None)
    return root


def _skipped(block: dict[str, Any], tmp_path: Path, runner: CliRunner, factory: Callable[[], object]) -> list[str]:
    """Replay a privileged reading of ``block`` through ``health`` and return its ``skipped`` list."""
    from lsdsk.adapters.cli import cli

    reading = {
        "schema": 2,
        "platform": "linux",
        "hostname": "sas-box",
        "kernel": "6.8.0",
        "euid": 0,
        "pci": {},
        "block": block,
        "ata": read_ata_blobs(ata_passthrough_nodes(block)),
    }
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(reading), encoding="utf-8")
    output = runner.invoke(cli, ["health", "--replay", str(capture), "--format", "json"], obj=factory).output
    envelope: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(envelope, dict)
    assert "skipped" in envelope, output
    return cast("list[str]", cast("dict[str, Any]", envelope)["skipped"])


def test_a_native_sas_drive_is_not_sent_ata_passthrough(sysfs: Path) -> None:
    assert "sdzza" not in ata_passthrough_nodes(read_block(sysfs / "block"))


def test_a_sata_drive_behind_a_sas_hba_a_usb_disk_and_an_unread_vendor_are_still_read(sysfs: Path) -> None:
    """The control: the rule must not swallow any disk that can answer, or one it cannot judge."""
    assert {"sdzzb", "sdzzc", "sdzzd"} <= set(ata_passthrough_nodes(read_block(sysfs / "block")))


def test_a_privileged_run_names_no_refusal_for_a_native_sas_drive(
    sysfs: Path, tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    skipped = _skipped(read_block(sysfs / "block"), tmp_path, cli_runner, production_factory)

    assert not [entry for entry in skipped if "/dev/sdzza" in entry], skipped
    # The disks that WERE sent through passthrough could not be opened here, and
    # say so - which is what proves the replay reached the skipped list at all.
    assert [entry for entry in skipped if "/dev/sdzzb" in entry], skipped
    assert [entry for entry in skipped if "/dev/sdzzc" in entry], skipped
