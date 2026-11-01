"""A Ctrl+C that lands while the startup line starts or stops leaves 130, the streams and the cursor.

Starting the line is several steps inside rich - hide the cursor, swap both
standard streams for proxies, push a render hook, start a refresh thread - and
stopping it undoes them. The entry point started the line outside its interrupt
guard, and the notice recorded its live display only once rich's start had
returned, so a Ctrl+C in that window escaped as a traceback with the cursor
hidden. And an interrupt BETWEEN two of rich's steps leaves a state rich's own
stop cannot undo: it pops a render hook that was never pushed and fails before
it shows the cursor again.

The interrupt is a real SIGINT raised at one chosen step, through a console
whose render-hook steps raise it, or the reviewer's own form: rich's
``Live.start`` finishing and then raising ``KeyboardInterrupt``. Both are rich's
code, the edge this project does not own.
"""

from __future__ import annotations

import io
import signal
import sys
import threading
from typing import TYPE_CHECKING

import pytest
from rich.console import Console
from rich.live import Live

from lsdsk import __init__conf__, entry
from lsdsk.adapters.startup_notice import StartupNotice

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import RenderHook

#: The control sequences that hide and show the terminal cursor.
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"

#: A run whose notice the entry point alone stops: ``--version`` leaves before the root group runs.
VERSION = ["--version"]


@pytest.fixture(autouse=True)
def a_ctrl_c_raises_keyboard_interrupt() -> Iterator[None]:
    """Python's own SIGINT handler for the test, whatever the runner installed, then the runner's back."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, previous)


class _InterruptedConsole(Console):
    """A terminal console that receives a real SIGINT at one step of the line's start or stop."""

    def __init__(self, *, at: str) -> None:
        self.drawn = io.StringIO()
        super().__init__(file=self.drawn, force_terminal=True, width=80, color_system="standard")
        self._at = at

    def _interrupt_at(self, step: str) -> None:
        if self._at == step:
            self._at = ""
            signal.raise_signal(signal.SIGINT)

    def push_render_hook(self, hook: RenderHook) -> None:
        # After rich hid the cursor and swapped the streams, before it pushes its hook.
        self._interrupt_at("start")
        super().push_render_hook(hook)

    def pop_render_hook(self) -> None:
        # After rich gave the streams back, before it shows the cursor again.
        self._interrupt_at("stop")
        super().pop_render_hook()


def _run_and_observe(console: _InterruptedConsole) -> tuple[int, bool, bool, bool]:
    """Run the entry point with a real notice on `console` and report what it left.

    Returns:
        The exit code, whether both standard streams are the ones it started
        with, whether the cursor was shown after it was last hidden, and whether
        every thread the run started has ended.
    """
    before = (sys.stdout, sys.stderr)
    threads = set(threading.enumerate())
    try:
        code = entry.main(VERSION, start_notice=lambda argv: StartupNotice(console))
    except KeyboardInterrupt:
        pytest.fail("the interrupt escaped the entry point, which prints a traceback")
    finally:
        streams_back = (sys.stdout, sys.stderr) == before
        # Put them back whatever happened, so a failure here leaves no proxy behind for later tests.
        sys.stdout, sys.stderr = before
    drawn = console.drawn.getvalue()
    cursor_shown = drawn.rfind(SHOW_CURSOR) > drawn.rfind(HIDE_CURSOR) >= 0
    return code, streams_back, cursor_shown, _all_end(set(threading.enumerate()) - threads)


def _all_end(started: set[threading.Thread]) -> bool:
    """Whether every thread in `started` ends: rich's stop asks its refresh thread to end and does not wait."""
    for thread in started:
        thread.join(timeout=5)
    return not any(thread.is_alive() for thread in started)


@pytest.mark.parametrize("step", ["start", "stop"])
def test_a_ctrl_c_between_two_of_rich_s_steps_leaves_130_the_streams_and_the_cursor(step: str) -> None:
    code, streams_back, cursor_shown, threads_ended = _run_and_observe(_InterruptedConsole(at=step))

    assert code == 130
    assert streams_back
    assert cursor_shown
    assert threads_ended


def test_a_ctrl_c_as_rich_s_start_returns_leaves_130_the_streams_and_the_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewer's form: rich's start completes, and the interrupt lands before the notice records it."""
    original = Live.start

    def start_then_interrupt(live: Live, *, refresh: bool = False) -> None:
        original(live, refresh=refresh)
        raise KeyboardInterrupt

    monkeypatch.setattr(Live, "start", start_then_interrupt)

    code, streams_back, cursor_shown, threads_ended = _run_and_observe(_InterruptedConsole(at=""))

    assert code == 130
    assert streams_back
    assert cursor_shown
    assert threads_ended


def test_the_control_an_uninterrupted_run_prints_the_version_and_leaves_0() -> None:
    """Holding the signal must not swallow the run: without an interrupt the version is printed.

    It is printed while the line runs, so it reaches the line's console above the line.
    """
    console = _InterruptedConsole(at="")

    code, streams_back, cursor_shown, threads_ended = _run_and_observe(console)

    assert code == 0
    assert __init__conf__.version in console.drawn.getvalue()
    assert streams_back
    assert cursor_shown
    assert threads_ended
