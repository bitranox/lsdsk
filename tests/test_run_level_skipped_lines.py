"""The run-level lines of ``skipped``, which ``ok`` rests on.

``ok`` is ``not skipped``, so a line removed from ``_skipped_readings`` turns an
incomplete scan into one that reports ``ok: true``. The per-device refusals are
held by ``test_refused_readings``; these are the lines that are properties of
the RUN - privilege, device access and the hypervisor - which no other test
asserted, so each could be deleted with the suite staying green.

Each test plants one run property in a copy of a real capture, replays it
through the CLI and reads the envelope, and a control run proves the same
capture reports none of these lines when the property is absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"

SMART_NEEDS_ROOT = "smart: needs root or Administrator"
SMART_NO_NODES = "smart: no device nodes are exposed here, so elevating would not help"
SLOT_NUMBERS = "slot-numbers: needs root or Administrator"
PHYSICAL_LINKS = "physical-link-rules: suppressed, the hypervisor invents these values"


def envelope_with(
    mutate: Callable[[dict[str, Any]], None],
    tmp_path: Path,
    runner: CliRunner,
    factory: Callable[[], object],
) -> dict[str, Any]:
    """Replay a mutated copy of ``linux-minimal`` and decode the envelope.

    Args:
        mutate: Plants the run property in the decoded capture.
        tmp_path: Directory for the modified copy.
        runner: The Click runner fixture.
        factory: The production service factory fixture.

    Returns:
        The decoded JSON envelope.
    """
    from lsdsk.adapters.cli import cli

    data = cast("dict[str, Any]", json.loads((FIXTURES / "linux-minimal.json").read_text(encoding="utf-8")))
    mutate(data)
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    output = runner.invoke(cli, ["health", "--replay", str(path), "--format", "json"], obj=factory).output
    decoded: object = json.JSONDecoder().raw_decode(output[output.index("{") :])[0]
    assert isinstance(decoded, dict)
    return cast("dict[str, Any]", decoded)


def test_a_privileged_run_on_a_physical_machine_names_none_of_the_run_lines(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The control: the unmodified capture was taken as root on bare metal."""
    envelope = envelope_with(lambda data: None, tmp_path, cli_runner, production_factory)

    run_lines = {SMART_NEEDS_ROOT, SMART_NO_NODES, SLOT_NUMBERS, PHYSICAL_LINKS}
    assert not run_lines & set(envelope["skipped"]), envelope["skipped"]


def test_an_unprivileged_run_says_smart_needs_root_and_is_not_ok(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Not root, device nodes reachable: elevating would help, and the run says so."""
    envelope = envelope_with(lambda data: data.update(euid=1000), tmp_path, cli_runner, production_factory)

    assert SMART_NEEDS_ROOT in envelope["skipped"], envelope["skipped"]
    assert SMART_NO_NODES not in envelope["skipped"], envelope["skipped"]
    assert envelope["ok"] is False


def test_an_unprivileged_run_names_the_slot_numbers_it_could_not_read(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """Slot numbers need root on their own account, whatever SMART says."""
    envelope = envelope_with(lambda data: data.update(euid=1000), tmp_path, cli_runner, production_factory)

    assert SLOT_NUMBERS in envelope["skipped"], envelope["skipped"]
    assert envelope["ok"] is False


def test_an_unprivileged_run_without_device_nodes_says_elevating_would_not_help(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """No device nodes: the other smart sentence, and not the one that advises root."""
    envelope = envelope_with(
        lambda data: data.update(euid=1000, devices_accessible=False), tmp_path, cli_runner, production_factory
    )

    assert SMART_NO_NODES in envelope["skipped"], envelope["skipped"]
    assert SMART_NEEDS_ROOT not in envelope["skipped"], envelope["skipped"]
    assert SLOT_NUMBERS in envelope["skipped"], envelope["skipped"]
    assert envelope["ok"] is False


def test_a_privileged_run_is_not_told_to_elevate_even_without_device_nodes(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """The privilege block is gated on privilege, not on device access."""
    envelope = envelope_with(
        lambda data: data.update(devices_accessible=False), tmp_path, cli_runner, production_factory
    )

    assert not {SMART_NEEDS_ROOT, SMART_NO_NODES, SLOT_NUMBERS} & set(envelope["skipped"]), envelope["skipped"]


def test_a_hypervisor_run_names_the_suppressed_link_rules_and_is_not_ok(
    tmp_path: Path, cli_runner: CliRunner, production_factory: Callable[[], object]
) -> None:
    """A privileged run on a hypervisor guest is incomplete for that reason alone."""

    def plant(data: dict[str, Any]) -> None:
        data["environment"]["hypervisor_flag"] = True

    envelope = envelope_with(plant, tmp_path, cli_runner, production_factory)

    assert PHYSICAL_LINKS in envelope["skipped"], envelope["skipped"]
    assert not {SMART_NEEDS_ROOT, SMART_NO_NODES, SLOT_NUMBERS} & set(envelope["skipped"]), envelope["skipped"]
    assert envelope["ok"] is False
