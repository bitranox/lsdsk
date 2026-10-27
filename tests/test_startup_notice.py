"""The line an interactive start shows while lsdsk imports and reads the machine.

Measured on a slow Windows guest: after uvx prints its install line, the CLI
import takes about 1.8 s, the interactive view's import up to 1.5 s more and the
scan 1.3 s, with nothing on the screen. These tests hold what fills that gap and
that it never reaches a run somebody is not sitting at.
"""

from __future__ import annotations

import io
import sys

import pytest
from rich.console import Console

from lsdsk.adapters import startup_notice
from lsdsk.adapters.startup_notice import NoStartupNotice, StartupNotice, notice_for_this_start, spinner_for

LINE = "Scanning Drives ..."


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


def test_the_command_names_the_start_rule_knows_are_the_commands_the_cli_has() -> None:
    from lsdsk.adapters.cli import cli  # the CLI is the slow import this module exists to run ahead of

    assert frozenset(cli.commands) == startup_notice.COMMAND_NAMES


def test_only_tui_among_the_commands_opens_the_view() -> None:
    assert [name for name in sorted(startup_notice.COMMAND_NAMES) if startup_notice.opens_the_view(name)] == ["tui"]
    assert startup_notice.opens_the_view(None)
