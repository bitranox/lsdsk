"""An IDENTIFY page that does not say solid state or rotating does not override a reading that does.

Word 217 is 0 or 0xFFFF on a drive that does not report a nominal rotation
rate, which decodes to ``UNKNOWN``. The operating system's own answer (the
kernel's ``queue/rotational``, Windows' seek-penalty descriptor) is a real
reading, and an unknown from the IDENTIFY page used to replace it with a dash.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
WORD_ROTATION_RATE = 217
SOLID_STATE = 1
SEVEN_TWO_HUNDRED = 7200


def _with_rotation_word(identify: str, value: int) -> str:
    words = list(struct.unpack("<256H", base64.b64decode(identify)[:512]))
    words[WORD_ROTATION_RATE] = value
    return base64.b64encode(struct.pack("<256H", *words)).decode("ascii")


def _kinds(
    fixture: str,
    plant: Callable[[dict[str, Any]], None],
    tmp_path: Path,
    runner: CliRunner,
    factory: Callable[[], object],
) -> dict[str, str]:
    from lsdsk.adapters.cli import cli

    data = cast("dict[str, Any]", json.loads((FIXTURES / fixture).read_text(encoding="utf-8")))
    plant(data)
    path = tmp_path / fixture
    path.write_text(json.dumps(data), encoding="utf-8")
    output = runner.invoke(cli, ["disks", "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return {disk["node"]: disk["kind"] for disk in cast("dict[str, Any]", decoded)["data"]["disks"]}


@pytest.mark.parametrize(("rotational", "expected"), [("1", "hdd"), ("0", "ssd")])
@pytest.mark.parametrize("silent_word", [0, 0xFFFF])
def test_linux_keeps_the_rotational_reading_when_identify_is_silent(
    rotational: str,
    expected: str,
    silent_word: int,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    def plant(data: dict[str, Any]) -> None:
        data["ata"]["sda"]["identify"] = _with_rotation_word(data["ata"]["sda"]["identify"], silent_word)
        data["block"]["sda"]["queue"]["rotational"] = rotational

    kinds = _kinds("linux-minimal.json", plant, tmp_path, cli_runner, production_factory)

    assert kinds["sda"] == expected


def test_linux_identify_still_wins_when_it_does_speak(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: a spoken word 217 is not overridden by the kernel's reading."""

    def plant(data: dict[str, Any]) -> None:
        data["ata"]["sda"]["identify"] = _with_rotation_word(data["ata"]["sda"]["identify"], SEVEN_TWO_HUNDRED)
        data["block"]["sda"]["queue"]["rotational"] = "0"

    kinds = _kinds("linux-minimal.json", plant, tmp_path, cli_runner, production_factory)

    assert kinds["sda"] == "hdd"


@pytest.mark.parametrize(("rotating", "expected"), [(True, "hdd"), (False, "ssd")])
def test_windows_keeps_the_seek_penalty_reading_when_identify_is_silent(
    rotating: bool,
    expected: str,
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    def plant(data: dict[str, Any]) -> None:
        (entry,) = data["disks"].values()
        entry["rotating"] = rotating

    kinds = _kinds("windows-ahci.json", plant, tmp_path, cli_runner, production_factory)

    assert list(kinds.values()) == [expected]
