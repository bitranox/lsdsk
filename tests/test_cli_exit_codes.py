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

    ``BROKEN_PIPE`` used to sit in that exemption and did not belong there. The
    stated reason was false - nothing translates a broken pipe, click turns it
    into a 1 first - so the member this guard was least able to see was the one
    that was unreachable. It is checked like any other now.
    """
    import ast
    import pathlib

    from lsdsk.adapters.cli.exit_codes import ExitCode

    informational = {ExitCode.SIGNAL_INT, ExitCode.SIGNAL_TERM}
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


@pytest.mark.os_agnostic
@pytest.mark.parametrize("argv", [["findings"], ["topology"], []], ids=["findings", "topology", "bare"])
def test_a_diagnostic_run_exits_13_when_the_hardware_read_is_refused(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """A refused read is "could not run", and it says so with the privilege code.

    ``load_inventory`` converts a ``PermissionError`` escaping the reader into
    exit 13, and every diagnostic command goes through it. Nothing pinned that,
    and the shipped skill told an agent the opposite - that a diagnostic run
    never exits 13 and degrades instead - so error handling written against the
    documentation branched on a code this path really does produce.

    Degrading IS what happens for a field the reader could not read: those are
    swallowed per attribute and reported as ``-`` with a line in ``skipped``.
    This is the other case, where the read as a whole was refused and there is
    no inventory to degrade.
    """
    from lsdsk.adapters.hw import snapshot as snapshot_adapter

    def refuse() -> dict[str, Any]:
        raise PermissionError(13, "Permission denied", "/sys/class/nvme")

    monkeypatch.setattr(snapshot_adapter, "read_current_machine", refuse)

    result: Result = cli_runner.invoke(cli_mod.cli, argv, obj=production_factory, color=False)

    assert result.exit_code == 13, f"{argv or 'bare'}: exited {result.exit_code}"
    assert "Permission denied" in result.output


def _run_and_take_the_output_away(*, capture: Path, history: Path, argv: list[str], leaves: bool) -> tuple[int, int]:
    """Run a real lsdsk process and either read it out or walk away.

    Spawned rather than invoked in-process because the defect lives in the write
    to a real pipe: a ``CliRunner`` writes into a buffer that never closes, so
    both arms would pass against the broken code.

    The reader closes WITHOUT reading. Taking a byte first and then closing is
    the shape a shell pipeline has, but it is a race against the writer and
    measured 5 failures in 12 runs here; closing first was 12 in 12. A flaky
    arm on a defect this quiet is worse than none, because the green run is the
    one that gets believed.

    Args:
        capture: The snapshot to replay.
        history: A counter store of this test's own, so the run can neither read
            nor write the developer's.
        argv: The subcommand and any format flag, which is what selects the sink
            under test - the human page renders through rich's writer, the JSON
            envelope through ``safe_console.echo``, and they are separate code.
        leaves: Whether the reader closes the pipe instead of consuming it.

    Returns:
        The exit code, and how many bytes were read before leaving.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    process = subprocess.Popen(  # noqa: S603 - argv is built here, no shell
        [
            sys.executable,
            "-m",
            "lsdsk",
            "--no-record",
            "--history-file",
            str(history),
            "--replay",
            str(capture),
            *argv,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent.parent),
        env={**os.environ, "TERM": "dumb"},
    )
    assert process.stdout is not None, "the pipe this test is about was not created"
    try:
        read = 0
        if leaves:
            process.stdout.close()
        else:
            read = len(process.stdout.read())
        return process.wait(timeout=120), read
    finally:
        if process.poll() is None:  # pragma: no cover - only on a hang
            process.kill()
            process.wait(timeout=30)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["findings", "--format", "json"], id="json, through safe_console.echo"),
        pytest.param(["report"], id="human, through rich's writer"),
    ],
)
def test_a_reader_that_leaves_early_gets_the_broken_pipe_code_not_the_findings_code(argv: list[str]) -> None:
    """`lsdsk ... | head` must not report the code that means a failing drive.

    Click catches the EPIPE itself (``click/core.py``, ``except OSError`` on
    ``errno.EPIPE``) and calls ``sys.exit(1)`` - and 1 is this tool's code for
    "found a warning or critical". So a monitoring check that pipes the output
    read a hardware fault on a healthy machine, with nothing on any stream to
    say otherwise, not even under ``--traceback``.

    The consume-everything arm is the control: it shares every part of this
    apparatus except the reader leaving, so a run that never reaches the pipe
    fails here rather than passing quietly.
    """
    from pathlib import Path

    from lsdsk.adapters.cli.exit_codes import ExitCode

    capture = Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json"
    history = Path(__file__).parent / "fixtures" / "hw" / "does-not-exist-history.json"

    consumed, size = _run_and_take_the_output_away(capture=capture, history=history, argv=argv, leaves=False)
    assert consumed == ExitCode.SUCCESS, "the control: this capture must exit 0 when its output is read"
    assert size > 0, "the control wrote nothing, so the arm below would pass with no pipe to break"

    abandoned, _ = _run_and_take_the_output_away(capture=capture, history=history, argv=argv, leaves=True)
    assert abandoned == ExitCode.BROKEN_PIPE, (
        f"a reader that left early got {abandoned}, "
        f"and {int(ExitCode.GENERAL_ERROR)} is what an actionable finding leaves"
    )
