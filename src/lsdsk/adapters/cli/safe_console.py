"""Encode-safe console output.

Purpose
-------
Wraps :func:`click.echo` so a console whose codepage cannot represent a glyph
degrades that glyph instead of aborting the command.

Why
---
Console output is a sink with an encoding the program does not choose. Python
hands stdout to a Windows console at codepage 1252 with ``errors="strict"``, so
writing ``✓`` raises ``UnicodeEncodeError: 'charmap' codec can't encode
character '\\u2713'`` and the command exits non-zero -- after its real work has
already succeeded, which is the part that misleads. ``click.echo`` does not
protect against this; the exception propagates.

Degrading at the SINK keeps the glyphs where they are wanted: an email body or
a UTF-8 terminal still receives ``✓``, and only a stream that genuinely cannot
encode it sees ``[OK]``. Callers therefore write the glyph they mean and never
branch on the platform.

Contents
--------
* :data:`ASCII_FALLBACKS` - the glyph-to-ASCII map
* :func:`ascii_fallback` - transliterate text for a target encoding
* :func:`encode_safe` - degrade text only when the encoding rejects it
* :func:`echo` - the :func:`click.echo` replacement every module uses
* :func:`safe_stream` - the same protection for a writer this module does not
  own, such as the one a :class:`rich.console.Console` writes through
* :func:`is_broken_pipe` - whether a failed write means the reader left
* :func:`flush_streams_or_leave` - deliver buffered output while a handler can
  still see it fail
* :func:`restore_original_streams` - give a library caller back the descriptors
  this module pointed at the null device
* :func:`write_unless_the_reader_left` - attempt a diagnostic write without
  letting a departed reader become the answer
"""

from __future__ import annotations

import contextlib
import errno
import os
import sys
from typing import IO, TYPE_CHECKING, Any, Final, NoReturn, TextIO, cast

import rich_click as click

from .exit_codes import ExitCode, outranks_a_departed_reader

if TYPE_CHECKING:
    from collections.abc import Callable

ASCII_FALLBACKS: Final[dict[str, str]] = {
    "✓": "[OK]",  # check mark
    "✔": "[OK]",  # heavy check mark
    "✅": "[OK]",  # white heavy check mark
    "✗": "[X]",  # ballot X
    "✘": "[X]",  # heavy ballot X
    "❌": "[X]",  # cross mark
    "⚠": "[!]",  # warning sign
    "️": "",  # variation selector 16, trails an emoji glyph and carries no text
    "•": "-",  # bullet
    "≥": ">=",
    "≤": "<=",
    "→": "->",
    "←": "<-",
    # The quotation marks are spelled as escapes on purpose: written literally
    # they are indistinguishable from ASCII ' and " in most editors, which is
    # exactly the confusion ruff's RUF001 exists to flag.
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "…": "...",
    # The tree's rules. One character each, so a degraded spine keeps its width
    # and the columns after it do not move on the console the fallback is for.
    "\u2502": "|",  # box drawings light vertical
    "\u251c": "|",  # box drawings light vertical and right
    "\u2514": "'",  # box drawings light up and right
    "\u2500": "-",  # box drawings light horizontal
    "\u252c": "+",  # box drawings light down and horizontal
}

#: Encodings that represent every code point, so the check can be skipped.
_UNIVERSAL_ENCODINGS: Final[frozenset[str]] = frozenset({"utf-8", "utf8", "utf-16", "utf16", "utf-32", "utf32"})


def _stream_encoding(file: IO[Any] | None, *, err: bool = False) -> str | None:
    """Return the target stream's encoding, or None when it cannot be determined.

    An unknown encoding means the caller gets the original text: guessing would
    degrade output that may well have been fine.

    With no explicit `file` the answer comes from ``sys.stdout``/``sys.stderr``,
    which is what :func:`click.echo` resolves its own default target from. click
    exposes no supported way to ask for that stream: ``get_text_stream`` was
    deprecated in click 8.5.0 and is removed in 9.0, its documented replacement
    being to let ``echo`` resolve the stream itself.
    """
    stream = file if file is not None else (sys.stderr if err else sys.stdout)
    encoding = getattr(stream, "encoding", None)
    return encoding if isinstance(encoding, str) else None


#: Descriptors this module pointed at the null device, against what they named before.
#:
#: Process-global because a file DESCRIPTOR is, so recording it anywhere narrower
#: would describe something other than the thing that was changed.
_REDIRECTED_DESCRIPTORS: Final[dict[int, int]] = {}


def _stop_writing_to(stream: IO[Any] | None) -> None:
    """Point the descriptor behind `stream` at the null device.

    Python flushes ``sys.stdout`` and ``sys.stderr`` as the interpreter exits. On
    a pipe whose reader has gone that raises a SECOND ``BrokenPipeError``, after
    the exit code has already been decided, and prints "Exception ignored while
    flushing sys.stdout" at somebody who did nothing wrong. Replacing the file
    DESCRIPTOR rather than rebinding the module attribute is what makes that
    hold: the original stream object is still flushed on the way down, and it
    writes through the descriptor.

    Only ``sys.stdout`` and ``sys.stderr`` are touched, because those two are the
    whole reason this exists - they are what the interpreter flushes on its way
    down. Redirecting any other stream a caller happened to pass would be this
    module rearranging a file it does not own. Nulling the WRONG one of the two
    is the defect this argument exists to stop: measured, a closed stderr pointed
    stdout at the null device, which both discarded a report that was being read
    perfectly well (8,876 bytes delivered as 1) and left stderr's own failed
    write to be retried at shutdown, where 120 overrode the 141 just decided.

    The stream is FLUSHED once redirected, which is what empties the buffer whose
    retry would otherwise fail; it goes to the null device, so it cannot.

    A stream with no descriptor - a test harness's buffer, a captured stream -
    needs none of this and is left alone.

    Args:
        stream: The stream whose write failed.

    Side Effects:
        Replaces a process file descriptor, recording what it named so
        :func:`restore_original_streams` can put it back.
    """
    if stream is None or (stream is not sys.stdout and stream is not sys.stderr):
        return
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return
    if descriptor in _REDIRECTED_DESCRIPTORS:
        return
    try:
        saved = os.dup(descriptor)
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:  # pragma: no cover - the null device is always openable
        return
    try:
        os.dup2(null, descriptor)
    finally:
        os.close(null)
    _REDIRECTED_DESCRIPTORS[descriptor] = saved
    with contextlib.suppress(OSError, ValueError):
        stream.flush()


def restore_original_streams() -> None:
    """Put back every descriptor :func:`_stop_writing_to` redirected.

    ``main`` RETURNS its exit code rather than exiting, so everything it did to
    the process outlives the call. ``entry.py`` exits immediately afterwards and
    never notices; a caller that imports ``main`` and carries on would get
    control back with its own fd 1 pointed at the null device, and nothing would
    raise to say so.

    Safe to run on the way out of the real process too: the redirected stream was
    flushed when it was redirected, so its buffer is empty and the interpreter's
    own flush writes nothing through the descriptor this hands back.

    Side Effects:
        Restores process file descriptors and closes the saved duplicates.
    """
    while _REDIRECTED_DESCRIPTORS:
        descriptor, saved = _REDIRECTED_DESCRIPTORS.popitem()
        try:
            os.dup2(saved, descriptor)
        except OSError:  # pragma: no cover - the saved descriptor is this process's own
            pass
        finally:
            with contextlib.suppress(OSError):
                os.close(saved)


def write_unless_the_reader_left(emit: Callable[[], None], *, err: bool = True) -> None:
    """Run `emit`, and give up quietly if the reader of its stream has gone.

    For a DIAGNOSTIC write on the way out of a run that has ALREADY decided its
    exit code - click's usage message, a traceback. A broken pipe there must not
    become the answer, and must not escape: the run's own code is what the caller
    asked about, and it is a refusal, which
    :func:`~.exit_codes.outranks_a_departed_reader` says stands.

    Measured before this existed: ``lsdsk nosuchcommand 2>&1 | head`` left 120.
    click raised ``UsageError``, the ``BrokenPipeError`` from printing it was
    raised INSIDE the handler that was printing it, so it escaped ``_run_cli``
    and then ``main`` itself, and the interpreter reported its own shutdown
    flush failure instead of the usage error the caller needed.

    Args:
        emit: The write to attempt. It is expected to raise nothing but an
            ``OSError`` for a stream that has gone.
        err: Whether `emit` writes to stderr, which is where diagnostics go.

    Side Effects:
        Points the written stream at the null device when its reader has left,
        so the interpreter's own exit flush cannot fail afterwards.
    """
    try:
        emit()
    except OSError as exc:
        if not is_broken_pipe(exc):
            raise
        _stop_writing_to(sys.stderr if err else sys.stdout)


def _reader_went_away(stream: IO[Any] | None) -> NoReturn:
    """Leave with the code that says the pipe closed, never the one that says a drive is failing.

    Raised rather than returned so it cannot be forgotten at a call site, and
    raised as ``SystemExit`` on purpose: click catches ``OSError`` with
    ``errno.EPIPE`` in its own ``main`` and calls ``sys.exit(1)``, which is this
    tool's code for an actionable finding. Reporting a hardware fault because
    somebody piped the output into ``head`` is the defect; exiting before click
    can see an ``OSError`` is the fix, and ``SystemExit`` passes through its
    handler untouched.

    Args:
        stream: The stream whose write failed, so the descriptor that is
            silenced is the one that actually broke.
    """
    _stop_writing_to(stream)
    raise SystemExit(ExitCode.BROKEN_PIPE)


def is_broken_pipe(exc: BaseException, *, on_windows: bool | None = None) -> bool:
    """Whether a failed write means the reader went away.

    ``BrokenPipeError`` covers ``EPIPE`` and ``ESHUTDOWN``, which is the whole of
    it on POSIX. On Windows a broken pipe can arrive as a plain ``OSError``
    carrying ``EINVAL`` instead (bpo-19612, bpo-30418), which a handler naming
    only ``BrokenPipeError`` never sees. Measured on Windows (Python 3.14.6), a
    reader closing the pipe left 120 without this - CPython's interpreter-shutdown
    flush failure, which is not an ``ExitCode`` member and appears in no document -
    and 141 with it. Reading the mapping alone predicts 22, since
    ``get_system_exit_code`` passes that errno through and 22 is this tool's
    ``INVALID_ARGUMENT``; end to end the shutdown flush fails afterwards and
    overrides it. Either code tells the caller a cause that did not happen.

    The errno is accepted only ON WINDOWS, which is also what pip's own
    ``_is_broken_pipe_error`` does (it returns early unless ``WINDOWS``). Accepted
    everywhere, a genuine ``EINVAL`` on a POSIX write would be reported as exit
    141 with the real error swallowed.

    Args:
        exc: The exception a write raised.
        on_windows: Whether to apply the Windows reading. Defaults to asking this
            interpreter, and is a parameter so a Linux cell can prove the Windows
            branch - the end-to-end pipe test that would catch it for real is the
            one no Windows runner has executed.

    Returns:
        Whether to treat this as the reader leaving.

    Example:
        >>> is_broken_pipe(BrokenPipeError(), on_windows=False)
        True
        >>> invalid = OSError(); invalid.errno = errno.EINVAL
        >>> is_broken_pipe(invalid, on_windows=True), is_broken_pipe(invalid, on_windows=False)
        (True, False)
    """
    if isinstance(exc, BrokenPipeError):
        return True
    windows = sys.platform.startswith("win") if on_windows is None else on_windows
    return windows and isinstance(exc, OSError) and exc.errno in {errno.EINVAL, errno.EPIPE}


def _delivered(stream: IO[Any] | None) -> bool:
    """Flush `stream`, reporting whether its reader was still there.

    Args:
        stream: The stream to deliver.

    Returns:
        Whether the flush reached a reader. A stream that cannot be flushed at
        all counts as delivered: there is no pipe behind it to break.

    Raises:
        OSError: When the flush failed for any reason other than the reader
            leaving, which is a real error and belongs to the caller.
    """
    try:
        if stream is not None:
            stream.flush()
    except ValueError:  # pragma: no cover - a closed test harness buffer
        return True
    except OSError as exc:
        if not is_broken_pipe(exc):
            raise
        _stop_writing_to(stream)
        return False
    return True


def flush_streams_or_leave(code: int) -> int:
    """Deliver anything still buffered, ranking the answer against a departed reader.

    Python block-buffers stdout off a terminal, so a command whose whole output
    fits the buffer never touches the pipe while it runs. Measured: ``lsdsk --help``
    wrote 7,111 bytes, ``cli.main()`` returned NORMALLY with both streams untouched,
    and the interpreter's own exit flush then failed - leaving 120, CPython's
    shutdown-flush code, which is not an :class:`ExitCode` member, appears in no
    document this tool ships, and happens after every handler has run. Flushing here
    moves that failure to a point the guard can still see.

    BOTH streams, not stdout alone. ``lsdsk ... 2>&1 | head`` is one pipe carrying
    both, so the reader leaving breaks both at once, and a stderr write that failed
    during the run leaves its bytes in the buffer to be retried at shutdown. Measured
    before this flushed stderr too: 120, from exactly that retry.

    What it answers with is :func:`~.exit_codes.outranks_a_departed_reader`: a code
    saying the run could not start stands, a code saying what the output contained
    yields to ``BROKEN_PIPE``.

    Args:
        code: What the run decided to leave with.

    Returns:
        That same code when every flush succeeds, so an ordinary run is untouched.

    Side Effects:
        Flushes stdout and stderr, and points either at the null device if its reader
        has gone, so the interpreter's own flush cannot fail afterwards and override
        this answer.
    """
    reached_a_reader = [_delivered(sys.stdout), _delivered(sys.stderr)]
    if all(reached_a_reader):
        return code
    return code if outranks_a_departed_reader(code) else int(ExitCode.BROKEN_PIPE)


def ascii_fallback(text: str, encoding: str) -> str:
    """Rewrite `text` so it survives `encoding`.

    Known glyphs become their ASCII equivalent from :data:`ASCII_FALLBACKS`;
    anything else the codec still cannot represent becomes ``?``. Text the
    encoding already accepts is returned unchanged.

    Parameters
    ----------
    text:
        The message as the caller wrote it.
    encoding:
        The target stream's encoding, e.g. ``"cp1252"``.

    Returns
    -------
    str
        A string that :meth:`str.encode` accepts for `encoding`.
    """
    mapped = "".join(ASCII_FALLBACKS.get(character, character) for character in text)
    return mapped.encode(encoding, errors="replace").decode(encoding)


def encode_safe(text: str, encoding: str | None) -> str:
    """Return `text` if `encoding` accepts it, else its ASCII fallback.

    The check runs BEFORE the write on purpose. Writing first and catching
    ``UnicodeEncodeError`` would leave the already-encoded prefix on the stream,
    so the retry would duplicate it.
    """
    if encoding is None or encoding.lower() in _UNIVERSAL_ENCODINGS:
        return text
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return ascii_fallback(text, encoding)
    return text


def echo(message: object = "", *, file: IO[Any] | None = None, err: bool = False, nl: bool = True) -> None:
    """Write `message` to the console, degrading anything it cannot encode.

    Drop-in for :func:`click.echo` for the arguments this project uses.

    Parameters
    ----------
    message:
        The text to write. Non-string values are stringified as click does.
    file:
        Target stream. Defaults to click's stdout (or stderr when `err`).
    err:
        Write to stderr instead of stdout.
    nl:
        Append a newline.

    Side Effects
    ------------
    Writes to the given stream.
    """
    text = message if isinstance(message, str) else str(message)
    target = file if file is not None else (sys.stderr if err else sys.stdout)
    try:
        click.echo(encode_safe(text, _stream_encoding(file, err=err)), file=file, err=err, nl=nl)
    except OSError as exc:
        if not is_broken_pipe(exc):
            raise
        if target is sys.stderr:
            # A departed STDERR reader is not a reason to stop. stderr carries
            # diagnostics about the run, never the run's own output, so nobody
            # listening to it means this one message is lost and the command's
            # own verdict still stands - which is the same ranking
            # :func:`~.exit_codes.outranks_a_departed_reader` applies at the
            # boundary. Aborting here instead answered `lsdsk config-deploy
            # ... 2>&1 | head` with 141 and threw away the 13 that says exactly
            # what went wrong. A departed STDOUT reader does stop the run: that
            # stream IS what was asked for.
            _stop_writing_to(target)
            return
        _reader_went_away(target)


class _SafeWriter:
    """A text stream that degrades what the wrapped stream cannot encode.

    Why
        Rich renders through a writer this module does not control, and it
        raises the same ``UnicodeEncodeError`` on a legacy codepage rather than
        substituting. Wrapping the writer applies the fallback to every segment
        rich emits without rich needing to know.

        With no explicit stream the target is resolved at WRITE time, not at
        construction. A module-level ``Console(file=safe_stream())`` built at
        import would otherwise capture the interpreter's original stdout, and
        anything that later swaps ``sys.stdout`` - click's ``CliRunner``,
        ``contextlib.redirect_stdout``, pytest's capture - would be bypassed
        and its buffer would come back empty.
    """

    def __init__(self, stream: TextIO | None) -> None:
        self._stream = stream

    def _target(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stdout

    def write(self, text: str) -> int:
        """Write `text`, degrading anything the current target cannot encode."""
        target = self._target()
        encoding = getattr(target, "encoding", None)
        try:
            return target.write(encode_safe(text, encoding if isinstance(encoding, str) else None))
        except OSError as exc:
            if not is_broken_pipe(exc):
                raise
            _reader_went_away(target)

    def flush(self) -> None:
        """Flush the current target."""
        target = self._target()
        try:
            target.flush()
        except OSError as exc:
            if not is_broken_pipe(exc):
                raise
            _reader_went_away(target)

    def isatty(self) -> bool:
        """Report the target's tty-ness, so rich keeps its styling."""
        return self._target().isatty()

    @property
    def encoding(self) -> str | None:
        """Expose the target's encoding; rich inspects it."""
        encoding = getattr(self._target(), "encoding", None)
        return encoding if isinstance(encoding, str) else None


def safe_stream(stream: TextIO | None = None) -> IO[str]:
    """Wrap a stream so unencodable text degrades instead of raising.

    Use for a writer handed to a third-party renderer. For this project's own
    output use :func:`echo` instead.

    The return is typed as the ``IO[str]`` rich's ``Console(file=...)`` declares,
    rather than left as ``Any``. ``_SafeWriter`` implements the four members
    rich actually calls - ``write``, ``flush``, ``isatty``, ``encoding`` - and
    nothing else of the ABC, which is why the type has to be asserted here
    rather than inferred; asserting it once at this boundary is what keeps the
    call site typed, where ``Any`` erased the whole console.

    Parameters
    ----------
    stream:
        The destination text stream. Omit it (or pass None) to follow
        ``sys.stdout`` as it is at each write, which is what a module-level
        renderer needs so test harnesses can still capture the output.

    Returns
    -------
    IO[str]
        A writer with ``write``/``flush``/``isatty``/``encoding``.
    """
    return cast("IO[str]", _SafeWriter(stream))


__all__ = [
    "ASCII_FALLBACKS",
    "ascii_fallback",
    "echo",
    "encode_safe",
    "flush_streams_or_leave",
    "is_broken_pipe",
    "restore_original_streams",
    "safe_stream",
    "write_unless_the_reader_left",
]
