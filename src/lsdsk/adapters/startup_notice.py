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

A ``--set`` is judged here by the rules :func:`lsdsk.adapters.config.overrides.apply_overrides`
refuses by, rather than by calling it: importing that module brings in the
configuration package and the domain models, which measured 0.19-0.20 s on the
development box against 0.09-0.14 s for this whole module, and the line exists
to be on the screen before that wait. So the keys of the sections this tool
owns are carried here as :data:`OWNED_SETTINGS`, and a test holds them, and the
whole judgement, to the real one.

System Role:
    Adapter layer. Outside ``adapters/cli`` because the entry point imports it
    before the CLI; ``lsdsk.adapters.cli`` is the whole command line.

Contents:
    * :data:`ROOT_OPTIONS_TAKING_A_VALUE` - the root options whose next word is their value.
    * :data:`VIEW_OPTIONS_TAKING_A_VALUE` - ``tui``'s own options whose next word is their value.
    * :data:`VIEW_FLAGS` - ``tui``'s own options that take no value.
    * :data:`OWNED_SETTINGS` - the sections ``--set`` may only name known keys of, with those keys.
    * :func:`opens_the_view` - whether a subcommand (or none) opens the interactive view.
    * :data:`ROOT_FLAGS` - the root options that take no value.
    * :data:`TREE_DENSITIES` - what ``--tree-density`` takes.
    * :func:`command_word` - the word click will read as the subcommand.
    * :func:`root_value_accepted` - whether the root group takes a value for one of its options.
    * :func:`refuses_a_root_option` - whether the root options end the run in a refusal.
    * :func:`refuses_a_view_option` - whether ``tui``'s own options end the run in a refusal.
    * :func:`spinner_for` - the spinner a stream of this encoding can draw.
    * :class:`StartupNotice` - the line and its spinner, paused around prompts.
    * :class:`NoStartupNotice` - the same steps, doing nothing, for every other start.
    * :func:`notice_for_this_start` - which of the two this start gets.
"""

from __future__ import annotations

import difflib
import os
import signal
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import orjson
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

#: ``tui``'s own options that take a value. Held by a test to the command's own.
VIEW_OPTIONS_TAKING_A_VALUE: frozenset[str] = frozenset({"--format", "--replay"})

#: ``tui``'s own options that take no value. Held by the same test.
VIEW_FLAGS: frozenset[str] = frozenset({"--expand-virtual", "--help", "-h"})

#: The sections a ``--set`` may name only a known key of, and those keys.
#:
#: The configuration derives these from its models; importing them here would
#: cost the start the wait this line exists to cover, so they are written out
#: and a test holds them to ``known_keys.owned_sections()``.
OWNED_SETTINGS: Final[Mapping[str, frozenset[str]]] = {
    "display": frozenset(
        {
            "auto_elevate",
            "detail_height_percent",
            "expand_virtual",
            "offer_desktop_shortcut",
            "piped_width",
            "summary_limit",
            "traceback_summary_limit",
            "traceback_verbose_limit",
            "tree_density",
            "wear_row_floor_percent",
            "wwn_width",
        }
    ),
    "history": frozenset({"enabled", "max_samples_per_drive", "path"}),
    "thresholds": frozenset(
        {
            "crc_errors_significant",
            "filesystem_full_hint_percent",
            "filesystem_full_warning_percent",
            "inodes_full_hint_percent",
            "inodes_full_warning_percent",
            "min_span_hours",
            "mixed_firmware_threshold",
            "quiet_expected_min",
            "wear_critical_percent",
            "wear_projection_min_points",
            "wear_warning_percent",
        }
    ),
}

#: The deepest a ``--set`` may nest, section and key path included: ``overrides.MAX_OVERRIDE_DEPTH``.
_MAX_SETTING_DEPTH: Final = 95

#: How close a section has to be to an owned one to be refused as its typo: ``known_keys``' own cutoff.
_TYPO_CUTOFF: Final = 0.6

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
    return _split_at_command(argv)[1]


def _split_at_command(argv: Sequence[str]) -> tuple[list[str], str | None, list[str]]:
    """The root options, the command word and the command's own words, as click will split them.

    Args:
        argv: The arguments after the program name.

    Returns:
        The words before the command (without a ``--`` ending them), the
        command or None, and the words after it.

    Example:
        >>> _split_at_command(["--set", "a.b=1", "tui", "--bogus"])
        (['--set', 'a.b=1'], 'tui', ['--bogus'])
        >>> _split_at_command(["--no-elevate", "--", "tui"])
        (['--no-elevate'], 'tui', [])
    """
    words = list(argv)
    index = 0
    while index < len(words):
        word = words[index]
        if word == _END_OF_OPTIONS:
            command = words[index + 1] if index + 1 < len(words) else None
            return words[:index], command, words[index + 2 :]
        if not word.startswith("-"):
            return words[:index], word, words[index + 1 :]
        index += 2 if word in ROOT_OPTIONS_TAKING_A_VALUE else 1
    return words, None, []


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


def _parsed_setting_value(text: str) -> object:
    """A ``--set`` value as the override parser reads it: JSON where it is JSON, the text otherwise."""
    if text == "":
        return text
    try:
        return cast("object", orjson.loads(text))
    except ValueError:  # orjson.JSONDecodeError is one
        return text


def _nesting(value: object) -> int:
    """How many levels of tables and lists `value` holds, walked without recursion as the override parser walks it.

    Example:
        >>> _nesting(1), _nesting([]), _nesting({"a": [1, {"b": 2}]})
        (0, 1, 3)
    """
    deepest = 0
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict):
            children: list[object] = list(cast("dict[str, object]", item).values())
        elif isinstance(item, list):
            children = list(cast("list[object]", item))
        else:
            deepest = max(deepest, depth)
            continue
        deepest = max(deepest, depth + 1)
        pending.extend((child, depth + 1) for child in children)
    return deepest


def _setting_path(raw: str) -> tuple[str, ...] | None:
    """The dotted path a ``--set`` names, or None where the override parser refuses its shape.

    Example:
        >>> _setting_path("display.piped_width=90"), _setting_path("nonsense"), _setting_path("display.=1")
        (('display', 'piped_width'), None, None)
    """
    path, equals, value = raw.partition("=")
    if not equals or "." not in path:
        return None
    parts = tuple(path.split("."))
    if not all(parts) or len(parts) + _nesting(_parsed_setting_value(value)) > _MAX_SETTING_DEPTH:
        return None
    return parts


def _names_an_unknown_owned_key(path: tuple[str, ...]) -> bool:
    """Whether `path` names a key an owned section lacks, or a section one typo from an owned one."""
    section, *keys = path
    known = OWNED_SETTINGS.get(section)
    if known is None:
        return bool(difflib.get_close_matches(section, sorted(OWNED_SETTINGS), n=1, cutoff=_TYPO_CUTOFF))
    return len(keys) != 1 or keys[0] not in known


def _settings_accepted(values: Sequence[str]) -> bool:
    """Whether the root group applies every ``--set`` in `values` together without a usage error.

    Example:
        >>> _settings_accepted(["display.piped_width=90", "lib_log_rich.console_level=DEBUG"])
        True
        >>> _settings_accepted(["display.nonexistent=1"]), _settings_accepted(["a.b=1", "a.b.c=2"])
        (False, False)
    """
    paths = [_setting_path(raw) for raw in values]
    shaped = [path for path in paths if path is not None]
    if len(shaped) != len(paths):
        return False
    # One setting's path inside another's asks for a value and a table at once.
    if any(len(outer) < len(inner) and inner[: len(outer)] == outer for outer in shaped for inner in shaped):
        return False
    return not any(_names_an_unknown_owned_key(path) for path in shaped)


def _a_setting_the_root_group_takes(value: str) -> bool:
    """Whether one ``--set`` is applied without a usage error."""
    return _settings_accepted([value])


#: What each root option's value must be for the root group to take it; any other takes any text.
_ACCEPTS: Final[Mapping[str, Callable[[str], bool]]] = {
    "--env-file": _names_a_readable_file,
    "--history-file": _can_name_a_history_file,
    # The library's own judge, the one the root group's refusal delegates to.
    "--profile": is_valid_profile_name,
    "--replay": _names_a_readable_file,
    "--set": _a_setting_the_root_group_takes,
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


def _values_given(
    words: Sequence[str], *, flags: frozenset[str], taking_a_value: frozenset[str]
) -> dict[str, list[str]] | None:
    """Every value each option among `words` was given, or None where click refuses the words.

    Each word must be an option the command has; a flag takes no value; an
    option that takes one needs it, as the next word or after ``=``. A word that
    is not an option is an argument, which neither the root options nor ``tui``
    take, and ``--`` may end them only with nothing after it.

    Example:
        >>> _values_given(["--a", "1", "--b"], flags=frozenset({"--b"}), taking_a_value=frozenset({"--a"}))
        {'--a': ['1']}
        >>> _values_given(["--b=1"], flags=frozenset({"--b"}), taking_a_value=frozenset()) is None
        True
    """
    given: dict[str, list[str]] = {}
    remaining = iter(words)
    for word in remaining:
        if word == _END_OF_OPTIONS:
            return given if next(remaining, None) is None else None
        name, attached, value = word.partition("=")
        if not word.startswith("-") or (name in flags and attached) or (name not in flags | taking_a_value):
            return None
        if name in flags:
            continue
        found = value if attached else next(remaining, None)
        if found is None:
            return None
        given.setdefault(name, []).append(found)
    return given


def refuses_a_root_option(argv: Sequence[str]) -> bool:
    """Whether this run's root options end it in a refusal before any command runs.

    The root options are the words before the command, judged by
    :func:`_values_given`, and each value must be one the root group accepts.
    For an option given twice, the last value is the one judged, as click keeps
    the last - except ``--set``, which is given as often as it is written and is
    judged as the whole set, because two settings can refuse each other.

    Args:
        argv: The arguments after the program name.

    Returns:
        True where click, or the root group, refuses them with a usage error.

    Example:
        >>> refuses_a_root_option(["--bogus"]), refuses_a_root_option(["--profile"])
        (True, True)
        >>> refuses_a_root_option(["--no-elevate", "--tree-density=full", "disks", "--bogus"])
        False
        >>> refuses_a_root_option(["--set", "nonsense"])
        True
    """
    root, _command, _rest = _split_at_command(argv)
    given = _values_given(root, flags=ROOT_FLAGS, taking_a_value=ROOT_OPTIONS_TAKING_A_VALUE)
    if given is None or not _settings_accepted(given.pop("--set", [])):
        return True
    return not all(root_value_accepted(name, values[-1]) for name, values in given.items())


def refuses_a_view_option(argv: Sequence[str]) -> bool:
    """Whether ``tui``'s own options end this run in a refusal before the view opens.

    Judged as the root options are, by :func:`_values_given`. ``--format`` is
    refused whatever it is given, because the view has no form it could take.

    Args:
        argv: The arguments after the program name.

    Returns:
        True where the run names ``tui`` and click, or ``tui`` itself, refuses
        the words after it; False for any other run.

    Example:
        >>> refuses_a_view_option(["tui", "--bogus"]), refuses_a_view_option(["tui", "extra"])
        (True, True)
        >>> refuses_a_view_option(["tui", "--expand-virtual"]), refuses_a_view_option(["disks", "--bogus"])
        (False, False)
    """
    _root, command, rest = _split_at_command(argv)
    if command != _VIEW_COMMAND:
        return False
    given = _values_given(rest, flags=VIEW_FLAGS, taking_a_value=VIEW_OPTIONS_TAKING_A_VALUE)
    if given is None or "--format" in given:
        return True
    replay = given.get("--replay")
    return replay is not None and not _names_a_readable_file(replay[-1])


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
    have, a value missing or one it does not take, a ``--set`` the root group
    cannot apply (see :func:`refuses_a_root_option`), nor an option of ``tui``'s
    own it refuses (see :func:`refuses_a_view_option`).
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
    if not opens_the_view(command_word(argv)) or refuses_a_root_option(argv) or refuses_a_view_option(argv):
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
    "OWNED_SETTINGS",
    "ROOT_FLAGS",
    "ROOT_OPTIONS_TAKING_A_VALUE",
    "TREE_DENSITIES",
    "VIEW_FLAGS",
    "VIEW_OPTIONS_TAKING_A_VALUE",
    "NoStartupNotice",
    "StartupNotice",
    "command_word",
    "notice_for_this_start",
    "opens_the_view",
    "refuses_a_root_option",
    "refuses_a_view_option",
    "root_value_accepted",
    "spinner_for",
]
