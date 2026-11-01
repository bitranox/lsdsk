"""The line an interactive start shows while lsdsk imports and reads the machine.

After uvx prints its install line, an interactive start used to show nothing
for several seconds: on a slow machine the CLI import, the interactive view's
import and the scan each take a noticeable part of that. So the line starts in
the entry point, BEFORE the CLI import, and stays until the view takes the
screen.

It is drawn with rich, imported here ahead of the CLI that imports it anyway:
what rich's console and status cost up front, the CLI import no longer pays,
so the start is no slower. rich's live display swaps stdout and stderr for
proxies while it runs, so a warning printed during the start (a configuration
value that fell back, a counter history this machine cannot use) lands above
the line instead of tangling with it, and the original streams come back on
pause and stop.

lib_layered_config is imported here too, for the profile-name check that
decides whether a ``--profile`` is refused: the CLI imports it anyway, so the
start is no slower, though the line now appears after that import rather than
before it.

System Role:
    Adapter layer. Outside ``adapters/cli`` because the entry point imports it
    before the CLI; ``lsdsk.adapters.cli`` is the whole command line.

Contents:
    * :data:`ROOT_OPTIONS_TAKING_A_VALUE` - the root options whose next word is their value.
    * :func:`opens_the_view` - whether a subcommand (or none) opens the interactive view.
    * :data:`ROOT_FLAGS` - the root options that take no value.
    * :data:`TREE_DENSITIES` - what ``--tree-density`` takes.
    * :func:`command_word` - the word click will read as the subcommand.
    * :func:`root_value_accepted` - whether the root group takes a value for one of its options.
    * :func:`refuses_a_root_option` - whether the root options end the run in a refusal.
    * :func:`spinner_for` - the spinner a stream of this encoding can draw.
    * :class:`StartupNotice` - the line and its spinner, paused around prompts.
    * :class:`NoStartupNotice` - the same steps, doing nothing, for every other start.
    * :func:`notice_for_this_start` - which of the two this start gets.
"""

from __future__ import annotations

import os
import signal
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final

from lib_layered_config import is_valid_profile_name
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from .terminal_streams import drawing_stream, streams_that_are_not_terminals

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence

#: What the line says, as the user asked for it.
LINE = "Scanning Drives ..."

#: Bold green, the line and the spinner beside it.
STYLE = "bold green"

#: The root options that take a value, so the word after one is that value and
#: not the command. A test holds this to the CLI's own options, because the rule
#: has to be decided before the CLI is imported.
ROOT_OPTIONS_TAKING_A_VALUE: frozenset[str] = frozenset(
    {"--env-file", "--history-file", "--profile", "--replay", "--set", "--tree-density"}
)

#: The root options that take no value. Held by the same test to the CLI's own.
ROOT_FLAGS: frozenset[str] = frozenset(
    {
        "--elevated-relaunch",
        "--expand-virtual",
        "--help",
        "--no-elevate",
        "--no-record",
        "--no-traceback",
        "--traceback",
        "--version",
        "-h",
    }
)

#: What ``--tree-density`` takes, held by a test to the CLI's own choices.
TREE_DENSITIES: frozenset[str] = frozenset({"full", "storage-and-siblings", "storage-only"})

#: The one subcommand that opens the interactive view, as a bare run does.
_VIEW_COMMAND = "tui"

#: click's own end of the options: the word after it is the command whatever it looks like.
_END_OF_OPTIONS = "--"

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


def command_word(argv: Sequence[str]) -> str | None:
    """The word click will read as the subcommand, found without importing the CLI.

    The root options come first, so the first word that is neither an option
    nor the value of one is the command. A word that names no command is
    returned as it is: click refuses it with a usage error.

    Args:
        argv: The arguments after the program name.

    Returns:
        The command word, or None for a run that names none.

    Example:
        >>> command_word(["--replay", "capture.json", "disks"])
        'disks'
        >>> command_word(["--no-elevate"]) is None
        True
        >>> command_word(["--profile", "disks"]) is None
        True
    """
    words = iter(argv)
    for word in words:
        if word == _END_OF_OPTIONS:
            return next(words, None)
        if not word.startswith("-"):
            return word
        if word in ROOT_OPTIONS_TAKING_A_VALUE:
            next(words, None)
    return None


def _a_readable_non_directory(value: str) -> bool | None:
    """Whether `value` names a readable non-directory, as click's path type tests it; None where nothing is there.

    Where the stat fails is where click's own check calls the path missing,
    and an embedded NUL fails it with ``ValueError`` rather than ``OSError``.
    """
    try:
        found = Path(value).stat()
    except (OSError, ValueError):
        return None
    return not stat.S_ISDIR(found.st_mode) and os.access(value, os.R_OK)


def _names_a_readable_file(value: str) -> bool:
    """Whether `value` can be ``--replay`` or ``--env-file``: a path that is there, not a directory, readable."""
    return value != "" and _a_readable_non_directory(value) is True


def _can_name_a_history_file(value: str) -> bool:
    """Whether `value` can be ``--history-file``: empty (the default), not there yet, or a readable non-directory."""
    return value == "" or _a_readable_non_directory(value) is not False


def _names_a_density(value: str) -> bool:
    """Whether `value` is one of the densities, in any case, as ``--tree-density`` compares them."""
    return value.casefold() in {density.casefold() for density in TREE_DENSITIES}


#: What each root option's value must be for the root group to take it; any other takes any text.
_ACCEPTS: Final[Mapping[str, Callable[[str], bool]]] = {
    "--env-file": _names_a_readable_file,
    "--history-file": _can_name_a_history_file,
    # The library's own judge, the one the root group's refusal delegates to.
    "--profile": is_valid_profile_name,
    "--replay": _names_a_readable_file,
    "--tree-density": _names_a_density,
}


def root_value_accepted(option: str, value: str) -> bool:
    """Whether the root group accepts `value` for the root `option`, judged without importing the CLI.

    A test asks the CLI the same question for every option and a spread of
    values, so the two cannot come to disagree unseen.

    Args:
        option: A root option that takes a value.
        value: What the command line gave it.

    Returns:
        False where the run would end in a refusal of this value.

    Example:
        >>> root_value_accepted("--tree-density", "FULL"), root_value_accepted("--tree-density", "dense")
        (True, False)
    """
    accepts = _ACCEPTS.get(option)
    return accepts is None or accepts(value)


def refuses_a_root_option(argv: Sequence[str]) -> bool:
    """Whether this run's root options end it in a refusal before any command runs.

    The root options are the words before the command. Each must be one the
    root group has; a flag takes no value; an option that takes one needs it,
    as the next word or after ``=``, and needs one it accepts. For an option
    given twice, the last value is the one judged, as click keeps the last.

    Args:
        argv: The arguments after the program name.

    Returns:
        True where click, or the root group, refuses them with a usage error.

    Example:
        >>> refuses_a_root_option(["--bogus"]), refuses_a_root_option(["--profile"])
        (True, True)
        >>> refuses_a_root_option(["--no-elevate", "--tree-density=full", "disks", "--bogus"])
        False
    """
    given: dict[str, str] = {}
    words = iter(argv)
    for word in words:
        if word == _END_OF_OPTIONS or not word.startswith("-"):
            break
        name, attached, value = word.partition("=")
        if name in ROOT_FLAGS and not attached:
            continue
        if name not in ROOT_OPTIONS_TAKING_A_VALUE:
            return True
        found = value if attached else next(words, None)
        if found is None:
            return True
        given[name] = found
    return not all(root_value_accepted(name, value) for name, value in given.items())


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


@contextmanager
def _ctrl_c_held() -> Generator[None]:
    """Hold a Ctrl+C that arrives inside the block, and deliver it once the block is done.

    rich's live display starts and stops in several steps - the cursor, both
    standard streams, the console's render hooks, a refresh thread - and an
    interrupt between two of them leaves a state its own stop cannot undo:
    measured, one landing before the render hook was pushed made the stop fail
    popping it, and one landing before the cursor was shown again left it
    hidden. Held, each start and stop is whole, and the interrupt still
    arrives, through whatever handler was in place.

    Only the main thread may install a handler, and one installed outside
    Python cannot be put back, so the block runs unheld in either case.
    """
    previous = signal.getsignal(signal.SIGINT)
    if previous is None or threading.current_thread() is not threading.main_thread():
        yield
        return
    held: list[int] = []
    signal.signal(signal.SIGINT, lambda signum, _frame: held.append(signum))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if held:
        signal.raise_signal(signal.SIGINT)


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
        with _ctrl_c_held():
            # Recorded first, so a stop reaches a display whatever ends its start.
            self._live = live
            live.start(refresh=True)

    def pause(self) -> None:
        """Erase the line and give the streams back, so a prompt starts on a clean line."""
        if self._live is None:
            return
        live, self._live = self._live, None
        with _ctrl_c_held():
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
    A word that names no command at all - a typo - shows nothing either: click
    refuses it at once, and the line would only flash around the usage error.
    Nor does a root option click or the root group refuses: one it does not
    have, a value missing or one it does not take (see
    :func:`refuses_a_root_option`).
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
    if not opens_the_view(command_word(argv)) or refuses_a_root_option(argv):
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
    "LINE",
    "ROOT_FLAGS",
    "ROOT_OPTIONS_TAKING_A_VALUE",
    "TREE_DENSITIES",
    "NoStartupNotice",
    "StartupNotice",
    "command_word",
    "notice_for_this_start",
    "opens_the_view",
    "refuses_a_root_option",
    "root_value_accepted",
    "spinner_for",
]
