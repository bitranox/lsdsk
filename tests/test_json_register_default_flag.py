"""The JSON says which links the human views mark as a register default.

The controllers and slots envelopes keep the raw register values, as decided: a
program reading them must get the register as it was read. What it could not do
was tell which of those links the printed pages mark ``*``, because the mark is
a verdict about the whole machine (the floor hint's three-reading evidence) and
a floor link on a controller no drive uses is named by no finding. So each
controller and each slot carries one additive boolean beside its link.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.hw.snapshot import build_from

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
CHIPSET = "windows-chipset-switch"
CAPTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))
MARKED_CONTROLLERS = {"0000:0e:00.0", "0000:10:00.0"}
#: The SATA functions, plus the chipsets' own USB functions on the same two switches, which
#: publish the floor on the same evidence and are no storage controller.
MARKED_OCCUPANTS = {"0000:0e:00.0", "0000:10:00.0", "0000:0d:00.0", "0000:0f:00.0"}


def _envelope(
    host: str, command: str, cli_runner: CliRunner, factory: Callable[[], AppServices], tmp_path: Path
) -> dict[str, Any]:
    result = cli_runner.invoke(
        cli,
        [
            "--replay",
            str(FIXTURES / f"{host}.json"),
            "--history-file",
            str(tmp_path / "history.json"),
            "--no-record",
            command,
            "--format",
            "json",
        ],
        obj=factory,
    )
    return cast("dict[str, Any]", json.loads(result.stdout))


def _raw(host: str) -> Any:
    return build_from(cast("dict[str, Any]", json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8"))))


@pytest.mark.parametrize("command", ["controllers", "slots", "topology"])
def test_the_json_flags_exactly_the_controllers_the_pages_mark(
    command: str, cli_runner: CliRunner, production_factory: Callable[[], AppServices], tmp_path: Path
) -> None:
    data = _envelope(CHIPSET, command, cli_runner, production_factory, tmp_path)["data"]
    flagged = {one["address"] for one in data["controllers"] if one["link_is_register_default"]}

    assert flagged == MARKED_CONTROLLERS
    assert any(not one["link_is_register_default"] for one in data["controllers"]), "the NVMe control is missing"
    nvme = next(one for one in data["controllers"] if one["kind"] == "nvme")
    assert nvme["link_is_register_default"] is False


def test_the_json_flags_the_slots_whose_occupant_the_pages_mark(
    cli_runner: CliRunner, production_factory: Callable[[], AppServices], tmp_path: Path
) -> None:
    data = _envelope(CHIPSET, "slots", cli_runner, production_factory, tmp_path)["data"]
    flagged = {one["occupant_address"] for one in data["slots"] if one["occupant_link_is_register_default"]}

    assert flagged == MARKED_OCCUPANTS
    assert any(not one["occupant_link_is_register_default"] for one in data["slots"])


@pytest.mark.parametrize("host", CAPTURES)
def test_the_flag_is_the_only_difference_from_the_raw_domain_values(
    host: str, cli_runner: CliRunner, production_factory: Callable[[], AppServices], tmp_path: Path
) -> None:
    """Raw register values are untouched on every capture; the new key is the whole change."""
    data = _envelope(host, "controllers", cli_runner, production_factory, tmp_path)["data"]
    machine = _raw(host)
    controllers = [{k: v for k, v in one.items() if k != "link_is_register_default"} for one in data["controllers"]]
    slots = [{k: v for k, v in one.items() if k != "occupant_link_is_register_default"} for one in data["slots"]]

    assert controllers == [json.loads(one.model_dump_json()) for one in machine.controllers]
    assert slots == [json.loads(one.model_dump_json()) for one in machine.slots]
    if host != CHIPSET:
        assert not any(one["link_is_register_default"] for one in data["controllers"])
        assert not any(one["occupant_link_is_register_default"] for one in data["slots"])
