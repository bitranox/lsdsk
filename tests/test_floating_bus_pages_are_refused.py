"""A page of 0xFF bytes is a bus that answered with floating lines, not a drive.

Decoded as data, an all-ones NVMe SMART log is a drive that reports itself as
failing, 255 percent through its rated endurance, with 10^32 bytes written, and
an all-ones ATA SMART page is a table of thirty attributes below their
thresholds. The same bytes in an Identify Controller page name a controller of
U+FFFD characters. Each page is refused the way an unusable IDENTIFY page
already is, so the disk shows no figure rather than an alarming one.

These drive the real replay over a copy of a committed capture with the page
planted, and read the `findings` envelope, because that output is where the
wrong answer reached a person.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.hw.decode.ata_smart import SMART_DATA_LENGTH, decode_attributes, decode_health
from lsdsk.adapters.hw.decode.nvme import (
    IDENTIFY_CONTROLLER_LENGTH,
    SMART_LOG_LENGTH,
    decode_identify_controller,
    decode_smart_log,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
ALARMS = ("failing", "endurance", "wear", "written")


def _ones(length: int) -> str:
    return base64.b64encode(b"\xff" * length).decode("ascii")


def _findings_of(
    fixture: str,
    plant: Callable[[dict[str, Any]], None],
    tmp_path: Path,
    runner: CliRunner,
    factory: Callable[[], object],
) -> dict[str, Any]:
    from lsdsk.adapters.cli import cli

    data = cast("dict[str, Any]", json.loads((FIXTURES / fixture).read_text(encoding="utf-8")))
    plant(data)
    capture = tmp_path / fixture
    capture.write_text(json.dumps(data), encoding="utf-8")
    output = runner.invoke(cli, ["findings", "--replay", str(capture), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)["data"]


def _alarms_about(data: dict[str, Any], node: str) -> list[str]:
    return [
        finding["title"]
        for finding in data["findings"]
        if finding["subject"] == f"/dev/{node}" and any(word in finding["title"].lower() for word in ALARMS)
    ]


def _health_of(data: dict[str, Any], node: str) -> dict[str, Any] | None:
    (disk,) = [disk for disk in data["disks"] if disk["node"] == node]
    return cast("dict[str, Any] | None", disk["health"])


@pytest.mark.os_agnostic
def test_an_all_ones_nvme_smart_log_reports_no_alarm_and_no_figure(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    def plant(data: dict[str, Any]) -> None:
        data["nvme"]["nvme0n1"]["smart_log"] = _ones(SMART_LOG_LENGTH)

    data = _findings_of("linux-nvme-board.json", plant, tmp_path, cli_runner, production_factory)

    assert _alarms_about(data, "nvme0n1") == []
    health = _health_of(data, "nvme0n1")
    assert health is None or (health["ok"] is None and health["percent_used"] is None)


@pytest.mark.os_agnostic
def test_the_alarm_check_can_see_a_real_failing_nvme_drive(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: the same harness DOES report a drive whose critical warning is set."""

    def plant(data: dict[str, Any]) -> None:
        page = bytearray(base64.b64decode(data["nvme"]["nvme0n1"]["smart_log"]))
        page[0] = 0x04
        data["nvme"]["nvme0n1"]["smart_log"] = base64.b64encode(bytes(page)).decode("ascii")

    data = _findings_of("linux-nvme-board.json", plant, tmp_path, cli_runner, production_factory)

    assert _alarms_about(data, "nvme0n1")


@pytest.mark.os_agnostic
def test_an_all_ones_smart_log_leaves_the_monitors_temperature_in_place(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    def plant(data: dict[str, Any]) -> None:
        data["nvme"]["nvme0n1"]["smart_log"] = _ones(SMART_LOG_LENGTH)

    data = _findings_of("linux-nvme-board.json", plant, tmp_path, cli_runner, production_factory)
    health = _health_of(data, "nvme0n1")

    assert health is not None, "the hwmon monitor still published a temperature"
    assert health["temperature_c"] is not None


@pytest.mark.os_agnostic
def test_an_all_ones_ata_smart_page_reports_no_alarm_and_no_attributes(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    def plant(data: dict[str, Any]) -> None:
        data["ata"]["sda"]["smart_data"] = _ones(SMART_DATA_LENGTH)

    data = _findings_of("linux-nvme-board.json", plant, tmp_path, cli_runner, production_factory)

    assert _alarms_about(data, "sda") == []
    health = _health_of(data, "sda")
    assert health is None or health["attributes"] == []


@pytest.mark.os_agnostic
def test_an_all_ones_identify_controller_names_no_controller(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    def plant(data: dict[str, Any]) -> None:
        data["nvme"]["nvme0n1"]["identify_controller"] = _ones(IDENTIFY_CONTROLLER_LENGTH)

    data = _findings_of("linux-nvme-board.json", plant, tmp_path, cli_runner, production_factory)
    (disk,) = [disk for disk in data["disks"] if disk["node"] == "nvme0n1"]

    assert "�" not in disk["model"]
    assert "�" not in (disk["serial"] or "")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "refuse",
    [
        lambda: decode_smart_log(b"\xff" * SMART_LOG_LENGTH),
        lambda: decode_attributes(b"\xff" * SMART_DATA_LENGTH),
        lambda: decode_health(b"\xff" * SMART_DATA_LENGTH),
        lambda: decode_identify_controller(b"\xff" * IDENTIFY_CONTROLLER_LENGTH),
    ],
    ids=["smart-log", "ata-attributes", "ata-health", "identify-controller"],
)
def test_each_decoder_refuses_an_all_ones_page(refuse: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="all ones"):
        refuse()
