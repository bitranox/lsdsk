"""Exit code integration tests."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.hw import snapshot as snapshot_adapter
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result

    from lsdsk.domain.deployment import DeployRequest

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json"
REPO = Path(__file__).parent.parent

#: The codes lsdsk never raises, exempt from both guards below.
#:
#: ``lib_cli_exit_tools`` translates a signal into these; no code here does. The
#: exemption is defined once because two tests read it: one asks that every other
#: member is reachable, the other that every other member is documented. Written
#: twice they would drift, and the drift would silently widen whichever guard
#: gained the extra member.
INFORMATIONAL_CODES = frozenset({ExitCode.SIGNAL_INT, ExitCode.SIGNAL_TERM})

#: A caller-facing document and the row shape its exit codes are published in.
_EXIT_CODE_ROW = re.compile(r"^\|\s*`(\d+)`\s*\|")


def _codes_in_the_table_of(path: Path) -> set[int]:
    """The exit codes published as table rows in `path`."""
    rows = (_EXIT_CODE_ROW.match(line) for line in path.read_text(encoding="utf-8").splitlines())
    return {int(row.group(1)) for row in rows if row is not None}


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

    def mock_deploy(request: DeployRequest) -> list[Any]:
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

    def mock_deploy(request: DeployRequest) -> list[Any]:
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

    src = pathlib.Path(__file__).parent.parent / "src" / "lsdsk"

    referenced: set[str] = set()
    for module in src.rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ExitCode":
                referenced.add(node.attr)

    assert referenced, "the control: no ExitCode reference was found, so this asserted nothing"
    unreachable = {member.name for member in ExitCode if member not in INFORMATIONAL_CODES} - referenced
    assert not unreachable, f"declared but never raised: {sorted(unreachable)}"


@pytest.mark.os_agnostic
def test_every_code_the_tool_can_raise_is_documented_where_a_caller_reads() -> None:
    """A caller branches on these, so a code the documents omit reaches nobody.

    141 arrived exactly that way: it was raised at the failing write while
    COMMANDS.md, its German twin and the skill all said nothing about it, and the
    skill's own table said anything above 1 meant the command did not run - which
    silently reclassified "your reader left". Nothing caught it, because the guard
    beside this one reads five VALUES off a hand-written dict and never asks what
    the documents enumerate.

    So this is keyed on the enum's members rather than on a list: a new code is
    required to be documented the day it is declared, and the requirement arrives
    with it rather than being remembered. The signal codes are the one exemption
    and they share it with the reachability guard above.

    The skill publishes a table, so the table is required to be EXACTLY the set -
    a row for a code no member holds is as wrong as a missing row. The command
    reference states them in prose instead, so there the test can only ask that
    the code appears at all.
    """
    published = _codes_in_the_table_of(REPO / "skills" / "lsdsk" / "SKILL.md")
    assert published, "the control: no exit-code table was found in the skill, so this asserted nothing"

    raised = {int(member) for member in ExitCode if member not in INFORMATIONAL_CODES}
    assert published == raised, f"the skill's table and the enum disagree: {sorted(published ^ raised)}"

    for reference in (REPO / "COMMANDS.md", REPO / "de" / "COMMANDS.md"):
        prose = reference.read_text(encoding="utf-8")
        undocumented = sorted(code for code in raised if f"`{code}`" not in prose)
        assert not undocumented, f"{reference.name} documents no {undocumented}"
        assert "`9999`" not in prose, "the control: this check cannot report a code as absent"


@pytest.mark.os_agnostic
def test_the_exit_codes_the_docs_promise_are_the_ones_the_code_defines() -> None:
    """README and the skill both publish this contract; a caller branches on it."""
    from lsdsk.adapters.cli.exit_codes import ExitCode

    published = {
        0: "SUCCESS",
        1: "GENERAL_ERROR",
        13: "PERMISSION_DENIED",
        22: "INVALID_ARGUMENT",
        70: "SOFTWARE_ERROR",
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


@pytest.mark.os_agnostic
def test_the_version_line_is_exactly_what_it_has_always_been() -> None:
    """The control for taking the version printer off click.

    Writing it ourselves is only safe if the text does not move: the existing
    subprocess test asserts the version NUMBER appears somewhere in stdout, which a
    reworded line would still satisfy. This pins the whole line.
    """
    import subprocess
    import sys
    from pathlib import Path

    from lsdsk import __init__conf__

    result = subprocess.run(
        [sys.executable, "-m", "lsdsk", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(__file__).parent.parent),
        check=False,
        timeout=60,
    )

    expected = f"{__init__conf__.shell_command} version {__init__conf__.version}\n"
    assert result.stdout == expected, f"the version line changed: {result.stdout!r} is not {expected!r}"
    assert result.returncode == 0, f"--version exited {result.returncode}"


class _PipeRun(NamedTuple):
    """What one spawned run left behind.

    A NamedTuple rather than a bare triple because all three members are ``int``,
    so a swap at a call site type-checks exactly as well as the right order does -
    which is the rule this repo already applies to its own returns.
    """

    code: int
    stdout_bytes: int
    stderr_bytes: int


def _run_and_take_the_output_away(
    *, capture: Path, history: Path, argv: list[str], leaves: bool, stderr_leaves: bool = False
) -> _PipeRun:
    """Run a real lsdsk process and either read each stream out or walk away from it.

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
        leaves: Whether the reader closes the stdout pipe instead of consuming it.
        stderr_leaves: Whether it closes the stderr pipe too, which is the shape
            ``lsdsk ... 2>&1 | head`` has - one pipe carrying both streams, so the
            reader leaving breaks both at once.

    Returns:
        The exit code and how many bytes each stream delivered; a stream whose
        reader left reports 0, since nothing was taken from it.
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
    assert process.stderr is not None, "the stderr pipe this test is about was not created"
    try:
        # Both readers are settled BEFORE either stream is drained: closing one
        # pipe after blocking on a read of the other deadlocks whenever the
        # writer fills the pipe nobody is reading.
        if leaves:
            process.stdout.close()
        if stderr_leaves:
            process.stderr.close()
        out = 0 if leaves else len(process.stdout.read())
        err = 0 if stderr_leaves else len(process.stderr.read())
        return _PipeRun(process.wait(timeout=120), out, err)
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
        # The third sink: the package metadata, which wrote to sys.stdout directly.
        # Its output is far under the 8 KB block buffer, so nothing reached the OS
        # until the interpreter's own exit flush - outside every handler - and the
        # run left 120, CPython's shutdown-flush failure, which is not an ExitCode
        # member and appears in no document.
        pytest.param(["info"], id="metadata, through the package's own writer"),
        # click's own two printers, each failing by a different mechanism and both
        # measured before the fix. --version: click's main() catches the EPIPE
        # itself (core.py, `except OSError` on errno.EPIPE), swaps both streams for
        # a _PacifyFlushWrapper and exits 1 - regardless of standalone_mode, so it
        # fires before any handler of ours is reached, and 1 is this tool's code for
        # an actionable finding. --help: main() RETURNS NORMALLY with the streams
        # untouched, because 7,111 bytes of help never left Python's 8 KB block
        # buffer, and the interpreter's own exit flush then fails at 120 where
        # nothing in the process can see it.
        pytest.param(["--version"], id="version, through click's own printer"),
        pytest.param(["--help"], id="help, buffered below the block size"),
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

    control = _run_and_take_the_output_away(capture=capture, history=history, argv=argv, leaves=False)
    assert control.code == ExitCode.SUCCESS, "the control: this capture must exit 0 when its output is read"
    assert control.stdout_bytes > 0, "the control wrote nothing, so the arm below would pass with no pipe to break"

    abandoned = _run_and_take_the_output_away(capture=capture, history=history, argv=argv, leaves=True).code
    assert abandoned == ExitCode.BROKEN_PIPE, (
        f"a reader that left early got {abandoned}, "
        f"and {int(ExitCode.GENERAL_ERROR)} is what an actionable finding leaves"
    )


FINDINGS_CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
ABSENT_HISTORY = Path(__file__).parent / "fixtures" / "hw" / "does-not-exist-history.json"


@pytest.mark.os_agnostic
def test_a_reader_that_leaves_on_both_streams_is_still_answered_with_the_broken_pipe_code() -> None:
    """`lsdsk ... 2>&1 | head` must not leave 120.

    One pipe carrying both streams is the shape a monitoring check has, and the
    reader leaving breaks both at once. Measured before this was fixed: 120,
    CPython's interpreter-shutdown flush failure, which is not an
    :class:`ExitCode` member and appears in no document this tool ships. The
    cause is that the guard pointed STDOUT at the null device when it was STDERR
    that broke, so stderr's own buffered write was retried at shutdown, outside
    every handler, and overrode the 141 already decided.

    ``config`` and not ``report``, because the defect needs a FAILED WRITE ON
    STDERR and only this command makes one under the harness's flags - it logs
    "Displaying configuration" there. Under ``report`` the stderr buffer is
    empty, its shutdown flush cannot fail, and the arm passes against the broken
    code. The control asserts both streams carried something so that can never
    go unnoticed again.
    """
    argv = ["config"]
    control = _run_and_take_the_output_away(capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False)
    assert control.code == ExitCode.SUCCESS, "the control: this capture must exit 0 when both streams are read"
    assert control.stdout_bytes > 0, "the control wrote nothing to stdout, so there is no pipe to break"
    assert control.stderr_bytes > 0, (
        "the control wrote nothing to STDERR, so the shutdown flush this test is about cannot fail "
        "and the arm below would pass against the broken code"
    )

    abandoned = _run_and_take_the_output_away(
        capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=True, stderr_leaves=True
    ).code
    assert abandoned == ExitCode.BROKEN_PIPE, f"a reader that left on both streams got {abandoned}"


@pytest.mark.os_agnostic
def test_a_refusal_click_itself_printed_outranks_a_departed_reader() -> None:
    """An unknown command is an unknown command whoever was reading.

    The contract (user, 2026-09-20): a code saying the run could not START - 2,
    13, 22, 78 - outranks the reader leaving, because there was never any output
    for that reader to lose. A code saying what the output CONTAINED yields,
    which is the test after this one.

    This arm is click's OWN printer, reached before any command runs. Measured
    before the fix: 120, because the ``BrokenPipeError`` from printing the usage
    message was raised inside the handler that was printing it and escaped
    ``main`` entirely.
    """
    argv = ["nosuchcommand"]
    control = _run_and_take_the_output_away(capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False)
    assert control.code == ExitCode.USAGE_ERROR, (
        f"the control: an unknown command must leave {int(ExitCode.USAGE_ERROR)}, and left {control.code}"
    )
    assert control.stderr_bytes > 0, (
        "the control wrote nothing to stderr, so the write this test is about never happened"
    )

    abandoned = _run_and_take_the_output_away(
        capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=True, stderr_leaves=True
    ).code
    assert abandoned == ExitCode.USAGE_ERROR, (
        f"an unknown command piped into a reader that left got {abandoned}, so the one actionable fact reached nobody"
    )


@pytest.mark.os_agnostic
def test_a_refusal_this_tool_printed_outranks_a_departed_stderr_reader() -> None:
    """The same contract through this project's own writer rather than click's.

    ``config --section`` finds its refusal itself and reports it with
    :func:`~lsdsk.adapters.cli.safe_console.echo`. A departed STDERR reader must
    not turn that into 141: stderr carries diagnostics ABOUT the run, never the
    run's own output, so nobody listening to it costs this one message and
    leaves the verdict standing. Measured before the fix: 120.

    Only stderr's reader leaves here. With stdout's gone as well the answer is
    141 and correctly so - this command prints a note to stdout BEFORE it looks
    the section up, so the reader leaves before any refusal has been decided and
    there is nothing for the contract to rank.
    """
    argv = ["config", "--section", "nonexistent_section_that_does_not_exist"]
    control = _run_and_take_the_output_away(capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False)
    assert control.code == ExitCode.INVALID_ARGUMENT, (
        f"the control: reading both streams must still give {int(ExitCode.INVALID_ARGUMENT)}, not {control.code}"
    )
    assert control.stderr_bytes > 0, "the control wrote nothing to stderr, so this arm has no write to break"

    abandoned = _run_and_take_the_output_away(
        capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False, stderr_leaves=True
    ).code
    assert abandoned == ExitCode.INVALID_ARGUMENT, (
        f"a mistyped --section whose stderr reader left got {abandoned}, so the one actionable fact reached nobody"
    )


@pytest.mark.os_agnostic
def test_a_closed_stderr_does_not_discard_the_report_on_stdout() -> None:
    """Losing the stream nobody was reading must not cost the reader the report.

    rich's own ``Console.on_broken_pipe`` runs
    ``os.dup2(devnull, sys.stdout.fileno())`` - hardcoded to STDOUT whichever
    stream actually broke - and the console it fires on is the one
    ``lib_log_rich`` builds for its stderr output, which this project does not
    construct. Measured before the logging stream was routed through the guarded
    writer: the control delivered 13,166 bytes on stdout and this arm delivered
    1, with nothing on any stream to say the rest had gone to the null device.

    ``config`` and not ``report``, because the defect needs a write on STDERR and
    only this command makes one under the harness's flags.
    """
    argv = ["config"]
    control = _run_and_take_the_output_away(capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False)
    assert control.stdout_bytes > 0, "the control read nothing, so this arm has no loss to detect"
    assert control.stderr_bytes > 0, (
        "the control wrote nothing to stderr, so the write this test is about never happened"
    )

    kept = _run_and_take_the_output_away(
        capture=CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False, stderr_leaves=True
    )
    assert kept.stdout_bytes == control.stdout_bytes, (
        f"a closed stderr cost stdout {control.stdout_bytes - kept.stdout_bytes} of its "
        f"{control.stdout_bytes} bytes, delivered to the null device instead of to the reader"
    )


@pytest.mark.os_agnostic
def test_a_findings_verdict_yields_to_a_departed_reader() -> None:
    """A verdict the reader never received must not be reported as delivered.

    The other half of the same contract. 141 says "what you asked for was not
    delivered", which is exactly true of a report cut off mid-stream; leaving 1
    would tell a monitoring check it had received a complete verdict when it
    received one line.
    """
    control = _run_and_take_the_output_away(
        capture=FINDINGS_CAPTURE, history=ABSENT_HISTORY, argv=["report"], leaves=False
    )
    assert control.code == ExitCode.GENERAL_ERROR, (
        f"the control: this capture must report a finding when read, and gave {control.code}"
    )

    abandoned = _run_and_take_the_output_away(
        capture=FINDINGS_CAPTURE, history=ABSENT_HISTORY, argv=["report"], leaves=True
    ).code
    assert abandoned == ExitCode.BROKEN_PIPE, (
        f"a truncated report left {abandoned}, which says the verdict arrived when it did not"
    )


def _read_a_committed_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand a committed capture in for the hardware read.

    ``read_current_machine`` reads sysfs, ioctls or SetupAPI, which is the true
    external edge of ``snapshot``: a CI runner has no drives worth reading and a
    macOS one cannot read hardware at all. Everything after the read - which is
    the write these two arms are about - runs for real.
    """
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(snapshot_adapter, "read_current_machine", lambda: capture)


@pytest.mark.os_agnostic
def test_a_snapshot_that_cannot_be_written_exits_with_a_code_lsdsk_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A destination that cannot be created is not a usage error.

    ``save`` declares ``Raises: OSError`` and the command caught
    ``ConfigurationError`` alone, so the errno reached the top-level handler and
    became the exit code. Measured on this machine before the change:
    ``-o /proc/lsdskx.json`` left 2 and ``-o /dev/full`` left 28, and 2 is the
    code Click leaves for a usage error - an unknown option, a bad ``--format``
    choice - so a caller branching on it rewrites its command line when the real
    answer is that the destination cannot be written. The arm reaches the same
    branch portably: a regular file standing where the destination's directory
    should be, which raises EEXIST rather than a permission error and so cannot
    coincide with a code lsdsk raises. Driven through ``main`` rather than a
    runner, because the errno mapping is ``main``'s and a runner never reaches
    it.
    """
    _read_a_committed_capture(monkeypatch)
    in_the_way = tmp_path / "not-a-directory"
    in_the_way.write_text("", encoding="utf-8")
    target = in_the_way / "capture.json"

    code = cli_mod.main(["snapshot", "-o", str(target)], services_factory=build_production)

    assert code == ExitCode.GENERAL_ERROR, f"a write that could not happen left {code}"
    assert str(target) in capsys.readouterr().err, "the refusal does not name the destination"


@pytest.mark.os_posix
def test_a_snapshot_refused_by_the_filesystem_says_which_path_was_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The permission arm, which the exit code alone cannot hold.

    EACCES is 13 and :class:`ExitCode.PERMISSION_DENIED` is 13, so an escaping
    ``PermissionError`` leaves the right number for the wrong reason and an
    assertion on the code alone passes either way - measured, it did. What
    separates them is the sentence: the top-level handler prints the exception,
    which carries the path but not what lsdsk was doing with it, and only the
    command's own handler says a capture was being written.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root writes through a mode that refuses everyone else")
    _read_a_committed_capture(monkeypatch)
    closed = tmp_path / "closed"
    closed.mkdir(mode=0o500)
    target = closed / "capture.json"

    try:
        code = cli_mod.main(["snapshot", "-o", str(target)], services_factory=build_production)
    finally:
        closed.chmod(0o700)

    assert code == ExitCode.PERMISSION_DENIED, f"a refused write left {code}"
    refusal = capsys.readouterr().err
    assert str(target) in refusal, "the refusal does not name the destination"
    assert "write the capture to" in refusal, f"nothing says what was refused: {refusal!r}"


#: A real file that is certainly not a capture, so the reader refuses it with 78
#: before anything is written to stdout at all.
NOT_A_CAPTURE = Path(__file__).parent.parent / "README.md"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "fmt",
    [
        pytest.param([], id="human, whose sentence goes to stderr"),
        pytest.param(["--format", "json"], id="json, whose envelope goes to stdout"),
    ],
)
def test_a_refusal_outranks_a_departed_reader_in_either_output_format(fmt: list[str]) -> None:
    """The exit code is the same fact in both formats, so it cannot depend on one.

    A snapshot this version cannot read is refused before a single byte of output
    exists, so the contract (user, 2026-09-20) says 78 stands whoever was
    reading. Human mode already did: its sentence goes to stderr, and stdout was
    never written.

    JSON mode wrote the same refusal as an envelope on STDOUT, and
    :func:`~lsdsk.adapters.cli.safe_console.echo` treats a departed stdout reader
    as a reason to stop the run - which is right for a command's own output and
    wrong for a diagnostic about a code already decided. Measured before the fix:
    78 in human mode and 141 in JSON mode for one identical refusal, breaking the
    format-independence the exit codes are for.

    Parametrized over the two formats rather than asserting the JSON arm alone,
    because the property is that they AGREE: a fix that moved human mode to 141
    would satisfy a single-arm test.
    """
    from lsdsk.adapters.cli.exit_codes import ExitCode

    argv = ["findings", *fmt]
    control = _run_and_take_the_output_away(capture=NOT_A_CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=False)
    assert control.code == ExitCode.CONFIG_ERROR, (
        f"the control: a file that is not a capture must leave {int(ExitCode.CONFIG_ERROR)}, and left {control.code}"
    )
    assert control.stderr_bytes > 0, "the control wrote nothing to stderr, so the refusal reached nobody"

    abandoned = _run_and_take_the_output_away(
        capture=NOT_A_CAPTURE, history=ABSENT_HISTORY, argv=argv, leaves=True, stderr_leaves=True
    ).code
    assert abandoned == ExitCode.CONFIG_ERROR, (
        f"a refusal whose reader left got {abandoned}, so the one actionable fact reached nobody"
    )


LINUX_SAS_HBA = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


@pytest.mark.os_agnostic
def test_a_crash_in_the_tool_does_not_look_like_a_machine_that_needs_attention(
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """The one distinction a monitoring caller needs and could not make.

    Both left 1 until this split, and the skill's exit-code table documented it
    by telling a caller to read stderr before treating 1 as a finding, which a
    monitoring script cannot do.

    Driven through ``main`` rather than through the click runner because the
    mapping lives in ``main``'s last-resort handler; the runner catches the
    exception itself and never reaches it.
    """
    crashed = cli_mod.main(["fail"], services_factory=build_production)
    capsys.readouterr()
    assert crashed == ExitCode.SOFTWARE_ERROR, f"an internal error left {crashed}"

    found = cli_runner.invoke(cli_mod.cli, ["topology", "--replay", str(LINUX_SAS_HBA)], obj=production_factory)
    assert found.exit_code == ExitCode.GENERAL_ERROR, (
        f"the control: a machine with an actionable finding must still leave {int(ExitCode.GENERAL_ERROR)}, "
        f"and left {found.exit_code}"
    )
    assert crashed != found.exit_code, "the two are the same number again, which is the whole defect"


@pytest.mark.os_agnostic
def test_a_code_the_resolver_derived_from_the_exception_is_not_relabelled_as_a_crash() -> None:
    """70 replaces the resolver's generic fallback, never a code it worked out.

    EPERM is 1, so an ``OSError`` carrying it resolves to the same number the
    fallback produces. Keying the split on that NUMBER would report a refusal the
    kernel gave as a bug in this tool, which is the opposite of what the split is
    for. A broken pipe resolves to 141 for the same reason and must survive it.
    """
    import errno

    from lsdsk.adapters.cli.exit_codes import code_for_an_unhandled_exception

    assert code_for_an_unhandled_exception(RuntimeError("boom")) == ExitCode.SOFTWARE_ERROR
    assert code_for_an_unhandled_exception(OSError(errno.EPERM, "Operation not permitted")) == ExitCode.GENERAL_ERROR
    assert code_for_an_unhandled_exception(KeyboardInterrupt()) == ExitCode.SIGNAL_INT
    assert code_for_an_unhandled_exception(BrokenPipeError()) == ExitCode.BROKEN_PIPE


@pytest.mark.os_agnostic
def test_a_crash_stands_whoever_was_reading_because_it_says_nothing_about_the_output() -> None:
    """The contract (user, 2026-09-20), extended to the code added the same day.

    141 means "what you asked for was not delivered", which is why a verdict
    yields to it: `lsdsk report | head -5` on a failing machine really did show
    five lines of one. A crash says nothing about what the output contained, so
    it stands for the same reason a refusal does - and a check that pipes lsdsk
    into `head`, `jq` or `grep` would otherwise read 141 and file a crash as its
    own reader leaving, which is the one hole left in the split that 70 exists
    to make.

    Asserted on the decider rather than end to end: the pipe breaks at a write,
    and no command both writes to stdout and then crashes, so there is no run
    that reaches this branch to drive. The mutation that drops 70 from the set
    is what keeps the arm honest.
    """
    from lsdsk.adapters.cli.exit_codes import ExitCode, outranks_a_departed_reader

    assert outranks_a_departed_reader(int(ExitCode.SOFTWARE_ERROR)) is True, (
        "a crash whose reader had already gone would report the reader leaving, not the crash"
    )
    assert outranks_a_departed_reader(int(ExitCode.CONFIG_ERROR)) is True, (
        "the control that must answer the same way: a refusal still stands"
    )
    assert outranks_a_departed_reader(int(ExitCode.GENERAL_ERROR)) is False, (
        "the control that must answer the OTHER way: an undelivered verdict still yields"
    )
    assert outranks_a_departed_reader(int(ExitCode.SUCCESS)) is False
