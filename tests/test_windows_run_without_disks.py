"""A Windows capture holding no disk at all is not a run that opened its disks.

``_devices_accessible`` discounts a run whose only records are stand-ins for
interfaces it could not read, because that run never tried a device node. A
capture with NO disk records is the other empty case: nothing was opened either,
and an unelevated run there must be told that no device nodes are exposed, not
that elevating would help.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "windows-ahci.json"

SMART_NEEDS_ROOT = "smart: needs root or Administrator"
SMART_NO_NODES = "smart: no device nodes are exposed here, so elevating would not help"


def _skipped_for(
    mutate: Callable[[dict[str, Any]], None], tmp_path: Path, runner: CliRunner, factory: Callable[[], object]
) -> list[str]:
    """Replay a mutated copy of ``windows-ahci`` and return the envelope's skipped lines."""
    from lsdsk.adapters.cli import cli

    capture = cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))
    mutate(capture)
    path = tmp_path / "windows-ahci.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    output = runner.invoke(cli, ["health", "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("list[str]", cast("dict[str, Any]", decoded)["skipped"])


def _no_disks_unelevated(capture: dict[str, Any]) -> None:
    capture.update(disks={}, devices_accessible=False, elevated=False)


def test_an_unelevated_windows_capture_with_no_disks_says_no_device_nodes_are_exposed(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    skipped = _skipped_for(_no_disks_unelevated, tmp_path, cli_runner, production_factory)

    assert SMART_NO_NODES in skipped, skipped
    assert SMART_NEEDS_ROOT not in skipped, skipped


def test_an_unelevated_windows_capture_whose_devices_opened_is_told_to_elevate(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Liveness: the same harness reports the other sentence when the device nodes were reachable."""
    skipped = _skipped_for(lambda capture: capture.update(elevated=False), tmp_path, cli_runner, production_factory)

    assert SMART_NEEDS_ROOT in skipped, skipped
    assert SMART_NO_NODES not in skipped, skipped
