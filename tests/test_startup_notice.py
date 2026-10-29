"""The line an interactive start shows while lsdsk imports and reads the machine.

Measured on a slow Windows guest: after uvx prints its install line, the CLI
import takes about 1.8 s, the interactive view's import up to 1.5 s more and the
scan 1.3 s, with nothing on the screen. These tests hold what fills that gap and
that it never reaches a run somebody is not sitting at.
"""

from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from lsdsk.adapters import startup_notice
from lsdsk.adapters.cli import safe_console
from lsdsk.adapters.startup_notice import NoStartupNotice, StartupNotice, notice_for_this_start, spinner_for

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

LINE = "Scanning Drives ..."

#: The control sequence that erases the line the cursor is on.
ERASE_LINE = "\x1b[2K"


def a_terminal() -> tuple[Console, io.StringIO]:
    """A console that believes it draws on a terminal, writing into a buffer the test reads."""
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=True, width=80, color_system="standard"), buffer


def nothing_is_redirected() -> list[str]:
    """The stream answer of a start where every stream the view needs is a terminal."""
    return []


def test_a_started_notice_draws_the_line_in_bold_green() -> None:
    console, buffer = a_terminal()
    notice = StartupNotice(console)

    notice.start()
    try:
        drawn = buffer.getvalue()
    finally:
        notice.stop()

    assert LINE in drawn
    # Bold (1) and green (32) as one SGR sequence, in front of the text.
    assert "\x1b[1;32m" in drawn


def test_a_stopped_notice_hands_the_standard_streams_back() -> None:
    console, _ = a_terminal()
    before = (sys.stdout, sys.stderr)
    notice = StartupNotice(console)

    notice.start()
    # While it runs, anything printed goes above the line rather than through it.
    assert (sys.stdout, sys.stderr) != before
    notice.stop()

    assert (sys.stdout, sys.stderr) == before


def test_a_paused_notice_draws_again_on_resume_and_hands_the_streams_back_between() -> None:
    console, buffer = a_terminal()
    before = (sys.stdout, sys.stderr)
    notice = StartupNotice(console)

    notice.start()
    notice.pause()
    paused_streams = (sys.stdout, sys.stderr)
    drawn_before_resume = buffer.getvalue().count(LINE)
    notice.resume()
    drawn_after_resume = buffer.getvalue().count(LINE)
    notice.stop()

    assert paused_streams == before
    assert drawn_after_resume > drawn_before_resume


def test_every_step_may_be_taken_twice_and_in_any_order() -> None:
    console, _ = a_terminal()
    before = (sys.stdout, sys.stderr)
    notice = StartupNotice(console)

    notice.pause()
    notice.resume()
    notice.resume()
    notice.pause()
    notice.pause()
    notice.stop()
    notice.stop()
    notice.resume()

    assert (sys.stdout, sys.stderr) == before


def test_a_stream_that_cannot_encode_braille_gets_an_ascii_spinner() -> None:
    assert spinner_for("utf-8") == "dots"
    assert spinner_for("UTF8") == "dots"
    assert spinner_for("cp1252") == "line"
    assert spinner_for("ascii") == "line"


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="bare"),
        pytest.param(["--no-elevate"], id="a root option"),
        pytest.param(["--replay", "capture.json"], id="a root option with a value"),
        pytest.param(["tui"], id="the view by name"),
    ],
)
def test_an_interactive_start_shows_the_notice(argv: list[str]) -> None:
    console, _ = a_terminal()

    notice = notice_for_this_start(argv, not_terminals=nothing_is_redirected, console=lambda: console)

    assert isinstance(notice, StartupNotice)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["disks"], id="a subcommand"),
        pytest.param(["--no-elevate", "findings"], id="a subcommand after a root option"),
        pytest.param(["--help"], id="help"),
        pytest.param(["-h"], id="short help"),
        pytest.param(["--version"], id="version"),
    ],
)
def test_a_start_that_is_not_the_interactive_view_shows_nothing(argv: list[str]) -> None:
    console, _ = a_terminal()

    notice = notice_for_this_start(argv, not_terminals=nothing_is_redirected, console=lambda: console)

    assert isinstance(notice, NoStartupNotice)


def test_a_redirected_stream_shows_nothing() -> None:
    console, _ = a_terminal()

    notice = notice_for_this_start([], not_terminals=lambda: ["standard output"], console=lambda: console)

    assert isinstance(notice, NoStartupNotice)


def test_only_tui_among_the_commands_opens_the_view() -> None:
    from lsdsk.adapters.cli import cli  # the CLI is the slow import this module exists to run ahead of

    assert [name for name in sorted(cli.commands) if startup_notice.opens_the_view(name)] == ["tui"]
    assert startup_notice.opens_the_view(None)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["dsiks"], id="a mistyped command"),
        pytest.param(["--no-elevate", "dsiks"], id="a mistyped command after a root option"),
        pytest.param(["--replay", "capture.json", "dsiks"], id="a mistyped command after a root option's value"),
        pytest.param(["--", "dsiks"], id="a mistyped command after the end of the options"),
    ],
)
def test_a_mistyped_command_shows_nothing(argv: list[str]) -> None:
    """Click refuses the word with a usage error, which the line would only flash around."""
    console, _ = a_terminal()

    notice = notice_for_this_start(argv, not_terminals=nothing_is_redirected, console=lambda: console)

    assert isinstance(notice, NoStartupNotice)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--profile", "disks"], id="a root option's value that happens to name a command"),
        pytest.param(["--set=display.piped_width=90", "tui"], id="a root option written with its value attached"),
        pytest.param(["--tree-density", "full", "tui", "--no-such-option"], id="the view, whatever follows it"),
    ],
)
def test_a_value_is_not_read_as_the_command(argv: list[str]) -> None:
    console, _ = a_terminal()

    notice = notice_for_this_start(argv, not_terminals=nothing_is_redirected, console=lambda: console)

    assert isinstance(notice, StartupNotice)


def test_the_root_options_the_start_rule_skips_a_value_for_are_the_ones_the_cli_has() -> None:
    import click

    from lsdsk.adapters.cli import cli  # the CLI is the slow import this module exists to run ahead of

    taking_a_value = {
        name
        for param in cli.params
        if isinstance(param, click.Option) and not param.is_flag and not param.count
        for name in (*param.opts, *param.secondary_opts)
    }

    assert frozenset(taking_a_value) == startup_notice.ROOT_OPTIONS_TAKING_A_VALUE


def _a_terminal_file(path: Path) -> tuple[Console, TextIO]:
    """A console drawing into a real buffered text file, which is what a terminal stream is.

    A ``StringIO`` cannot stand in here: click resolves its default stream by
    the object's ``buffer``, which a ``StringIO`` has none of, and that is the
    path a warning took around rich's proxy.
    """
    stream = path.open("w", encoding="utf-8", newline="")
    console = Console(file=stream, force_terminal=True, width=80, color_system="standard", legacy_windows=False)
    return console, stream


@pytest.mark.parametrize("err", [True, False], ids=["stderr", "stdout"])
def test_a_line_printed_while_the_notice_runs_lands_above_it_rather_than_glued_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: bool
) -> None:
    console, stream = _a_terminal_file(tmp_path / "terminal")
    # One terminal behind both streams, as at a shell prompt.
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    notice = StartupNotice(console)

    notice.start()
    safe_console.echo("Warning: a value fell back", err=err)
    notice.stop()
    stream.close()
    drawn = (tmp_path / "terminal").read_text(encoding="utf-8")

    # rich's proxy erases the line before it writes what was printed; a write
    # that went around the proxy follows the spinner's text on the same line.
    assert f"{ERASE_LINE}Warning: a value fell back\n" in drawn, repr(drawn)


def test_an_encoding_python_does_not_know_gets_the_ascii_spinner() -> None:
    assert spinner_for("no-such-encoding") == "line"


def test_a_stopped_notice_leaves_no_line_behind(tmp_path: Path) -> None:
    console, stream = _a_terminal_file(tmp_path / "terminal")
    notice = StartupNotice(console)

    notice.start()
    notice.stop()
    stream.close()
    drawn = (tmp_path / "terminal").read_text(encoding="utf-8")

    # Erased: the last thing written is the erase of the line it was drawn on,
    # not the line drawn once more for good.
    assert drawn.endswith(ERASE_LINE), repr(drawn)


class _TerminalBuffer(io.StringIO):
    """A buffer rich takes for a terminal, so a notice drawn on it is seen."""

    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("platform", "drawn_on"),
    [
        pytest.param("linux", "__stderr__", id="off Windows, the stream the view draws on"),
        pytest.param("win32", "stdout", id="on Windows, stdout"),
    ],
)
def test_the_notice_is_drawn_on_the_stream_the_view_will_draw_on(
    monkeypatch: pytest.MonkeyPatch, platform: str, drawn_on: str
) -> None:
    streams = {name: _TerminalBuffer() for name in ("stdout", "stderr", "__stderr__")}
    for name, buffer in streams.items():
        monkeypatch.setattr(sys, name, buffer)
    monkeypatch.setattr(sys, "platform", platform)
    # rich draws no live display on a dumb terminal, so the line would be on no stream.
    monkeypatch.delenv("TERM", raising=False)

    notice = notice_for_this_start([], not_terminals=nothing_is_redirected)
    notice.start()
    notice.stop()

    assert [name for name, buffer in streams.items() if LINE in buffer.getvalue()] == [drawn_on]
