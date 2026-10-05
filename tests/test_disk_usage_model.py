"""What a disk's usage can say, and what an absent one means."""

from __future__ import annotations

import pytest

from lsdsk.domain.enums import UseKind
from lsdsk.domain.models import Disk, DiskUsage, DiskUse


@pytest.mark.os_agnostic
def test_a_disk_built_without_a_usage_reading_says_it_was_not_read() -> None:
    assert Disk(node="sda", path="/dev/sda", model="m").usage is None


@pytest.mark.os_agnostic
def test_a_usage_carries_the_boot_mark_and_its_uses_in_order() -> None:
    usage = DiskUsage(
        boot=True,
        uses=(DiskUse(kind=UseKind.ZFS, name="rpool"), DiskUse(kind=UseKind.SWAP)),
    )
    disk = Disk(node="sde", path="/dev/sde", model="m", usage=usage)
    assert disk.usage is not None
    assert disk.usage.boot is True
    assert [use.kind for use in disk.usage.uses] == [UseKind.ZFS, UseKind.SWAP]


@pytest.mark.os_agnostic
def test_a_use_refuses_a_field_it_does_not_declare() -> None:
    with pytest.raises(ValueError, match="mountpoint"):
        DiskUse(kind=UseKind.MOUNT, mountpoint="/")  # type: ignore[call-arg]


@pytest.mark.os_agnostic
def test_the_usage_reaches_the_json_payload_by_name() -> None:
    usage = DiskUsage(uses=(DiskUse(kind=UseKind.LETTER, mounts=("C:\\",)),))
    dumped = Disk(node="PhysicalDrive0", path="p", model="m", usage=usage).model_dump(mode="json")
    assert dumped["usage"] == {"boot": False, "uses": [{"kind": "letter", "name": "", "mounts": ["C:\\"]}]}
