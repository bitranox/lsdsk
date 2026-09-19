"""The Linux builder's per-disk lookups are built once per capture, not per disk.

Three of them used to walk a whole capture-controlled map for every disk, so a
capture cost the PRODUCT of its disks and its class entries. The arms below are
scaling arms rather than wall-clock ones: they double a capture and require the
work to roughly double with it, because the claim is about complexity and a
figure that passes on this machine says nothing about a slower one.
"""

from __future__ import annotations

import time

import pytest

from lsdsk.adapters.hw.linux.builder import build_disks
from lsdsk.adapters.hw.linux.capture import (
    AtaLinkEntry,
    BlockEntry,
    HwmonEntry,
    LinuxCapture,
    NvmeClassEntry,
    SysfsClasses,
)
from lsdsk.domain.enums import Platform

#: Enough disks for a per-disk scan to dominate and few enough for an indexed
#: build to stay instant. Measured before the fix, 2,000 against 4,000 disks
#: took 0.18s and 0.69s on the ATA arm and 0.16s and 0.60s on the NVMe one.
_SCALED_DISK_COUNT = 2_000

#: How many times the doubled capture may cost the single one. A per-disk scan
#: of a per-capture map quadruples on a doubling and measured 3.8 on both arms;
#: an indexed lookup doubles. Three sits between the two, nearer the defect than
#: the fix so a slow runner's noise cannot fail it.
_ACCEPTABLE_GROWTH = 3.0


def _ata_capture(count: int) -> LinuxCapture:
    """`count` SATA disks, each hanging off a libata link of its own."""
    return _capture(
        block={
            f"sd{index:05d}": BlockEntry(
                device_path=f"/sys/devices/pci0000:00/0000:00:17.0/ata{index}/host{index}/target{index}:0:0",
                size="1000215216",
            )
            for index in range(count)
        },
        classes=SysfsClasses(
            ata_link={
                f"link{index}": AtaLinkEntry(
                    path=f"/sys/devices/pci0000:00/0000:00:17.0/ata{index}/link{index}/ata_link/link{index}",
                    sata_spd="6.0 Gbps",
                    sata_spd_max="6.0 Gbps",
                )
                for index in range(count)
            }
        ),
    )


def _nvme_capture(count: int) -> LinuxCapture:
    """`count` NVMe namespaces, each with a class entry and a hardware monitor."""
    controller = "/sys/devices/pci0000:00/0000:{bus:02x}:00.0/nvme/nvme{index}"
    return _capture(
        block={
            f"nvme{index}n1": BlockEntry(
                device_path=controller.format(bus=index // 256, index=index),
                size="1000215216",
                hwmon=(controller.format(bus=index // 256, index=index) + f"/hwmon{index}",),
            )
            for index in range(count)
        },
        classes=SysfsClasses(
            nvme={
                f"nvme{index}": NvmeClassEntry(
                    path=controller.format(bus=index // 256, index=index),
                    model=f"model {index}",
                    serial=f"serial {index}",
                )
                for index in range(count)
            },
            hwmon={
                f"hwmon{index}": HwmonEntry(
                    path=controller.format(bus=index // 256, index=index) + f"/hwmon{index}",
                    temp1_input="35000",
                )
                for index in range(count)
            },
        ),
    )


def _capture(*, block: dict[str, BlockEntry], classes: SysfsClasses) -> LinuxCapture:
    """A Linux capture carrying nothing but the sections these arms scale."""
    return LinuxCapture.model_validate(
        {
            "schema": 2,
            "platform": Platform.LINUX,
            "hostname": "crafted",
            "kernel": "6.1.0",
            "pci": {},
            "block": block,
            "classes": classes,
        }
    )


def _seconds_to_build(capture: LinuxCapture, expected: int, runs: int = 2) -> float:
    """The FASTEST of several builds of one capture.

    The fastest rather than the mean, for the reason the fabric's arms take it:
    a one-off pause on a shared runner only ever inflates a reading, and a pause
    in the smaller arm would make the growth below look better than it is.
    """
    measured: list[float] = []
    for _run in range(runs):
        started = time.perf_counter()
        disks = build_disks(capture)
        measured.append(time.perf_counter() - started)
        assert len(disks) == expected, f"the build produced {len(disks)} of {expected} disks, so the timing is wrong"
    return min(measured)


@pytest.mark.os_agnostic
def test_doubling_a_sata_capture_does_not_quadruple_what_it_costs_to_build() -> None:
    """`_ata_link_for` used to scan every libata link in the capture, per disk.

    Measured at 4,000 disks before the fix: 93 percent of `build_disks` sat in
    that scan, including 8,010,000 re-evaluations of one regex group inside its
    own generator. A capture is untrusted input admitted up to 64 MB, which is
    hundreds of thousands of entries, so the ceiling on the product is hours.
    """
    single = _seconds_to_build(_ata_capture(_SCALED_DISK_COUNT), _SCALED_DISK_COUNT)
    doubled = _seconds_to_build(_ata_capture(_SCALED_DISK_COUNT * 2), _SCALED_DISK_COUNT * 2)

    assert single > 0, "the smaller arm measured no time at all, so the growth below means nothing"
    assert doubled < single * _ACCEPTABLE_GROWTH, (
        f"doubling {_SCALED_DISK_COUNT} SATA disks took {doubled:.3f}s against {single:.3f}s, "
        f"a growth of {doubled / single:.1f} where a linear build grows by 2"
    )


@pytest.mark.os_agnostic
def test_doubling_an_nvme_capture_does_not_quadruple_what_it_costs_to_build() -> None:
    """`_nvme_class_entry` and `_hwmon_temperature` scanned their maps per disk.

    Measured at 4,000 disks before the fix: 85 percent of `build_disks` sat in
    the class-entry scan, over 15,940,134 prefix comparisons, and the hardware
    monitors added a scan of their own on top of it.
    """
    single = _seconds_to_build(_nvme_capture(_SCALED_DISK_COUNT), _SCALED_DISK_COUNT)
    doubled = _seconds_to_build(_nvme_capture(_SCALED_DISK_COUNT * 2), _SCALED_DISK_COUNT * 2)

    assert single > 0, "the smaller arm measured no time at all, so the growth below means nothing"
    assert doubled < single * _ACCEPTABLE_GROWTH, (
        f"doubling {_SCALED_DISK_COUNT} NVMe namespaces took {doubled:.3f}s against {single:.3f}s, "
        f"a growth of {doubled / single:.1f} where a linear build grows by 2"
    )


@pytest.mark.os_agnostic
def test_a_disk_on_ata1_is_not_given_the_link_of_ata10() -> None:
    """The port token is the whole number between separators, never a prefix of one.

    Both ports are real on `linux-nvme-board`, which carries `ata1` and `ata10`
    on the same board. A lookup keyed on anything looser than the delimited
    token hands the disk on port 1 the tenth port's negotiated speed, which
    reads as a measurement rather than as a mix-up.
    """
    capture = _capture(
        block={
            "sda": BlockEntry(device_path="/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0", size="1024")
        },
        classes=SysfsClasses(
            ata_link={
                "link10": AtaLinkEntry(
                    path="/sys/devices/pci0000:00/0000:00:17.0/ata10/link10/ata_link/link10",
                    sata_spd="1.5 Gbps",
                    sata_spd_max="1.5 Gbps",
                )
            }
        ),
    )

    (disk,) = build_disks(capture)

    assert disk.link.negotiated_gbps is None, "the disk on ata1 took the link belonging to ata10"


@pytest.mark.os_agnostic
def test_a_link_path_carrying_two_port_tokens_is_reachable_by_both() -> None:
    """Every token in a path is indexed, not just the first the scanner can consume.

    A capture is untrusted input, so a path holding two tokens back to back is
    something to handle rather than assume away. The substring test this index
    replaces found either of them; a pattern that CONSUMES its trailing
    separator finds only the first, because the second needs that separator as
    its own leading one, and the disk asking for it would silently get no link.
    """
    shared = AtaLinkEntry(path="/sys/devices/pci0000:00/ata1/ata2/link1", sata_spd="6.0 Gbps", sata_spd_max="6.0 Gbps")
    capture = _capture(
        block={
            "sda": BlockEntry(device_path="/sys/devices/pci0000:00/ata1/host0", size="1024"),
            "sdb": BlockEntry(device_path="/sys/devices/pci0000:00/ata2/host1", size="1024"),
        },
        classes=SysfsClasses(ata_link={"link1": shared}),
    )

    first, second = build_disks(capture)

    assert first.link.negotiated_gbps == 6.0, "the disk on the first token got no link"
    assert second.link.negotiated_gbps == 6.0, "the disk on the second token got no link"


@pytest.mark.os_agnostic
def test_the_first_link_in_capture_order_wins_a_port_two_of_them_claim() -> None:
    """Two links on one port resolve to the earlier entry, as the scan did.

    The scan returned the first match in capture order. An index that let a
    later entry overwrite an earlier one would answer differently for a capture
    that is otherwise built and rendered the same, so the tie-break is pinned
    rather than left to whichever way the index happens to be written.
    """
    capture = _capture(
        block={"sda": BlockEntry(device_path="/sys/devices/pci0000:00/ata3/host0", size="1024")},
        classes=SysfsClasses(
            ata_link={
                "link3": AtaLinkEntry(path="/sys/devices/pci0000:00/ata3/link3", sata_spd="6.0 Gbps"),
                "link3-again": AtaLinkEntry(path="/sys/devices/pci0000:00/ata3/link3b", sata_spd="1.5 Gbps"),
            }
        ),
    )

    (disk,) = build_disks(capture)

    assert disk.link.negotiated_gbps == 6.0, "a later entry overwrote the first link on the port"


@pytest.mark.os_agnostic
def test_a_namespace_takes_the_class_entry_of_the_controller_above_it() -> None:
    """The class entry sits at the controller, which is an ancestor of a namespace.

    Every committed capture resolves a namespace's `device_path` to the
    controller itself, so the equal case is the one they cover. This is the
    other shape the lookup has always accepted: the reader resolves the path to
    the namespace directory, and the entry that names the drive is one level up.
    Without it an unprivileged run shows three dashes where sysfs published the
    model, serial and firmware all along.
    """
    controller = "/sys/devices/pci0000:00/0000:04:00.0/nvme/nvme0"
    capture = _capture(
        block={"nvme0n1": BlockEntry(device_path=f"{controller}/nvme0n1", size="1024")},
        classes=SysfsClasses(
            nvme={"nvme0": NvmeClassEntry(path=controller, model="published model", serial="published serial")}
        ),
    )

    (disk,) = build_disks(capture)

    assert disk.model == "published model"
    assert disk.serial == "published serial"


@pytest.mark.os_agnostic
def test_a_namespace_does_not_take_a_class_entry_from_below_its_own_device_path() -> None:
    """An entry UNDER the device path names something the namespace contains.

    The scan this replaces tested a raw string prefix in both directions, so an
    entry deeper than the device path was accepted as the controller the disk
    hangs off - and, being a raw string prefix rather than a path one, an entry
    at `nvme01` was accepted for a device path of `nvme0`, which is a different
    controller. Neither direction occurs in any committed capture; both are
    refused here, and a drive whose controller published nothing is reported as
    having published nothing.
    """
    capture = _capture(
        block={"nvme0n1": BlockEntry(device_path="/sys/devices/pci0000:00/0000:04:00.0/nvme/nvme0", size="1024")},
        classes=SysfsClasses(
            nvme={
                "nvme01": NvmeClassEntry(
                    path="/sys/devices/pci0000:00/0000:04:00.0/nvme/nvme01", model="a different controller"
                )
            }
        ),
    )

    (disk,) = build_disks(capture)

    assert disk.model == "nvme0n1", f"the namespace claimed {disk.model!r} from a controller that is not its own"


@pytest.mark.os_agnostic
def test_a_disk_owning_two_monitors_reads_the_one_earlier_in_the_capture() -> None:
    """Capture order decides, not the order the device lists its monitors.

    The scan walked the capture's monitors and returned the first whose path the
    device owned, so a device listing them the other way round still read the
    earlier entry. A lookup that walked the device's own list instead would
    answer differently on the same capture.
    """
    base = "/sys/devices/pci0000:00/0000:04:00.0/nvme/nvme0"
    capture = _capture(
        block={"nvme0n1": BlockEntry(device_path=base, size="1024", hwmon=(f"{base}/hwmon9", f"{base}/hwmon1"))},
        classes=SysfsClasses(
            hwmon={
                "hwmon1": HwmonEntry(path=f"{base}/hwmon1", temp1_input="31000"),
                "hwmon9": HwmonEntry(path=f"{base}/hwmon9", temp1_input="88000"),
            }
        ),
    )

    (disk,) = build_disks(capture)

    assert disk.health is not None
    assert disk.health.temperature_c == 31, "the device's own listing order decided the reading"


@pytest.mark.os_agnostic
def test_a_monitor_with_an_unreadable_temperature_does_not_shadow_the_next_one() -> None:
    """An entry that published nothing usable is walked past, not given up on.

    The scan continued to the next monitor the device owned; an index that
    stored the unreadable entry as the answer for its path would report no
    temperature for a drive that published one on its second monitor.
    """
    base = "/sys/devices/pci0000:00/0000:04:00.0/nvme/nvme0"
    capture = _capture(
        block={"nvme0n1": BlockEntry(device_path=base, size="1024", hwmon=(f"{base}/hwmon0", f"{base}/hwmon1"))},
        classes=SysfsClasses(
            hwmon={
                "hwmon0": HwmonEntry(path=f"{base}/hwmon0", temp1_input=None),
                "hwmon1": HwmonEntry(path=f"{base}/hwmon1", temp1_input="42000"),
            }
        ),
    )

    (disk,) = build_disks(capture)

    assert disk.health is not None
    assert disk.health.temperature_c == 42, "an unreadable monitor hid the one that answered"
