"""The line an interactive start shows while lsdsk imports and reads the machine.

After uvx prints its install line, an interactive start used to show nothing
for several seconds. Measured on a slow Windows guest: the CLI import takes
about 1.8 s, the interactive view's import up to 1.5 s more and the scan
1.3 s. So the line starts in the entry point, BEFORE the CLI import, and stays
until the view takes the screen.

It is drawn with rich, imported here ahead of the CLI that imports it anyway:
measured on the same guest, rich's console and status cost 0.25 s up front
and the CLI import shrinks by about as much, so the start is no slower. rich's
live display swaps stdout and stderr for proxies while it runs, so a warning
printed during the start (a configuration value that fell back, a counter
history this machine cannot use) lands above the line instead of tangling
with it, and the original streams come back on pause and stop.

System Role:
    Adapter layer. Outside ``adapters/cli`` because the entry point imports it
    before the CLI; ``lsdsk.adapters.cli`` is the whole command line.

Contents:
    * :data:`COMMAND_NAMES` - every subcommand, so the start rule can tell one from the view.
    * :func:`opens_the_view` - whether a subcommand (or none) opens the interactive view.
    * :func:`spinner_for` - the spinner a stream of this encoding can draw.
    * :class:`StartupNotice` - the line and its spinner, paused around prompts.
    * :class:`NoStartupNotice` - the same steps, doing nothing, for every other start.
    * :func:`notice_for_this_start` - which of the two this start gets.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from .terminal_streams import drawing_stream, streams_that_are_not_terminals

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: What the line says, as the user asked for it.
LINE = "Scanning Drives ..."

#: Bold green, the line and the spinner beside it.
STYLE = "bold green"

#: Every subcommand the CLI registers. A test holds this to the CLI's own registry,
#: because the rule has to be decided before the CLI is imported.
COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "config",
        "config-deploy",
        "config-generate-examples",
        "controllers",
        "disks",
        "fail",
        "filesystems",
        "findings",
        "health",
        "info",
        "install-shortcut",
        "logdemo",
        "record",
        "report",
        "slots",
        "smart",
        "snapshot",
        "topology",
        "trend",
        "tui",
    }
)

#: The one subcommand that opens the interactive view, as a bare run does.
_VIEW_COMMAND = "tui"

#: Options that print something and leave without opening the view.
_LEAVES_WITHOUT_THE_VIEW = frozenset({"--help", "-h", "--version"})

#: Frames per second; enough to look alive, few enough to cost nothing.
_REFRESH_PER_SECOND = 10


def opens_the_view(command: str | None) -> bool:
    """Whether a run naming this subcommand opens the interactive view.

    Args:
        command: The subcommand click resolved, or None for a bare run.

    Returns:
        True for a bare run and for ``tui``.

    Example:
        >>> opens_the_view(None), opens_the_view("tui"), opens_the_view("disks")
        (True, True, False)
    """
    return command is None or command == _VIEW_COMMAND


def spinner_for(encoding: str) -> str:
    """The rich spinner a stream of this encoding can draw.

    Args:
        encoding: The stream's encoding name.

    Returns:
        ``"dots"`` (braille) where the encoding can carry it, otherwise
        ``"line"``, which is ASCII, so a cp1252 console gets a spinner too.

    Example:
        >>> spinner_for("utf-8"), spinner_for("cp1252")
        ('dots', 'line')
    """
    try:
        chr(0x280B).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "line"
    return "dots"


class StartupNotice:
    """The line and its spinner, drawn until the interactive view takes the screen.

    Every step may be taken more than once and in any order, and ``stop`` is
    final, so a caller that stops it on one path and again in a ``finally``
    cannot bring it back.
    """

    def __init__(self, console: Console) -> None:
        """Hold the console the line is drawn on.

        Args:
            console: A console on the stream the interactive view draws on.
        """
        self._console = console
        self._live: Live | None = None
        self._stopped = False

    def start(self) -> None:
        """Draw the line now."""
        self.resume()

    def resume(self) -> None:
        """Draw the line again after a pause; nothing once stopped or already drawn."""
        if self._stopped or self._live is not None:
            return
        spinner = Spinner(spinner_for(self._console.encoding), text=Text(LINE, style=STYLE), style=STYLE)
        live = Live(
            spinner,
            console=self._console,
            transient=True,
            refresh_per_second=_REFRESH_PER_SECOND,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        live.start(refresh=True)
        self._live = live

    def pause(self) -> None:
        """Erase the line and give the streams back, so a prompt starts on a clean line."""
        if self._live is None:
            return
        live, self._live = self._live, None
        live.stop()

    def stop(self) -> None:
        """Erase the line for good."""
        self.pause()
        self._stopped = True


class NoStartupNotice:
    """The same four steps, doing nothing: a start that is not the interactive view."""

    def start(self) -> None:
        """Nothing to draw."""

    def resume(self) -> None:
        """Nothing to draw."""

    def pause(self) -> None:
        """Nothing to erase."""

    def stop(self) -> None:
        """Nothing to erase."""


def notice_for_this_start(
    argv: Sequence[str],
    *,
    not_terminals: Callable[[], list[str]] = streams_that_are_not_terminals,
    console: Callable[[], Console] | None = None,
) -> StartupNotice | NoStartupNotice:
    """Decide, before the CLI is imported, whether this start shows the line.

    Only a start that is going to open the interactive view shows it: every
    stream the view needs is a terminal (the router's own rule), and the
    arguments ask for neither another subcommand nor ``--help``/``--version``.
    A guess this rule gets wrong is corrected once click has parsed the run,
    which stops the notice for any subcommand but ``tui``.

    Args:
        argv: The arguments after the program name.
        not_terminals: Names the needed streams that are not terminals.
        console: Builds the console to draw on; one on the view's drawing
            stream when not given.

    Returns:
        A :class:`StartupNotice` for an interactive start, otherwise a
        :class:`NoStartupNotice`.
    """
    if any(arg in _LEAVES_WITHOUT_THE_VIEW for arg in argv):
        return NoStartupNotice()
    if any(arg in COMMAND_NAMES and not opens_the_view(arg) for arg in argv):
        return NoStartupNotice()
    if not_terminals():
        return NoStartupNotice()
    return StartupNotice(console() if console is not None else _console_on_the_drawing_stream())


def _console_on_the_drawing_stream() -> Console:
    """A console on the stream the interactive view will draw on: stderr off Windows, stdout on it."""
    drawing = drawing_stream()
    stream = drawing[1] if drawing is not None else sys.stdout
    return Console(file=stream, highlight=False)


__all__ = [
    "COMMAND_NAMES",
    "LINE",
    "NoStartupNotice",
    "StartupNotice",
    "notice_for_this_start",
    "opens_the_view",
    "spinner_for",
]
