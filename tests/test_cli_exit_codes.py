"""Exit code integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result


@pytest.mark.os_agnostic
def test_when_config_section_is_invalid_it_exits_with_code_22(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Config --section with nonexistent section must exit with INVALID_ARGUMENT (22)."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config", "--section", "nonexistent_section_that_does_not_exist"], obj=production_factory
    )

    assert result.exit_code == 22
    assert "not found" in result.stderr


@pytest.mark.os_agnostic
def test_when_config_deploy_has_permission_error_it_exits_with_code_13(
    cli_runner: CliRunner,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Config-deploy PermissionError must exit with PERMISSION_DENIED (13)."""

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        set_permissions: bool = True,
        dir_mode: int | None = None,
        file_mode: int | None = None,
    ) -> list[Any]:
        raise PermissionError("Permission denied")

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "app"], obj=factory)

    assert result.exit_code == 13
    assert "Permission denied" in result.stderr


@pytest.mark.os_agnostic
def test_when_config_deploy_has_generic_error_it_exits_with_code_1(
    cli_runner: CliRunner,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Config-deploy generic Exception must exit with GENERAL_ERROR (1)."""

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        set_permissions: bool = True,
        dir_mode: int | None = None,
        file_mode: int | None = None,
    ) -> list[Any]:
        raise OSError("Disk full")

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 1
    assert "Disk full" in result.stderr


@pytest.mark.os_agnostic
def test_every_declared_exit_code_is_one_the_tool_can_actually_produce() -> None:
    """A code nothing raises is a promise to a caller that cannot be kept.

    ``TIMEOUT = 110`` sat in this enum unraised, and the module reference listed
    it among the outcomes a caller should expect. lsdsk issues no subprocesses
    and makes no network requests, so it had no way to produce it.

    The signal codes are exempt and say so in the enum's own docstring: they are
    produced by lib_cli_exit_tools translating a signal, never raised here.
    """
    import ast
    import pathlib

    from lsdsk.adapters.cli.exit_codes import ExitCode

    informational = {ExitCode.SIGNAL_INT, ExitCode.BROKEN_PIPE, ExitCode.SIGNAL_TERM}
    src = pathlib.Path(__file__).parent.parent / "src" / "lsdsk"

    referenced: set[str] = set()
    for module in src.rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ExitCode":
                referenced.add(node.attr)

    assert referenced, "the control: no ExitCode reference was found, so this asserted nothing"
    unreachable = {member.name for member in ExitCode if member not in informational} - referenced
    assert not unreachable, f"declared but never raised: {sorted(unreachable)}"


@pytest.mark.os_agnostic
def test_the_exit_codes_the_docs_promise_are_the_ones_the_code_defines() -> None:
    """README and the skill both publish this contract; a caller branches on it."""
    from lsdsk.adapters.cli.exit_codes import ExitCode

    published = {
        0: "SUCCESS",
        1: "GENERAL_ERROR",
        13: "PERMISSION_DENIED",
        22: "INVALID_ARGUMENT",
        78: "CONFIG_ERROR",
    }
    for value, name in published.items():
        assert ExitCode[name].value == value, f"{name} moved away from the documented {value}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "argv",
    [
        ["topology", "--replay", "/nonexistent.json"],
        ["topology", "--nosuchoption"],
        ["nosuchcommand"],
        ["snapshot"],
        ["topology", "--format", "bogus"],
    ],
    ids=["missing file", "unknown option", "unknown command", "missing required", "bad choice"],
)
def test_exit_two_means_a_usage_error_not_specifically_a_missing_file(
    argv: list[str],
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Both docs said "2 a missing file"; Click produces it for any bad usage.

    Parametrised over all five causes so the documentation can be written from
    what the tool does. A caller told that 2 means the capture was absent would
    retry with a different path after mistyping an option name.
    """
    result: Result = cli_runner.invoke(cli_mod.cli, argv, obj=production_factory)

    assert result.exit_code == 2
