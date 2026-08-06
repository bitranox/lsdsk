"""CLI core stories: traceback, main entry, help, fail, info, unknown command."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lib_cli_exit_tools
import pytest

from lsdsk import __init__conf__
from lsdsk.adapters import cli as cli_mod
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result


SNAPSHOT = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


@pytest.mark.os_agnostic
def test_snapshot_traceback_state_returns_disabled_by_default(managed_traceback_state: None) -> None:
    """snapshot_traceback_state returns both flags disabled initially."""
    assert cli_mod.snapshot_traceback_state() == (False, False)


@pytest.mark.os_agnostic
def test_apply_traceback_preferences_enables_both_flags(managed_traceback_state: None) -> None:
    """apply_traceback_preferences(True) enables traceback and force_color."""
    cli_mod.apply_traceback_preferences(True)

    assert lib_cli_exit_tools.config.traceback is True
    assert lib_cli_exit_tools.config.traceback_force_color is True


@pytest.mark.os_agnostic
def test_restore_traceback_state_resets_flags_to_previous(managed_traceback_state: None) -> None:
    """restore_traceback_state resets flags to their pre-apply values."""
    previous = cli_mod.snapshot_traceback_state()
    cli_mod.apply_traceback_preferences(True)

    cli_mod.restore_traceback_state(previous)

    assert lib_cli_exit_tools.config.traceback is False
    assert lib_cli_exit_tools.config.traceback_force_color is False


@pytest.mark.os_agnostic
def test_traceback_flag_is_active_during_info_command(
    managed_traceback_state: None,
) -> None:
    """--traceback enables both flags during command execution.

    Injected at the ``print_info`` port rather than patched onto this project's
    own ``__init__conf__``: the port exists because these two tests were the
    suite's only remaining self-mocks, and a double substituted at a real seam
    proves the wiring as well as the behaviour.
    """
    notes: list[tuple[bool, bool]] = []

    def record() -> None:
        notes.append(
            (
                lib_cli_exit_tools.config.traceback,
                lib_cli_exit_tools.config.traceback_force_color,
            )
        )

    def services() -> Any:
        return replace(build_production(), print_info=record)

    exit_code = cli_mod.main(["--traceback", "info"], services_factory=services)

    assert exit_code == 0
    assert notes == [(True, True)]


@pytest.mark.os_agnostic
def test_traceback_flags_restored_after_info_command(
    managed_traceback_state: None,
) -> None:
    """--traceback flags are restored to disabled after command completes."""

    def services() -> Any:
        return replace(build_production(), print_info=lambda: None)

    cli_mod.main(["--traceback", "info"], services_factory=services)

    assert lib_cli_exit_tools.config.traceback is False
    assert lib_cli_exit_tools.config.traceback_force_color is False


@pytest.mark.os_agnostic
def test_when_main_is_called_it_invokes_cli_with_services_factory(
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify main() invokes CLI with services_factory passed via ctx.obj."""
    result = cli_mod.main(["info"], services_factory=build_production)

    assert result == 0
    captured = capsys.readouterr()
    assert __init__conf__.name in captured.out


@pytest.mark.os_agnostic
def test_when_cli_runs_without_arguments_it_scans(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a bare invocation scans instead of printing help.

    Looking at the machine is what the tool is for, so that is the default.
    A snapshot is replayed rather than reading real hardware, which keeps the
    test identical on every operating system CI runs on.
    """
    result = cli_runner.invoke(cli_mod.cli, ["--replay", str(SNAPSHOT)], obj=production_factory)

    assert result.exit_code in (0, 1), result.output
    assert "linux-sas-hba" in result.output
    assert "PROBLEMS" in result.output


@pytest.mark.os_agnostic
def test_when_main_receives_only_help_it_shows_usage(
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify --help still lists the commands."""
    exit_code = cli_mod.main(["--help"], services_factory=build_production)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Usage:" in captured.out


@pytest.mark.os_agnostic
def test_when_traceback_is_requested_without_command_the_scan_still_runs(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify a global flag before the default command does not break it."""
    result = cli_runner.invoke(cli_mod.cli, ["--traceback", "--replay", str(SNAPSHOT)], obj=production_factory)

    assert result.exit_code in (0, 1), result.output
    assert "linux-sas-hba" in result.output


@pytest.mark.os_agnostic
def test_traceback_flag_displays_full_exception_traceback(
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
    strip_ansi: Callable[[str], str],
) -> None:
    """--traceback prints the complete traceback on failure."""
    exit_code = cli_mod.main(["--traceback", "fail"], services_factory=build_production)

    plain_err = strip_ansi(capsys.readouterr().err)

    assert exit_code != 0
    assert "Traceback (most recent call last)" in plain_err
    assert "RuntimeError: I should fail" in plain_err
    assert "[TRUNCATED" not in plain_err
    assert lib_cli_exit_tools.config.traceback is False
    assert lib_cli_exit_tools.config.traceback_force_color is False


@pytest.mark.os_agnostic
def test_fail_command_raises_runtime_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """fail command raises RuntimeError."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["fail"], obj=production_factory)

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)


@pytest.mark.os_agnostic
def test_info_command_displays_project_metadata(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """info command displays project name and version."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["info"], obj=production_factory)

    assert result.exit_code == 0
    assert f"Info for {__init__conf__.name}:" in result.output
    assert __init__conf__.version in result.output


@pytest.mark.os_agnostic
def test_unknown_command_shows_no_such_command_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Unknown command produces 'No such command' error."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["does-not-exist"], obj=production_factory)

    assert result.exit_code != 0
    assert "No such command" in result.output


@pytest.mark.os_agnostic
def test_restore_traceback_false_keeps_flags_enabled(
    managed_traceback_state: None,
) -> None:
    """restore_traceback=False leaves traceback flags enabled after command."""
    cli_mod.apply_traceback_preferences(False)

    cli_mod.main(["--traceback", "info"], restore_traceback=False, services_factory=build_production)

    assert lib_cli_exit_tools.config.traceback is True
    assert lib_cli_exit_tools.config.traceback_force_color is True


@pytest.mark.os_agnostic
def test_when_logdemo_is_invoked_it_completes_successfully(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify logdemo command runs and exits with code 0."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["logdemo"], obj=production_factory)

    assert result.exit_code == 0
    assert "Log demo completed" in result.output
