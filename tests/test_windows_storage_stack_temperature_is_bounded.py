"""The Windows storage-stack temperature is held to the bound every other path uses.

The fallback reads a temperature, a warning and a critical threshold, each a
signed 16-bit value the driver fills in. A driver with no sensor to report, or a
bus answering garbage, leaves values such as -32768, 150, 32767 or 0; shown as a
reading, one of them is also a critical finding and exit status 1. An
implausible value is unread, whichever of the three fields carries it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.hw.decode.captured import MAX_PLAUSIBLE_CELSIUS, MIN_PLAUSIBLE_CELSIUS

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"


def _data_with(temperature: dict[str, int], tmp_path: Path, runner: CliRunner, factory: Callable[[], object]) -> Any:
    from lsdsk.adapters.cli import cli

    capture = cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))
    (entry,) = capture["disks"].values()
    entry["ata"].pop("smart_data")
    entry["ata"].pop("smart_thresholds")
    entry["temperature"] = temperature
    path = tmp_path / "windows-ahci.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    output = runner.invoke(cli, ["findings", "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)["data"]


def _temperature_figures(data: Any) -> dict[str, int | None]:
    (disk,) = data["disks"]
    health = disk["health"]
    keys = ("temperature_c", "temperature_warning_c", "temperature_critical_c")
    return {key: None if health is None else health[key] for key in keys}


@pytest.mark.os_agnostic
def test_a_plausible_storage_stack_temperature_is_a_reading(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: the harness reports a sound reading, so a missing one below means it was refused."""
    data = _data_with(
        {"temperature_c": 41, "warning_c": 70, "critical_c": 80}, tmp_path, cli_runner, production_factory
    )

    assert _temperature_figures(data) == {
        "temperature_c": 41,
        "temperature_warning_c": 70,
        "temperature_critical_c": 80,
    }


@pytest.mark.os_agnostic
@pytest.mark.parametrize("implausible", [-(2**15), MIN_PLAUSIBLE_CELSIUS - 1, MAX_PLAUSIBLE_CELSIUS + 1, 2**15 - 1])
@pytest.mark.parametrize("field", ["temperature_c", "warning_c", "critical_c"])
def test_an_implausible_value_in_any_field_is_unread(
    field: str,
    implausible: int,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    sound = {"temperature_c": 41, "warning_c": 70, "critical_c": 80}
    data = _data_with({**sound, field: implausible}, tmp_path, cli_runner, production_factory)

    figures = _temperature_figures(data)
    key = {
        "temperature_c": "temperature_c",
        "warning_c": "temperature_warning_c",
        "critical_c": "temperature_critical_c",
    }
    assert figures[key[field]] is None, f"{implausible} was shown as a {field} reading"


def _limit_findings(data: Any) -> list[str]:
    return [
        finding["title"]
        for finding in data["findings"]
        if "critical limit" in finding["title"] or "warning threshold" in finding["title"]
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("limit", ["warning_c", "critical_c"])
def test_an_absurd_threshold_raises_no_finding_against_a_sound_temperature(
    limit: str, tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    data = _data_with({"temperature_c": 41, limit: -(2**15)}, tmp_path, cli_runner, production_factory)

    assert _limit_findings(data) == []


@pytest.mark.os_agnostic
def test_a_real_threshold_below_the_temperature_still_raises_a_finding(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: the finding filter above can see a genuine breach through the same harness."""
    data = _data_with({"temperature_c": 85, "critical_c": 80}, tmp_path, cli_runner, production_factory)

    assert _limit_findings(data), "a sound 85 C against a sound 80 C critical limit is a finding"
