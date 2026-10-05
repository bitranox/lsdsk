"""Disks come out in the order a reader counts them in, on both platforms.

A plain string compare puts ``PhysicalDrive10`` before ``PhysicalDrive2`` and
``sdaa`` before ``sdz``, so the twenty-seventh SCSI disk was listed second.
"""

from __future__ import annotations

from lsdsk.domain.disk_name import disk_name_order


def _ordered(*names: str) -> list[str]:
    return sorted(names, key=disk_name_order)


def test_a_windows_drive_number_is_compared_as_a_number() -> None:
    names = [rf"\\.\PhysicalDrive{n}" for n in (10, 2, 1, 0)]
    assert _ordered(*names) == [rf"\\.\PhysicalDrive{n}" for n in (0, 1, 2, 10)]


def test_a_linux_disk_letter_counts_on_past_z() -> None:
    assert _ordered("sdaa", "sdz", "sdb", "sda", "sdab") == ["sda", "sdb", "sdz", "sdaa", "sdab"]


def test_both_numbers_in_an_nvme_name_are_compared_as_numbers() -> None:
    assert _ordered("nvme10n1", "nvme1n10", "nvme1n2", "nvme2n1") == ["nvme1n2", "nvme1n10", "nvme2n1", "nvme10n1"]


def test_disk_families_keep_the_order_a_plain_compare_gave_them() -> None:
    # Only the numbering inside a family changes; which family comes first
    # does not, so no existing listing reorders its families.
    assert _ordered("sda", "nvme0n1", "vda", "mmcblk0") == ["mmcblk0", "nvme0n1", "sda", "vda"]


def test_a_virtio_or_xen_disk_letter_counts_on_past_z_too() -> None:
    assert _ordered("vdaa", "vdz", "xvdaa", "xvdz") == ["vdz", "vdaa", "xvdz", "xvdaa"]


def test_distinct_names_never_share_a_key() -> None:
    names = ["sda", "sda1", "sdaa", "nvme0n1", "nvme0n1p1", "nvme00n1", r"\\.\PhysicalDrive0", "", "zram0"]
    assert len({disk_name_order(name) for name in names}) == len(names)
