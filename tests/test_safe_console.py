"""Tests for the encode-safe console adapter.

These pin the behaviour that a legacy-codepage console (the Windows default,
cp1252) must never turn a successful command into a crash, and the guard that
keeps the next output line someone adds covered by construction.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

from lsdsk.adapters.cli import safe_console

PKG = Path(__file__).resolve().parent.parent / "src" / "lsdsk"


def _cp1252_stream() -> io.TextIOWrapper:
    """Build the stream shape a Windows cp1252 console hands to Python."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="")


def _read_back(stream: io.TextIOWrapper) -> str:
    stream.flush()
    buffer = stream.buffer
    assert isinstance(buffer, io.BytesIO)
    return buffer.getvalue().decode("cp1252")


class TestEchoOnALegacyCodepage:
    """A cp1252 console must degrade the character, not raise."""

    def test_check_mark_does_not_raise(self) -> None:
        stream = _cp1252_stream()
        safe_console.echo("✓ deployed", file=stream)
        assert "[OK]" in _read_back(stream)

    @pytest.mark.parametrize(
        ("glyph", "expected"),
        [("✓", "[OK]"), ("✗", "[X]"), ("⚠", "[!]"), ("≥", ">="), ("→", "->")],
    )
    def test_a_character_cp1252_lacks_degrades_to_its_ascii_form(self, glyph: str, expected: str) -> None:
        stream = _cp1252_stream()
        safe_console.echo(f"{glyph} status", file=stream)
        assert expected in _read_back(stream)

    @pytest.mark.parametrize("glyph", ["•", "…", "\u2019"])
    def test_a_character_cp1252_has_is_left_alone(self, glyph: str) -> None:
        """Degrade only what the stream cannot take; cp1252 has these."""
        stream = _cp1252_stream()
        safe_console.echo(f"{glyph} status", file=stream)
        assert glyph in _read_back(stream)

    def test_an_unmapped_character_is_replaced_rather_than_raising(self) -> None:
        stream = _cp1252_stream()
        safe_console.echo("host 中文 name", file=stream)
        assert "host" in _read_back(stream)

    def test_the_message_is_written_exactly_once(self) -> None:
        """A retry-after-failure would emit the surviving prefix twice."""
        stream = _cp1252_stream()
        safe_console.echo("\n✓ done", file=stream)
        assert _read_back(stream).count("done") == 1


class TestEchoOnAUtf8Console:
    """The common case must be untouched: the character survives verbatim."""

    def test_character_is_preserved(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict", newline="")
        safe_console.echo("✓ deployed", file=stream)
        stream.flush()
        buffer = stream.buffer
        assert isinstance(buffer, io.BytesIO)
        assert "✓" in buffer.getvalue().decode("utf-8")


class TestTheDefaultTargetFollowsTheStreamEchoWritesTo:
    """With no `file`, the encoding must come from the stream `echo` lands on.

    click resolves that target itself from ``sys.stdout``/``sys.stderr``. Judge
    the wrong stream and a legacy-codepage console gets exactly the crash this
    module exists to prevent, while every test passing an explicit `file` stays
    green and says nothing about it.
    """

    def test_a_cp1252_stdout_degrades_the_glyph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stream = _cp1252_stream()
        monkeypatch.setattr(sys, "stdout", stream)
        safe_console.echo("✓ deployed")
        assert "[OK]" in _read_back(stream)

    def test_err_is_judged_against_stderr_not_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stdout that could take the glyph must not excuse a cp1252 stderr."""
        stream = _cp1252_stream()
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline=""))
        monkeypatch.setattr(sys, "stderr", stream)
        safe_console.echo("✓ deployed", err=True)
        assert "[OK]" in _read_back(stream)


class TestSafeStreamProtectsRich:
    """Rich raises on a legacy codepage too; it renders through its own writer."""

    def test_rich_output_degrades_instead_of_raising(self) -> None:
        stream = _cp1252_stream()
        Console(file=safe_console.safe_stream(stream), legacy_windows=False, width=80).print("check ✓ done ≥ 90%")
        written = _read_back(stream)
        assert "[OK]" in written
        assert ">= 90%" in written


class TestNoModuleBypassesTheAdapter:
    """The guard that keeps this fixed in every project derived from the template."""

    def test_no_module_calls_click_echo_directly(self) -> None:
        offenders = [
            f"{path.relative_to(PKG)}:{number}"
            for path in sorted(PKG.rglob("*.py"))
            if path.name != "safe_console.py"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "click.echo(" in line
        ]
        assert not offenders, (
            "these call click.echo directly and will crash on a cp1252 console; "
            f"use the adapters.cli.safe_console.echo adapter instead: {offenders}"
        )

    def test_no_module_builds_an_unwrapped_rich_console_on_stdout(self) -> None:
        offenders = [
            f"{path.relative_to(PKG)}:{number}"
            for path in sorted(PKG.rglob("*.py"))
            if path.name != "safe_console.py"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "Console(file=sys.stdout" in line
        ]
        assert not offenders, f"wrap the writer with safe_console.safe_stream(sys.stdout): {offenders}"


class TestTheRealCliSurvivesALegacyCodepage:
    """End-to-end: the packaged CLI on a cp1252 stdout, as Windows runs it."""

    def test_help_runs_under_a_cp1252_stdout(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8=0", "-m", "lsdsk", "--help"],
            capture_output=True,
            # Inherit the real environment: replacing it outright leaves a Windows
            # child with no SYSTEMROOT and no usable PATH, so python.exe never starts.
            env={**os.environ, "PYTHONIOENCODING": "cp1252:strict"},
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode("cp1252", "replace")
        assert b"UnicodeEncodeError" not in completed.stderr


class TestWhatCountsAsTheReaderLeaving:
    """A broken pipe does not look the same on both platforms.

    ``BrokenPipeError`` is raised for ``EPIPE`` and ``ESHUTDOWN``. On Windows a
    broken pipe can arrive as a plain ``OSError`` with ``EINVAL`` instead
    (bpo-19612, bpo-30418), which a handler naming only ``BrokenPipeError`` never
    sees.

    What that cost was MEASURED on a real Windows box (Python 3.14.6, both arms
    built from the same tree, the reader closing the pipe without reading):

        the guard as it is now           -> 141
        the predicate cut back to before -> 120

    and each arm's control, a reader that consumes all 78,433 bytes, exited 0. So
    the code a Windows caller got was 120, CPython's interpreter-shutdown flush
    failure, which is not an ``ExitCode`` member and appears in no document. It was
    NOT the 22 that reading the mapping alone predicts - ``get_system_exit_code``
    does answer 22 for that errno, which the control arm below pins, but end to end
    the shutdown flush fails afterwards and overrides the code already decided.
    Either way the caller is told something that did not happen.

    The platform is a PARAMETER of the predicate, so a Linux cell proves the
    Windows branch. Nothing about these arms is skipped anywhere, which is the
    point: the end-to-end pipe test that would catch this for real is
    ``os_agnostic`` and has never run on a Windows cell, because the commit that
    introduced the guard was never pushed.
    """

    @pytest.mark.os_agnostic
    def test_the_errno_this_is_about_really_does_map_to_invalid_argument(self) -> None:
        """The control for the whole class: without this, the arms below guard nothing.

        It pins the MAPPING, not the end-to-end code. Measured on Windows the
        unrecognised errno ended at 120 rather than 22, because the shutdown flush
        fails after the mapping has answered; both are codes for a cause that did
        not happen, and this arm is what keeps the class anchored to a real one.
        """
        import errno

        import lib_cli_exit_tools

        from lsdsk.adapters.cli.exit_codes import ExitCode

        windows_shaped = OSError()
        windows_shaped.errno = errno.EINVAL

        assert lib_cli_exit_tools.get_system_exit_code(windows_shaped) == ExitCode.INVALID_ARGUMENT, (
            "errno EINVAL no longer maps to 22, so this class is guarding a defect that has moved"
        )
        assert lib_cli_exit_tools.get_system_exit_code(BrokenPipeError()) == ExitCode.BROKEN_PIPE, (
            "a plain BrokenPipeError no longer maps to 141, so the comparison below means nothing"
        )

    @pytest.mark.os_agnostic
    @pytest.mark.parametrize("on_windows", [True, False], ids=["on windows", "on posix"])
    def test_a_broken_pipe_error_is_the_reader_leaving_on_every_platform(self, on_windows: bool) -> None:
        """The one shape both platforms agree on, so neither branch may lose it."""
        assert safe_console.is_broken_pipe(BrokenPipeError(), on_windows=on_windows)

    @pytest.mark.os_agnostic
    def test_an_einval_oserror_is_the_reader_leaving_only_on_windows(self) -> None:
        """Both directions, because each is a different defect.

        Read as the reader leaving on POSIX, a genuine ``EINVAL`` on a write would
        be reported as exit 141 and the real error swallowed - which is why pip's
        own predicate returns early unless it is on Windows, rather than accepting
        the errno everywhere.
        """
        import errno

        windows_shaped = OSError()
        windows_shaped.errno = errno.EINVAL

        assert safe_console.is_broken_pipe(windows_shaped, on_windows=True), (
            "a Windows broken pipe was not recognised, so it still lands on exit 22 there"
        )
        assert not safe_console.is_broken_pipe(windows_shaped, on_windows=False), (
            "a genuine EINVAL on POSIX was called a broken pipe, so a real write error exits 141"
        )

    @pytest.mark.os_agnostic
    @pytest.mark.parametrize("on_windows", [True, False], ids=["on windows", "on posix"])
    def test_an_unrelated_oserror_is_never_the_reader_leaving(self, on_windows: bool) -> None:
        """A full disk is not a closed pipe, on either platform."""
        import errno

        full = OSError()
        full.errno = errno.ENOSPC

        assert not safe_console.is_broken_pipe(full, on_windows=on_windows)

    @pytest.mark.os_agnostic
    def test_the_platform_is_read_at_the_call_when_nobody_names_it(self) -> None:
        """The production call sites pass no platform, so the default must be live.

        Bound at import instead, the answer would be frozen for the process, and
        every arm above would still pass.
        """
        import errno

        windows_shaped = OSError()
        windows_shaped.errno = errno.EINVAL

        assert safe_console.is_broken_pipe(windows_shaped) == sys.platform.startswith("win"), (
            "the unnamed platform did not follow the interpreter this test is running on"
        )

    @pytest.mark.os_agnostic
    def test_a_write_that_fails_for_another_reason_still_reaches_the_caller(self) -> None:
        """The guard widens from BrokenPipeError to OSError, so it must not swallow the rest.

        Without this, catching ``OSError`` to reach the Windows case would turn a
        full disk into a silent exit 141 on every platform.
        """
        import errno

        class _RefusesToWrite(io.TextIOWrapper):
            """A stream whose write fails for a reason that is not the reader leaving."""

            def write(self, text: str) -> int:
                full = OSError()
                full.errno = errno.ENOSPC
                raise full

        stream = _RefusesToWrite(io.BytesIO(), encoding="utf-8", errors="strict", newline="")

        with pytest.raises(OSError) as raised:
            safe_console.echo("anything", file=stream)

        assert raised.value.errno == errno.ENOSPC, (
            f"the guard replaced a disk-full error with errno {raised.value.errno}"
        )


class TestWhatMainLeavesBehindForAnEmbeddingCaller:
    """``main()`` RETURNS its code, so whatever it did to the process outlives the call."""

    @pytest.mark.os_posix
    def test_a_departed_reader_does_not_leave_the_caller_s_stdout_pointed_at_the_null_device(
        self, tmp_path: Path
    ) -> None:
        """A library caller must get its own fd 1 back.

        The guard against the interpreter's shutdown flush replaces the file
        DESCRIPTOR, which is process-wide and permanent. ``entry.py`` exits
        immediately afterwards so it never notices; a caller that imports
        ``main`` and carries on gets control back with everything it prints
        going to the null device, and nothing raises to say so.

        Driven as a spawned program because the probe has to break its own fd 1,
        which a test process sharing pytest's stdout cannot do.
        """
        import json

        from lsdsk.adapters.cli.exit_codes import ExitCode

        verdict = tmp_path / "verdict.json"
        probe = Path(__file__).parent / "helpers" / "embedding_caller.py"
        completed = subprocess.run(  # noqa: S603 - argv is built here, no shell
            [sys.executable, str(probe), str(verdict), "--version"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            check=False,
            timeout=120,
        )

        assert completed.returncode == 0, f"the probe itself failed: {completed.stderr.decode(errors='replace')}"
        recorded = json.loads(verdict.read_text(encoding="utf-8"))
        assert recorded["code"] == int(ExitCode.BROKEN_PIPE), (
            f"the control: a broken fd 1 must reach the broken-pipe code, and left {recorded['code']}"
        )
        assert recorded["after"] == recorded["before"], (
            "main() returned with fd 1 pointing somewhere else, so an embedding caller's own "
            "output now goes to the null device in silence"
        )


@pytest.mark.os_agnostic
def test_a_guarded_diagnostic_write_swallows_a_departed_reader_and_nothing_else() -> None:
    """The guard catches one event in two shapes, never every ``SystemExit``.

    A diagnostic write reaches the reader two ways, and a broken pipe arrives as
    an ``OSError`` from one and as ``SystemExit(141)`` from the other, because
    :func:`~lsdsk.adapters.cli.safe_console.echo` ends the run for a departed
    STDOUT reader - which is right for a command's own output and wrong for a
    sentence about a code already decided.

    So the second catch has to be narrow. Written broadly, ``except SystemExit``
    passes every other arm in this file while swallowing a command's deliberate
    exit raised inside a diagnostic write, and the run would then report success
    for a failure it had already decided. The control below is what makes this
    arm about WHICH code rather than about a guard that does nothing.
    """
    from lsdsk.adapters.cli.exit_codes import ExitCode

    def leaves_with_a_code_of_its_own() -> None:
        raise SystemExit(ExitCode.CONFIG_ERROR)

    with pytest.raises(SystemExit) as leaving:
        safe_console.write_unless_the_reader_left(leaves_with_a_code_of_its_own)
    assert leaving.value.code == ExitCode.CONFIG_ERROR, (
        f"a deliberate exit was rewritten to {leaving.value.code!r} inside a diagnostic write"
    )

    def leaves_because_the_reader_did() -> None:
        raise SystemExit(ExitCode.BROKEN_PIPE)

    safe_console.write_unless_the_reader_left(leaves_because_the_reader_did)
