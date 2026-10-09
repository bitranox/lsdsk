"""A temperature outside what a drive can physically report is not a reading.

A monitor publishing ``99999999999999999`` showed ``100000000000000C``, and an
NVMe log page of all 0xFF bytes (a bus that answers with floating lines)
decoded to 65262 C. Both are a device saying nothing true, so the field stays
unmeasured, exactly like a drive that published none.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.hw.decode.nvme import decode_smart_log
from lsdsk.adapters.hw.decode.temperature import MAX_PLAUSIBLE_CELSIUS, MIN_PLAUSIBLE_CELSIUS
from lsdsk.adapters.hw.linux.builder import build_disks
from lsdsk.adapters.hw.linux.capture import BlockEntry, HwmonEntry, LinuxCapture, SysfsClasses
from lsdsk.domain.enums import Platform

BASE = "/sys/devices/pci0000:00/0000:04:00.0/nvme/nvme0"
KELVIN_OFFSET = 273


def _disks_with(*monitors: HwmonEntry):
    capture = LinuxCapture.model_validate(
        {
            "schema": 2,
            "platform": Platform.LINUX,
            "hostname": "crafted",
            "kernel": "6.1.0",
            "pci": {},
            "block": {
                "nvme0n1": BlockEntry(device_path=BASE, size="1024", hwmon=tuple(m.path for m in monitors)),
            },
            "classes": SysfsClasses(hwmon={f"hwmon{n}": m for n, m in enumerate(monitors)}),
        }
    )
    (disk,) = build_disks(capture)
    return disk


@pytest.mark.os_agnostic
@pytest.mark.parametrize("millidegrees", ["99999999999999999", "9" * 400, "-99999999", str((MAX_PLAUSIBLE_CELSIUS + 1) * 1000)])
def test_an_implausible_hwmon_temperature_is_not_a_reading(millidegrees: str) -> None:
    disk = _disks_with(HwmonEntry(path=f"{BASE}/hwmon0", temp1_input=millidegrees))
    assert disk.health is None or disk.health.temperature_c is None


@pytest.mark.os_agnostic
def test_an_implausible_monitor_does_not_shadow_the_next_one() -> None:
    disk = _disks_with(
        HwmonEntry(path=f"{BASE}/hwmon0", temp1_input="99999999999999999"),
        HwmonEntry(path=f"{BASE}/hwmon1", temp1_input="42000"),
    )
    assert disk.health is not None
    assert disk.health.temperature_c == 42


@pytest.mark.os_agnostic
@pytest.mark.parametrize("celsius", [MIN_PLAUSIBLE_CELSIUS, 0, 85, MAX_PLAUSIBLE_CELSIUS])
def test_the_edges_of_the_plausible_range_are_still_readings(celsius: int) -> None:
    disk = _disks_with(HwmonEntry(path=f"{BASE}/hwmon0", temp1_input=str(celsius * 1000)))
    assert disk.health is not None
    assert disk.health.temperature_c == celsius


@pytest.mark.os_agnostic
def test_an_all_ones_nvme_log_page_has_no_temperature() -> None:
    assert decode_smart_log(b"\xff" * 512).temperature_c is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("celsius", [MIN_PLAUSIBLE_CELSIUS, 38, MAX_PLAUSIBLE_CELSIUS])
def test_a_plausible_nvme_composite_temperature_is_kept(celsius: int) -> None:
    page = bytearray(512)
    page[3] = 100  # a page that is not all zeros
    page[1:3] = (celsius + KELVIN_OFFSET).to_bytes(2, "little")
    assert decode_smart_log(bytes(page)).temperature_c == celsius
