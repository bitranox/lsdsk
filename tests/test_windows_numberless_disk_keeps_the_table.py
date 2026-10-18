"""A disk whose number Windows refused does not push every other column off the page.

Without its PhysicalDrive number such a disk is named by its device interface
path, a hundred characters of GUID-laden text. The device column never gave up
width, so at the tool's own piped width of 120 the disks table dropped every
column but the device and its link - for every disk on the machine, not only
the one Windows would not number. The column now gives up width like the model
does, its cut marked, while the whole path stays in the JSON ``path`` field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-chipset-switch.json"
#: The SATA drive on windows-chipset-switch whose number the capture is made to lose.
_NODE = "PhysicalDrive0"
#: The columns the disks table keeps at 120 for this machine when its drives are numbered.
_KEPT_AT_120 = ("device", "model", "size", "link")


def _numberless(tmp_path: Path) -> tuple[Path, str]:
    capture = cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))
    interface, entry = next((path, entry) for path, entry in capture["disks"].items() if entry.get("node") == _NODE)
    del entry["node"]
    entry["device_number_error"] = "Win32 error 1"
    path = tmp_path / "numberless.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    return path, interface


def _disks(path: Path, runner: CliRunner, factory: Callable[[], object], *, width: int | None = None) -> str:
    from lsdsk.adapters.cli import cli

    # A root option, so it goes before the command: after it, click answers "No such option".
    root = [] if width is None else ["--set", f"display.piped_width={width}"]
    result = runner.invoke(cli, [*root, "disks", "--replay", str(path)], obj=factory)
    assert result.exit_code in {0, 1}, result.output
    return result.output


def _header(output: str) -> list[str]:
    line = next(line for line in output.splitlines() if line.split()[:1] == ["device"])
    return line.split()


def test_the_numbered_machine_keeps_its_columns_at_the_piped_width(
    cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The control: the unchanged capture draws more than the device and its link at 120."""
    header = _header(_disks(FIXTURE, cli_runner, production_factory))

    assert all(column in header for column in _KEPT_AT_120), header


def test_a_numberless_disk_keeps_every_other_column_on_the_page(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    capture, interface = _numberless(tmp_path)

    output = _disks(capture, cli_runner, production_factory)

    numbered = _header(_disks(FIXTURE, cli_runner, production_factory))
    lost = set(numbered) - set(_header(output))
    # The cut name keeps a few characters more than PhysicalDrive0 takes, which may cost one column.
    assert len(lost) <= 1, f"lost {sorted(lost)}"
    assert not lost & set(_KEPT_AT_120), lost
    (row,) = [line for line in output.splitlines() if "\\\\?\\" in line]
    device = row.split()[1]
    assert interface.startswith(device[:-1]), f"the device cell is the start of its path: {row!r}"
    assert device.endswith(">"), f"the cut is marked: {row!r}"


@pytest.mark.parametrize("width", [60, 80, 120, 160])
def test_no_row_of_a_numberless_machine_runs_past_the_page(
    width: int, tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    capture, _ = _numberless(tmp_path)

    output = _disks(capture, cli_runner, production_factory, width=width)

    table = output.split("\n\n", 1)[0].splitlines()
    assert len(table) == 5, "the title, the header and three disks"
    assert max(len(line.rstrip()) for line in table) <= width
    # The prose under the table is laid out by the same console, so it wraps at the same width.
    assert "not on this page" in " ".join(output.split()), "the left-out findings line is not drawn, so it is not held"
    too_long = [line for line in output.splitlines() if len(line.rstrip()) > width]
    assert not too_long, f"lines past {width} columns: {too_long}"


def test_the_whole_interface_path_stays_in_the_json(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    from lsdsk.adapters.cli import cli

    capture, interface = _numberless(tmp_path)
    output = cli_runner.invoke(
        cli, ["disks", "--replay", str(capture), "--format", "json"], obj=production_factory
    ).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    paths = [disk["path"] for disk in cast("dict[str, Any]", decoded)["data"]["disks"]]

    assert interface in paths
