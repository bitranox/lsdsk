"""The Windows storage-stack temperature is held to the bound every other path uses.

The fallback reads a temperature, a warning and a critical threshold, each a
signed 16-bit value the driver fills in. A driver with no sensor to report, or a
bus answering garbage, leaves values such as -32768, 150, 32767 or 0; shown as a
reading, one of them is also a critical finding and exit status 1. An
implausible value is unread, whichever of the three fields carries it.
"""

from __future__ import annotations

import base64
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


@pytest.mark.parametrize("implausible", [-(2**15), MIN_PLAUSIBLE_CELSIUS - 1, MAX_PLAUSIBLE_CELSIUS + 1, 2**15 - 1])
def test_an_implausible_temperature_leaves_the_disk_with_no_health_reading_at_all(
    implausible: int, tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """With no sound temperature the disk has no health, rather than a health holding only the thresholds."""
    sound = {"temperature_c": 41, "warning_c": 70, "critical_c": 80}
    data = _data_with({**sound, "temperature_c": implausible}, tmp_path, cli_runner, production_factory)

    (disk,) = data["disks"]
    assert disk["health"] is None


def test_a_sound_temperature_keeps_the_disk_health_reading(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: the same harness reports a health object when the temperature is sound."""
    data = _data_with(
        {"temperature_c": 41, "warning_c": 70, "critical_c": 80}, tmp_path, cli_runner, production_factory
    )

    (disk,) = data["disks"]
    assert disk["health"] is not None


def _limit_findings(data: Any) -> list[str]:
    return [
        finding["title"]
        for finding in data["findings"]
        if "critical limit" in finding["title"] or "warning threshold" in finding["title"]
    ]


@pytest.mark.parametrize("limit", ["warning_c", "critical_c"])
def test_an_absurd_threshold_raises_no_finding_against_a_sound_temperature(
    limit: str, tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    data = _data_with({"temperature_c": 41, limit: -(2**15)}, tmp_path, cli_runner, production_factory)

    assert _limit_findings(data) == []


def test_a_real_threshold_below_the_temperature_still_raises_a_finding(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: the finding filter above can see a genuine breach through the same harness."""
    data = _data_with({"temperature_c": 85, "critical_c": 80}, tmp_path, cli_runner, production_factory)

    assert _limit_findings(data), "a sound 85 C against a sound 80 C critical limit is a finding"


#: The ATA attributes a SMART temperature is read from.
_TEMPERATURE_ATTRIBUTES = (190, 194)
#: Where SMART READ DATA's attribute table starts, how many entries it holds, and their width.
_TABLE_OFFSET, _TABLE_ENTRIES, _ENTRY_BYTES = 2, 30, 12


def _without_temperature_attributes(encoded: str) -> str:
    """The SMART READ DATA blob with every temperature attribute's slot emptied."""
    raw = bytearray(base64.b64decode(encoded))
    for slot in range(_TABLE_ENTRIES):
        offset = _TABLE_OFFSET + slot * _ENTRY_BYTES
        if raw[offset] in _TEMPERATURE_ATTRIBUTES:
            raw[offset : offset + _ENTRY_BYTES] = bytes(_ENTRY_BYTES)
    return base64.b64encode(bytes(raw)).decode("ascii")


def _health_with(
    temperature: dict[str, int] | None,
    *,
    smart_has_temperature: bool,
    tmp_path: Path,
    runner: CliRunner,
    factory: Callable[[], object],
) -> dict[str, Any]:
    """Replay windows-ahci with its SMART data kept, and a storage-stack reading beside it."""
    from lsdsk.adapters.cli import cli

    capture = cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))
    (entry,) = capture["disks"].values()
    if not smart_has_temperature:
        entry["ata"]["smart_data"] = _without_temperature_attributes(entry["ata"]["smart_data"])
    if temperature is not None:
        entry["temperature"] = temperature
    path = tmp_path / "windows-ahci.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    output = runner.invoke(cli, ["health", "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    (disk,) = cast("dict[str, Any]", decoded)["data"]["disks"]
    health = disk["health"]
    assert isinstance(health, dict), "the SMART data decoded, so the disk carries health"
    return cast("dict[str, Any]", health)


def test_smart_data_with_no_temperature_attribute_carries_none(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The precondition: emptying the temperature slots leaves the passthrough health without one."""
    health = _health_with(
        None, smart_has_temperature=False, tmp_path=tmp_path, runner=cli_runner, factory=production_factory
    )

    assert health["temperature_c"] is None
    assert health["power_on_hours"] is not None


def test_passthrough_health_with_no_temperature_takes_the_storage_stacks(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """A drive whose SMART names no temperature still has the one the storage stack offered."""
    stack = {"temperature_c": 41, "warning_c": 70, "critical_c": 80}
    health = _health_with(
        stack, smart_has_temperature=False, tmp_path=tmp_path, runner=cli_runner, factory=production_factory
    )

    assert (health["temperature_c"], health["temperature_warning_c"], health["temperature_critical_c"]) == (41, 70, 80)
    assert health["power_on_hours"] is not None, "the SMART figures stay; only the temperature is filled in"


def test_a_passthrough_temperature_keeps_the_storage_stacks_thresholds(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The drive's own SMART temperature (31 here) wins; the thresholds ATA SMART lacks come from the stack."""
    stack = {"temperature_c": 41, "warning_c": 70, "critical_c": 80}
    health = _health_with(
        stack, smart_has_temperature=True, tmp_path=tmp_path, runner=cli_runner, factory=production_factory
    )

    assert health["temperature_c"] == 31, "the capture's own SMART reading"
    assert (health["temperature_warning_c"], health["temperature_critical_c"]) == (70, 80)


SWITCH_FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-chipset-switch.json"
#: The SATA drive on windows-chipset-switch: SMART names a temperature, the stack a 70 C warning.
_SATA_NODE = "PhysicalDrive0"
#: The NVMe drive on windows-chipset-switch: its SMART log and Identify carry 52 C, 84 C and 88 C.
_NVME_NODE = "PhysicalDrive1"


def _switch_entry(capture: dict[str, Any], node: str) -> dict[str, Any]:
    return next(entry for entry in capture["disks"].values() if entry.get("node") == node)


def _with_smart_temperature(encoded: str, celsius: int) -> str:
    """The SMART READ DATA blob with its temperature attribute's current reading set to ``celsius``."""
    raw = bytearray(base64.b64decode(encoded))
    slots = [_TABLE_OFFSET + slot * _ENTRY_BYTES for slot in range(_TABLE_ENTRIES)]
    offset = next(offset for offset in slots if raw[offset] in _TEMPERATURE_ATTRIBUTES)
    raw[offset + 5] = celsius
    return base64.b64encode(bytes(raw)).decode("ascii")


def _switch_data(
    command: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    tmp_path: Path,
    runner: CliRunner,
    factory: Callable[[], object],
) -> Any:
    from lsdsk.adapters.cli import cli

    capture = cast("dict[str, Any]", json.loads(SWITCH_FIXTURE.read_text(encoding="utf-8")))
    mutate(capture)
    path = tmp_path / "windows-chipset-switch.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    output = runner.invoke(cli, [command, "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)["data"]


def _switch_health(data: Any, node: str) -> tuple[int | None, int | None, int | None]:
    health = next(disk["health"] for disk in data["disks"] if disk["node"] == node)
    return health["temperature_c"], health["temperature_warning_c"], health["temperature_critical_c"]


def test_a_hot_drive_whose_smart_names_its_temperature_is_judged_by_the_stacks_warning(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """SMART reads 75 C and the stack offers a 70 C warning: the drive is reported at its threshold.

    The stack's critical figure on this capture is -32768, a driver's "nothing here", so it stays unread.
    """

    def hot(capture: dict[str, Any]) -> None:
        ata = _switch_entry(capture, _SATA_NODE)["ata"]
        ata["smart_data"] = _with_smart_temperature(ata["smart_data"], 75)

    data = _switch_data("findings", hot, tmp_path=tmp_path, runner=cli_runner, factory=production_factory)

    assert _switch_health(data, _SATA_NODE) == (75, 70, None)
    assert any("75 C, at or above its 70 C warning threshold" in title for title in _limit_findings(data))


def test_the_drives_own_thresholds_win_over_the_storage_stacks(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Both ends name every figure: the NVMe drive's own temperature and thresholds are the ones kept."""

    def disagreeing_stack(capture: dict[str, Any]) -> None:
        _switch_entry(capture, _NVME_NODE)["temperature"] = {"temperature_c": 30, "warning_c": 60, "critical_c": 65}

    data = _switch_data("health", disagreeing_stack, tmp_path=tmp_path, runner=cli_runner, factory=production_factory)

    assert _switch_health(data, _NVME_NODE) == (52, 84, 88)


def test_an_implausible_stack_temperature_fills_no_temperature_in(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The fallback is held to the same bound: -32768 from the stack is no temperature either.

    Its sound thresholds are still offered, since each field is judged alone.
    """
    stack = {"temperature_c": -(2**15), "warning_c": 70, "critical_c": 80}
    health = _health_with(
        stack, smart_has_temperature=False, tmp_path=tmp_path, runner=cli_runner, factory=production_factory
    )

    assert (health["temperature_c"], health["temperature_warning_c"], health["temperature_critical_c"]) == (
        None,
        70,
        80,
    )


def test_a_smart_temperature_takes_the_thresholds_of_a_stack_whose_own_temperature_is_absurd(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """A stack with no sound temperature still offers sound thresholds to the drive's own reading."""
    stack = {"temperature_c": -(2**15), "warning_c": 70, "critical_c": 80}
    health = _health_with(
        stack, smart_has_temperature=True, tmp_path=tmp_path, runner=cli_runner, factory=production_factory
    )

    assert (health["temperature_c"], health["temperature_warning_c"], health["temperature_critical_c"]) == (31, 70, 80)
