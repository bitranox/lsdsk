"""End-to-end tests for the storage commands.

Every test drives a real captured machine through `--replay`, so the whole path
runs: decode, map, diagnose, render. That is the same code a live scan uses, and
it runs identically on every operating system CI covers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.commands.scan import exit_code_for
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.domain.enums import Severity
from lsdsk.domain.models import Finding

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
LINUX_SNAPSHOT = FIXTURES / "linux-sas-hba.json"
WINDOWS_SNAPSHOT = FIXTURES / "windows-ahci.json"

LISTING_COMMANDS = ("topology", "findings", "controllers", "disks", "health", "slots", "smart")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", LISTING_COMMANDS)
def test_when_a_snapshot_is_replayed_every_listing_renders(
    command: str,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify each listing command renders a real machine without error."""
    result = cli_runner.invoke(cli_mod.cli, [command, "--replay", str(LINUX_SNAPSHOT)], obj=production_factory)

    assert result.exit_code in (0, 1), result.output
    assert result.output.strip(), f"{command} produced no output"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", LISTING_COMMANDS)
def test_when_json_is_requested_the_envelope_parses(
    command: str,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify the machine-readable mode emits a parsable envelope."""
    result = cli_runner.invoke(
        cli_mod.cli,
        [command, "--replay", str(LINUX_SNAPSHOT), "--format", "json"],
        obj=production_factory,
    )

    assert result.exit_code in (0, 1), result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["hostname"] == "linux-sas-hba"
    assert len(payload["data"]["disks"]) == 19
    assert payload["data"]["findings"], "this machine has findings"


@pytest.mark.os_agnostic
def test_when_a_machine_has_findings_the_exit_code_says_so(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a machine with warnings exits non-zero, for monitoring."""
    result = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", str(LINUX_SNAPSHOT)], obj=production_factory)

    assert result.exit_code == ExitCode.GENERAL_ERROR


@pytest.mark.os_agnostic
def test_when_a_machine_is_clean_the_exit_code_is_zero(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a machine with nothing wrong exits zero."""
    result = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", str(WINDOWS_SNAPSHOT)], obj=production_factory)

    assert result.exit_code == ExitCode.SUCCESS
    assert "none found" in result.output


@pytest.mark.os_agnostic
def test_when_only_hints_exist_the_exit_code_stays_zero() -> None:
    """Verify a hint alone never fails a monitoring check.

    A hint describes a ceiling that cannot be acted on, so treating it as a
    failure would make every machine with a capable card in an older board
    permanently red.
    """
    hint = Finding(Severity.HINT, "0000:03:00.0", "capped by the mainboard")
    warning = Finding(Severity.WARNING, "/dev/sda", "linked below capability")

    assert exit_code_for([hint]) == ExitCode.SUCCESS
    assert exit_code_for([]) == ExitCode.SUCCESS
    assert exit_code_for([hint, warning]) == ExitCode.GENERAL_ERROR


@pytest.mark.os_agnostic
def test_when_a_windows_snapshot_is_replayed_on_this_machine_it_renders(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a capture from Windows renders anywhere, which is the point of it."""
    result = cli_runner.invoke(cli_mod.cli, ["disks", "--replay", str(WINDOWS_SNAPSHOT)], obj=production_factory)

    assert result.exit_code == ExitCode.SUCCESS
    assert "PhysicalDrive0" in result.output


@pytest.mark.os_agnostic
def test_when_the_snapshot_is_missing_the_error_is_a_usage_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a path that does not exist is refused by argument parsing."""
    result = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", "/nonexistent.json"], obj=production_factory)

    assert result.exit_code != 0


@pytest.mark.os_agnostic
def test_when_the_snapshot_has_the_wrong_schema_it_is_refused(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a snapshot from a future version is refused with a clear message."""
    bad = tmp_path / "future.json"
    bad.write_text(json.dumps({"schema": 99, "platform": "linux"}), encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", str(bad)], obj=production_factory)

    assert result.exit_code == ExitCode.CONFIG_ERROR
    assert "schema" in result.output


@pytest.mark.os_agnostic
def test_when_the_snapshot_is_not_json_it_is_refused(
    tmp_path: Path,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a file that is not a snapshot at all produces an error, not a crash."""
    bad = tmp_path / "notjson.json"
    bad.write_text("this is not json", encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", str(bad)], obj=production_factory)

    assert result.exit_code == ExitCode.CONFIG_ERROR


@pytest.mark.os_agnostic
def test_when_replay_is_given_globally_the_default_command_uses_it(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify the global --replay reaches the default command."""
    result = cli_runner.invoke(cli_mod.cli, ["--replay", str(LINUX_SNAPSHOT)], obj=production_factory)

    assert "linux-sas-hba" in result.output


@pytest.mark.os_agnostic
def test_when_a_subcommand_overrides_replay_its_own_value_wins(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a subcommand's own --replay beats the global one."""
    result = cli_runner.invoke(
        cli_mod.cli,
        ["--replay", str(LINUX_SNAPSHOT), "disks", "--replay", str(WINDOWS_SNAPSHOT)],
        obj=production_factory,
    )

    assert "PhysicalDrive0" in result.output
    assert "/dev/sda" not in result.output


@pytest.mark.os_agnostic
def test_when_the_health_view_runs_it_shows_the_counters(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify the health view reports real wear and error counters."""
    result = cli_runner.invoke(cli_mod.cli, ["health", "--replay", str(LINUX_SNAPSHOT)], obj=production_factory)

    assert "worn" in result.output
    assert "crc" in result.output, "the cable-quality counter must be visible"
    assert "media" in result.output
    assert "/dev/nvme0n1" in result.output


@pytest.mark.os_agnostic
def test_when_the_controller_view_runs_it_shows_placement(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify the controller view reports the PCIe placement and free ports."""
    result = cli_runner.invoke(cli_mod.cli, ["controllers", "--replay", str(LINUX_SNAPSHOT)], obj=production_factory)

    assert "HBA 9500-16i" in result.output
    assert "3.0 x8" in result.output
    assert "0000:03:00.0" in result.output


@pytest.mark.os_agnostic
def test_the_full_wwn_flag_prints_the_whole_identifier(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """The escape hatch from the column cap, at the SHIPPED width.

    Lifting the ceiling alone does not do this: the fitter shrinks the column to
    its minimum long before it drops anything, and Rich then compresses every
    column again to reach the console width, so a flag that only raised the
    ceiling would be a no-op on any ordinary terminal while reading as if it had
    worked. Driven without a width override for exactly that reason.

    The row is meant to run off the side rather than to buy its width from the
    columns beside it, so the rest of the row has to survive intact.
    """
    from lsdsk.adapters.hw.snapshot import load

    machine = load(LINUX_SNAPSHOT)
    disk = next(d for d in machine.disks if d.wwn and len(d.wwn) > 24)
    assert disk.wwn is not None

    capped = cli_runner.invoke(cli_mod.cli, ["disks", "--replay", str(LINUX_SNAPSHOT)], obj=production_factory)
    full = cli_runner.invoke(
        cli_mod.cli, ["disks", "--full-wwn", "--replay", str(LINUX_SNAPSHOT)], obj=production_factory
    )

    assert disk.wwn not in capped.output, "the default must still cut it"
    assert disk.wwn in full.output, "--full-wwn must print the whole identifier"
    assert disk.path in full.output, "and say which drive it belongs to"
    # Overflowing, not trading: the columns beside it stay whole and uncut.
    assert disk.serial is not None
    assert disk.serial in full.output, "the other columns must survive the overflow"
    assert disk.controller_address is not None
    assert disk.controller_address in full.output, "including the last one on the row"
    assert max(len(line) for line in full.output.splitlines()) > 120, "the row must overflow, not fit"


@pytest.mark.os_agnostic
def test_the_full_wwn_flag_leaves_the_json_envelope_alone(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """The envelope always carried every WWN in full, so the flag changes nothing.

    A machine-readable mode that a display flag could reshape would make every
    parser depend on how the human view happened to be asked for.
    """
    argv = ["disks", "--replay", str(LINUX_SNAPSHOT), "--format", "json"]
    plain = cli_runner.invoke(cli_mod.cli, argv, obj=production_factory)
    flagged = cli_runner.invoke(cli_mod.cli, [*argv, "--full-wwn"], obj=production_factory)

    assert json.loads(plain.stdout) == json.loads(flagged.stdout)
