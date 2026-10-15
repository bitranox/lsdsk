"""Tests for config-deploy permission options.

Verifies that permission parameters are correctly parsed, passed through
the CLI, and delivered to deploy_configuration.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.deploy import deploy_configuration
from lsdsk.adapters.config.permissions import read_permission_defaults
from lsdsk.composition import build_production
from lsdsk.domain.deployment import DeployRequest
from lsdsk.domain.enums import DeployTarget

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result
    from lib_layered_config import Config

# ======================== Permission Settings Loader Tests ========================


@dataclass
class CapturedDeployArgs:
    """The permission arguments one config-deploy invocation passed through."""

    set_permissions: bool | None = None
    dir_mode: int | None = None
    file_mode: int | None = None


def test_permissions_are_set_when_nothing_is_configured(
    config_factory: Callable[[dict[str, Any]], Config],
) -> None:
    """With no section configured, modes are set: the switch defaults to on."""
    reading = read_permission_defaults(config_factory({}))

    assert reading.defaults.enabled is True
    assert reading.rejected == ()


def test_the_configured_switch_is_read_and_the_modes_beside_it_are_left_to_the_deploy(
    config_factory: Callable[[dict[str, Any]], Config],
) -> None:
    """``enabled`` is lsdsk's to settle; a mode next to it, usable or not, is not judged here."""
    config = config_factory(
        {"lib_layered_config": {"default_permissions": {"user_file": "abc", "app_file": "0o640", "enabled": False}}}
    )

    reading = read_permission_defaults(config)

    assert reading.defaults.enabled is False
    assert reading.rejected == ()


@pytest.mark.os_agnostic
def test_cli_deploy_passes_set_permissions_true_by_default(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """Default behavior sets permissions (enabled in default config)."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].set_permissions is True


@pytest.mark.os_agnostic
def test_cli_deploy_no_permissions_flag_disables_permissions(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--no-permissions flag disables permission setting."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--no-permissions"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].set_permissions is False


@pytest.mark.os_agnostic
def test_cli_deploy_permissions_flag_enables_permissions(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--permissions flag explicitly enables permission setting."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user", "--permissions"], obj=factory)

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].set_permissions is True


@pytest.mark.os_agnostic
def test_cli_deploy_dir_mode_parses_octal_without_prefix(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--dir-mode accepts octal string without 0o prefix (e.g., '750')."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--dir-mode", "750"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].dir_mode == 0o750


@pytest.mark.os_agnostic
def test_cli_deploy_dir_mode_parses_octal_with_prefix(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--dir-mode accepts octal string with 0o prefix (e.g., '0o750')."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--dir-mode", "0o750"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].dir_mode == 0o750


@pytest.mark.os_agnostic
def test_cli_deploy_file_mode_parses_octal(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--file-mode accepts octal string."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--file-mode", "640"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].file_mode == 0o640


@pytest.mark.os_agnostic
def test_cli_deploy_invalid_octal_dir_mode_rejected(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Invalid octal mode string is rejected with error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--dir-mode", "abc"], obj=production_factory
    )

    assert result.exit_code != 0
    assert "Invalid octal mode" in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_invalid_octal_file_mode_rejected(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Invalid octal mode string for file-mode is rejected."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--file-mode", "xyz"], obj=production_factory
    )

    assert result.exit_code != 0
    assert "Invalid octal mode" in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_both_mode_options_passed(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """Both --dir-mode and --file-mode can be specified together."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-deploy", "--target", "user", "--dir-mode", "750", "--file-mode", "640"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].dir_mode == 0o750
    assert captured[0].file_mode == 0o640


@pytest.mark.os_agnostic
def test_cli_deploy_no_permissions_reports_in_output(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """When --no-permissions is used, output mentions permissions not set."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--no-permissions"], obj=factory
    )

    assert result.exit_code == 0
    assert "permissions not set" in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_help_shows_permission_options(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Help text includes permission-related options."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--help"], obj=production_factory)

    assert result.exit_code == 0
    assert "--permissions" in result.output
    assert "--no-permissions" in result.output
    assert "--dir-mode" in result.output
    assert "--file-mode" in result.output


# ======================== deploy.py Integration Tests ========================


@pytest.mark.os_agnostic
def test_deploy_configuration_passes_set_permissions_to_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deploy_configuration passes set_permissions to deploy_config."""
    from lsdsk.adapters.config import deploy as deploy_mod

    captured_kwargs: list[dict[str, Any]] = []

    def mock_deploy_config(**kwargs: Any) -> list[Any]:
        captured_kwargs.append(kwargs)
        return []

    monkeypatch.setattr(deploy_mod, "deploy_config", mock_deploy_config)

    deploy_mod.deploy_configuration(DeployRequest(targets=(DeployTarget.USER,), force=False, set_permissions=False))

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["set_permissions"] is False


@pytest.mark.os_agnostic
def test_deploy_configuration_passes_mode_overrides_to_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deploy_configuration passes dir_mode and file_mode to deploy_config."""
    from lsdsk.adapters.config import deploy as deploy_mod

    captured_kwargs: list[dict[str, Any]] = []

    def mock_deploy_config(**kwargs: Any) -> list[Any]:
        captured_kwargs.append(kwargs)
        return []

    monkeypatch.setattr(deploy_mod, "deploy_config", mock_deploy_config)

    deploy_mod.deploy_configuration(
        DeployRequest(targets=(DeployTarget.USER,), force=False, dir_mode=0o750, file_mode=0o640)
    )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["dir_mode"] == 0o750
    assert captured_kwargs[0]["file_mode"] == 0o640


@pytest.mark.os_posix
def test_a_custom_mode_reaches_the_bits_on_disk(user_config_dir: Path) -> None:
    """The whole feature, through the real library, ending at stat().

    Every other test of this stops one layer short: the CLI-level ones capture
    what the command passed through an injected double, and the two at
    deploy_configuration level substitute the library call. A regression losing
    dir_mode or file_mode between this command and the library would pass all of
    them, because none of them ever looks at a file.

    POSIX only, and not as root: root's umask and ownership make the modes
    something other than what was asked for, so the assertion would be about the
    runner rather than about the code.
    """
    if os.geteuid() == 0:
        pytest.skip("root's deployment modes are not the ones asked for")

    deployed = deploy_configuration(
        DeployRequest(targets=(DeployTarget.USER,), dir_mode=0o750, file_mode=0o640, set_permissions=True)
    )

    assert deployed, "nothing was written, so the modes below would assert nothing"
    directory = user_config_dir
    assert stat.S_IMODE(directory.stat().st_mode) == 0o750, oct(stat.S_IMODE(directory.stat().st_mode))
    for written in deployed:
        assert stat.S_IMODE(written.stat().st_mode) == 0o640, f"{written}: {oct(stat.S_IMODE(written.stat().st_mode))}"


@pytest.mark.os_posix
def test_the_shipped_modes_are_not_the_custom_ones(user_config_dir: Path) -> None:
    """The control for the test above.

    If the defaults happened to be 750 and 640 it would pass against a build
    that ignored the overrides entirely, which is the regression it exists to
    catch.
    """
    if os.geteuid() == 0:
        pytest.skip("root's deployment modes are not the ones asked for")

    deployed = deploy_configuration(DeployRequest(targets=(DeployTarget.USER,), set_permissions=True))

    assert deployed
    assert stat.S_IMODE(user_config_dir.stat().st_mode) != 0o750
    assert stat.S_IMODE(deployed[0].stat().st_mode) != 0o640


@pytest.fixture
def inject_deploy_with_permission_capture() -> Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]]:
    """Capture the permission arguments the deploy command passes through.

    Returns:
        A factory taking the path to report as deployed and a list to record
        each call's arguments into, returning a services factory.
    """

    def _inject(deployed_path: Path, captured: list[CapturedDeployArgs]) -> Callable[[], Any]:
        # One typed parameter rather than **kwargs: a double that took loose
        # keywords went on accepting a call that had dropped one of them, which
        # is the drift the request exists to make impossible.
        def _capturing_deploy(request: DeployRequest) -> list[Path]:
            captured.append(
                CapturedDeployArgs(
                    set_permissions=request.set_permissions,
                    dir_mode=request.dir_mode,
                    file_mode=request.file_mode,
                )
            )
            return [deployed_path]

        test_services = replace(build_production(), deploy_configuration=_capturing_deploy)
        return lambda: test_services

    return _inject
