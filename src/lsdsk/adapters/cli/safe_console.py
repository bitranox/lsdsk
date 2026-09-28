r"""Encode-safe console output.

Purpose
-------
Wraps :func:`click.echo` so a console whose codepage cannot represent a glyph
degrades that glyph instead of aborting the command.

Why
---
Console output is a sink with an encoding the program does not choose. Python
hands stdout to a Windows console at codepage 1252 with ``errors="strict"``, so
writing ``✓`` raises ``UnicodeEncodeError: 'charmap' codec can't encode
character '\u2713'`` and the command exits non-zero -- after its real work has
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
* :func:`safe_stream_to_both` - the same, for a renderer writing to both streams
* :func:`is_broken_pipe` - whether a failed write means the reader left
* :class:`UnwritableStandardOutputError` - stdout refused a write for another reason
* :func:`say_standard_output_failed` - the one sentence that reports it
* :func:`stand_in_for_missing_standard_streams` and
  :func:`standard_output_is_missing` - a process started with no stdout or no
  stderr at all
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
import io
import os
import sys
import threading
from enum import Enum
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

#: What a stdout write met on a thread other than the main one, oldest first.
#:
#: lib_log_rich writes on a queue worker thread, and an exception raised there
#: ends that thread and nothing else: ``SystemExit(141)`` never reached the exit
#: code, and the worker it killed left every later log line undelivered and the
#: shutdown drain waiting out its whole stop timeout. So a failure met off the
#: main thread is recorded here instead, and :func:`flush_streams_or_leave`
#: answers it on the main thread, after the logging shutdown has drained.
_FAILED_OFF_THE_MAIN_THREAD: Final[list[tuple[_Delivery, OSError]]] = []


def _stop_writing_to(stream: IO[Any] | io.IOBase | None) -> None:
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
    # Two acquisitions, so two scopes: taken together, a failure on the second
    # returned with the first neither closed nor recorded, which means nothing
    # closes it later either. The realistic way the second fails is descriptor
    # exhaustion, which is exactly when leaking one more is worst, and this runs
    # on the broken-pipe error path.
    try:
        saved = os.dup(descriptor)
    except OSError:  # pragma: no cover - dup of a live descriptor
        return
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:  # pragma: no cover - the null device is always openable
        os.close(saved)
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
        Restores process file descriptors and closes the saved duplicates, puts
        back each None :func:`stand_in_for_missing_standard_streams` replaced,
        and forgets any stdout failure recorded off the main thread, so the next
        run in the same process starts from nothing.
    """
    if isinstance(sys.stdout, _MissingStandardOutput):
        sys.stdout = None
    if isinstance(sys.stderr, _MissingStandardError):
        sys.stderr = None
    _FAILED_OFF_THE_MAIN_THREAD.clear()
    while _REDIRECTED_DESCRIPTORS:
        descriptor, saved = _REDIRECTED_DESCRIPTORS.popitem()
        try:
            os.dup2(saved, descriptor)
        except OSError:  # pragma: no cover - the saved descriptor is this process's own
            pass
        finally:
            with contextlib.suppress(OSError):
                os.close(saved)


def _lose_the_diagnostic() -> None:
    """Give up on stderr after a write to it failed, for ANY reason.

    stderr carries diagnostics about the run and never the run's own output, so a
    stderr that cannot be written costs the diagnostic and nothing else: the
    report on stdout and the exit code the run decided both stand. That holds
    whether its reader left (``BrokenPipeError``), the disk behind it is full
    (``ENOSPC``, ``2>/dev/full``) or the device failed (``EIO``) - the three are
    one event from the point of view of whoever asked for the report. Measured
    before a full disk was answered this way: ``lsdsk --profile nosuch findings
    --format json 2>/dev/full`` left 120 with 0 bytes on stdout, against 1 and the
    whole envelope with stderr to a file, because the warning's ``ENOSPC`` was
    re-raised, the crash report about it failed the same way, and the interpreter
    retried the buffered bytes at shutdown, outside every handler.

    Pointing the descriptor at the null device is what empties the buffer whose
    retry would fail at shutdown.

    Side Effects:
        Points stderr's descriptor at the null device.
    """
    _stop_writing_to(sys.stderr)


def write_unless_the_reader_left(emit: Callable[[], None], *, err: bool = True) -> None:
    """Run `emit`, and give up quietly if the reader of its stream has gone.

    For a DIAGNOSTIC write on the way out of a run that has ALREADY decided its
    exit code - click's usage message, a traceback, the failure envelope. A broken
    pipe there must not become the answer, and must not escape: the run's own code
    is what the caller asked about, and it is a refusal, which
    :func:`~.exit_codes.outranks_a_departed_reader` says stands.

    The same event arrives in two shapes, so both are caught. A write straight to
    the stream raises ``OSError``; a write through :func:`echo` raises
    ``SystemExit(BROKEN_PIPE)``, because for a command's OWN output a departed
    stdout reader does end the run. Catching only the first left one identical
    refusal answering 78 in human mode and 141 in JSON mode, since only the JSON
    mode writes its sentence to stdout - and an exit code that depends on the
    output format is the collapse the codes exist to prevent.

    Measured before this existed: ``lsdsk nosuchcommand 2>&1 | head`` left 120.
    click raised ``UsageError``, the ``BrokenPipeError`` from printing it was
    raised INSIDE the handler that was printing it, so it escaped ``_run_cli``
    and then ``main`` itself, and the interpreter reported its own shutdown
    flush failure instead of the usage error the caller needed.

    A diagnostic bound for STDOUT that stdout refuses for another reason - the
    failure envelope meeting a full disk - is lost the same way, and said so on
    stderr: the code was decided before the write, so it still stands.

    A diagnostic bound for STDERR is lost whatever the reason, because stderr
    never carries the run's own output (see :func:`_lose_the_diagnostic`).

    Args:
        emit: The write to attempt. It is expected to raise nothing but an
            ``OSError`` for a stream that has gone.
        err: Whether `emit` writes to stderr, which is where diagnostics go.

    Raises:
        SystemExit: Re-raised unchanged when `emit` leaves with any code other
            than :attr:`~.exit_codes.ExitCode.BROKEN_PIPE`. Only that code is
            the departed-reader shape of the event this function absorbs; any
            other is an exit something inside `emit` decided on, and swallowing
            it would replace that decision with the run's earlier one.

    Side Effects:
        Points the written stream at the null device when its reader has left,
        so the interpreter's own exit flush cannot fail afterwards.
    """
    try:
        emit()
    except OSError as exc:
        if err:
            _lose_the_diagnostic()
            return
        if is_broken_pipe(exc):
            _stop_writing_to(sys.stdout)
            return
        refused = (
            exc if isinstance(exc, UnwritableStandardOutputError) else _refused_by_standard_output(sys.stdout, exc)
        )
        say_standard_output_failed(refused)
    except SystemExit as leaving:
        if leaving.code != int(ExitCode.BROKEN_PIPE):
            raise
        # Nothing to silence here: whatever raised this has already pointed the
        # stream at the null device, which is what `_reader_went_away` does first.


def _reader_went_away(stream: IO[Any] | io.IOBase | None) -> NoReturn:
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


def _answer_a_failed_standard_output_write(target: IO[Any] | io.IOBase | None, exc: OSError) -> None:
    """Answer a stdout write that failed, on whichever thread met it.

    On the MAIN thread the answer is raised where it happened: ``SystemExit(141)``
    for a departed reader, :class:`UnwritableStandardOutputError` for anything
    else, as before. OFF it the answer is recorded, because a raise there ends
    one thread and nothing else - measured before this, ``LOG_CONSOLE_STREAM=stdout
    lsdsk fail`` with stdout's reader gone took 5.6 s against 0.6 s, the logging
    worker having died on a ``SystemExit`` nobody saw while the shutdown waited
    out the library's 5 s stop timeout for it. stdout is silenced either way, so
    nothing retries the bytes; :func:`flush_streams_or_leave` reads the record.

    Args:
        target: The stream whose write failed, which is ``sys.stdout``.
        exc: What the write raised.

    Raises:
        SystemExit: On the main thread, when stdout's reader has gone.
        UnwritableStandardOutputError: On the main thread, when stdout refused
            the write for another reason.
    """
    broken = is_broken_pipe(exc)
    if threading.current_thread() is not threading.main_thread():
        _stop_writing_to(target)
        _FAILED_OFF_THE_MAIN_THREAD.append((_Delivery.READER_LEFT if broken else _Delivery.NOT_WRITTEN, exc))
        return
    if broken:
        _reader_went_away(target)
    raise _refused_by_standard_output(target, exc) from exc


def _answer_what_failed_off_the_main_thread(already: set[_Delivery]) -> set[_Delivery]:
    """Hand the final flush what stdout met off the main thread, and say a refusal once.

    Args:
        already: What flushing the streams themselves came to, so a refusal
            that flush already reported is not reported twice.

    Returns:
        How each recorded failure ended, as the flush would have put it.

    Side Effects:
        Empties the record, and writes one sentence to stderr for the first
        refusal when the flush did not already write it.
    """
    recorded = list(_FAILED_OFF_THE_MAIN_THREAD)
    _FAILED_OFF_THE_MAIN_THREAD.clear()
    refusals = [exc for outcome, exc in recorded if outcome is _Delivery.NOT_WRITTEN]
    if refusals and _Delivery.NOT_WRITTEN not in already:
        say_standard_output_failed(UnwritableStandardOutputError(refusals[0].errno, refusals[0].strerror))
    return {outcome for outcome, _ in recorded}


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


class UnwritableStandardOutputError(OSError):
    """Standard output refused a write for a reason other than its reader leaving.

    A full disk is the common case (``lsdsk ... > /dev/full`` fails with
    ``ENOSPC``). It is raised in place of the ``OSError`` the stream gave, with
    that error's errno and text, AFTER stdout has been pointed at the null
    device: the failed bytes stay in Python's buffer, and without the redirect
    the interpreter retried them at shutdown, outside every handler, printing a
    traceback and leaving 120.

    It stays an ``OSError`` so a command that knows what it was writing - the
    capture ``snapshot -o -`` produces - can still catch it and say so in its own
    words. Anything that does not reaches the last-resort handler, which reads
    this type rather than the errno: the errno is the filesystem's, and 28 means
    nothing in this tool's exit-code table.
    """


def _refused_by_standard_output(stream: IO[Any] | io.IOBase | None, exc: OSError) -> UnwritableStandardOutputError:
    """Silence stdout after a failed write, and name the failure for the caller to raise.

    Args:
        stream: The stream whose write failed, which is ``sys.stdout``.
        exc: What the write raised.

    Returns:
        The typed error carrying the same errno and text.
    """
    _stop_writing_to(stream)
    return UnwritableStandardOutputError(exc.errno, exc.strerror)


class _MissingStandardOutput(io.TextIOBase):
    """Stands in for the standard output a process was started without.

    With descriptor 1 closed - ``>&-``, pythonw, a detached Windows launch - the
    interpreter sets ``sys.stdout`` to None, and every printer met that
    differently: ``click.echo`` returned silently, so output went nowhere and the
    run exited as if it had arrived, while rich asked None whether it was a
    terminal and died with an ``AttributeError`` that left 70. This object gives
    them all one answer: a write is refused the way a full disk refuses it, as
    :class:`UnwritableStandardOutputError`, which the last-resort handler reports
    in one sentence and leaves 74 for.
    """

    #: UTF-8, so an encoding probe has an answer and moves on to the write.
    encoding = "utf-8"

    def writable(self) -> bool:
        """Claim to be writable, so a printer reaches the write that refuses."""
        return True

    def write(self, text: str) -> int:
        """Refuse, as a missing stream must.

        Raises:
            UnwritableStandardOutputError: Always.
        """
        raise UnwritableStandardOutputError(errno.EBADF, "standard output is closed")

    def flush(self) -> None:
        """Nothing is ever buffered here, so there is nothing to deliver."""

    def isatty(self) -> bool:
        """No terminal: nobody is sitting at a stream that does not exist."""
        return False


class _MissingStandardError(io.TextIOBase):
    """Stands in for the standard error a process was started without.

    With descriptor 2 closed - ``2>&-``, a supervisor that wires only stdout -
    the interpreter sets ``sys.stderr`` to None, and three printers fell back to
    STDOUT on meeting it: the logging console, rich-click's usage error and the
    log line of a crash. So ``lsdsk config --format json 2>&-`` left 0 with a log
    line appended to the JSON a caller was parsing.

    It ACCEPTS every write and keeps none, where the stdout stand-in refuses:
    stderr carries diagnostics about the run and never the run's own output, so
    a stderr nobody holds loses the diagnostic and leaves the verdict alone -
    the rule a departed stderr reader is already answered by.
    """

    #: UTF-8, so an encoding probe has an answer and moves on to the write.
    encoding = "utf-8"

    def writable(self) -> bool:
        """Claim to be writable, so a printer writes here rather than elsewhere."""
        return True

    def write(self, text: str) -> int:
        """Accept `text` and keep none of it.

        Returns:
            The whole length, so no caller retries what has nowhere to go.
        """
        return len(text)

    def flush(self) -> None:
        """Nothing is ever kept here, so there is nothing to deliver."""

    def isatty(self) -> bool:
        """No terminal: nobody is sitting at a stream that does not exist."""
        return False


def stand_in_for_missing_standard_streams() -> None:
    """Put a stand-in where a missing ``sys.stdout`` or ``sys.stderr`` would be.

    Called once by ``main`` before any command runs, and undone by
    :func:`restore_original_streams`, so every printer - click's, rich's, this
    project's own - finds a stream rather than None and none of them picks a
    fallback of its own. A missing stdout refuses a write, which leaves 74; a
    missing stderr swallows one. A stream the process has is untouched.

    Side Effects:
        Rebinds ``sys.stdout`` and ``sys.stderr`` where either is None.
    """
    if sys.stdout is None:
        sys.stdout = _MissingStandardOutput()
    if sys.stderr is None:
        sys.stderr = _MissingStandardError()


def standard_output_is_missing() -> bool:
    """Whether this process was started with no standard output at all.

    Returns:
        True when ``sys.stdout`` is None or the stand-in for it.

    Example:
        >>> standard_output_is_missing()
        False
    """
    return sys.stdout is None or isinstance(sys.stdout, _MissingStandardOutput)


def say_standard_output_failed(exc: OSError) -> None:
    """Tell the person on stderr that stdout could not be written, and why.

    One sentence and no traceback: the tool did not break, the destination
    refused. It goes to stderr, which is the only stream left that can carry it,
    and through the guard that lets a departed stderr reader cost this sentence
    and nothing else.

    Args:
        exc: The error the write raised.

    Side Effects:
        Writes one line to stderr.
    """
    write_unless_the_reader_left(lambda: echo(f"Error: could not write to standard output: {exc}", err=True))


class _Delivery(Enum):
    """What flushing one stream came to."""

    DELIVERED = "delivered"
    READER_LEFT = "reader left"
    NOT_WRITTEN = "not written"
    DIAGNOSTIC_LOST = "diagnostic lost"


def _delivered(stream: IO[Any] | None) -> _Delivery:
    """Flush `stream`, reporting whether what it held reached anybody.

    Args:
        stream: The stream to deliver.

    Returns:
        How the flush ended. A stream that cannot be flushed at all counts as
        delivered: there is no pipe behind it to break. A stderr refusing for any
        reason other than its reader leaving is a lost diagnostic, which leaves
        the verdict alone (see :func:`_lose_the_diagnostic`); a stdout refusing
        so is answered here and said on stderr, because this runs after the
        command has finished and nothing downstream could.
    """
    try:
        if stream is not None:
            stream.flush()
    except ValueError:  # pragma: no cover - a closed test harness buffer
        return _Delivery.DELIVERED
    except OSError as exc:
        if is_broken_pipe(exc):
            _stop_writing_to(stream)
            return _Delivery.READER_LEFT
        if stream is not sys.stdout:
            _lose_the_diagnostic()
            return _Delivery.DIAGNOSTIC_LOST
        say_standard_output_failed(_refused_by_standard_output(stream, exc))
        return _Delivery.NOT_WRITTEN
    return _Delivery.DELIVERED


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

    Stdout that refused the flush for another reason - a full disk - yields the
    same way, to ``IO_ERROR``: that is the documented code for a ``snapshot``
    whose write failed other than for permission, and the output that was not
    written is no more a verdict than output nobody read. Measured before this:
    ``lsdsk --version > /dev/full`` printed a traceback and left 120, because the
    error escaped here and the interpreter then retried the same bytes.

    A stdout failure the logging worker met off the main thread is answered here
    too, from the record :func:`_answer_a_failed_standard_output_write` kept: it
    could not be raised where it happened.

    Args:
        code: What the run decided to leave with.

    Returns:
        That same code when every flush succeeds, so an ordinary run is untouched.

    Side Effects:
        Flushes stdout and stderr, and points either at the null device if its reader
        has gone or stdout refused the write, so the interpreter's own flush cannot
        fail afterwards and override this answer.
    """
    outcomes = {_delivered(sys.stdout), _delivered(sys.stderr)}
    outcomes |= _answer_what_failed_off_the_main_thread(outcomes)
    if outcomes <= {_Delivery.DELIVERED, _Delivery.DIAGNOSTIC_LOST} or outranks_a_departed_reader(code):
        return code
    if _Delivery.NOT_WRITTEN in outcomes:
        return int(ExitCode.IO_ERROR)
    return int(ExitCode.BROKEN_PIPE)


def ascii_fallback(text: str, encoding: str) -> str:
    """Rewrite `text` so it survives `encoding`.

    Known glyphs become their ASCII equivalent from :data:`ASCII_FALLBACKS`;
    anything else the codec still cannot represent becomes ``?``. Text the
    encoding already accepts is returned unchanged.

    Args:
        text: The message as the caller wrote it.
        encoding: The target stream's encoding, e.g. ``"cp1252"``.

    Returns:
        A string that :meth:`str.encode` accepts for `encoding`.
    """
    mapped = "".join(ASCII_FALLBACKS.get(character, character) for character in text)
    return mapped.encode(encoding, errors="replace").decode(encoding)


def encode_safe(text: str, encoding: str | None) -> str:
    """Return `text` if `encoding` accepts it, else its ASCII fallback.

    The check runs BEFORE the write on purpose. Writing first and catching
    ``UnicodeEncodeError`` would leave the already-encoded prefix on the stream,
    so the retry would duplicate it.

    Args:
        text: The message as the caller wrote it.
        encoding: The target stream's encoding, or ``None`` when it has none.

    Returns:
        The text unchanged, or its ASCII fallback.
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

    Args:
        message: The text to write. Non-string values are stringified as click does.
        file: Target stream. Defaults to click's stdout (or stderr when `err`).
        err: Write to stderr instead of stdout.
        nl: Append a newline.

    Raises:
        UnwritableStandardOutputError: When stdout refused the write for a reason
            other than its reader leaving; stdout is already silenced by then.

    Side Effects:
        Writes to the given stream. A write stderr refuses, for any reason, is
        lost rather than raised.
    """
    text = message if isinstance(message, str) else str(message)
    target = file if file is not None else (sys.stderr if err else sys.stdout)
    try:
        click.echo(encode_safe(text, _stream_encoding(file, err=err)), file=file, err=err, nl=nl)
    except OSError as exc:
        if target is sys.stderr:
            # A stderr that cannot be written is not a reason to stop, whether
            # its reader left or its disk is full. stderr carries diagnostics
            # about the run, never the run's own output, so this one message is
            # lost and the command's own verdict still stands - which is the
            # same ranking :func:`~.exit_codes.outranks_a_departed_reader`
            # applies at the boundary. Aborting here instead answered `lsdsk
            # config-deploy ... 2>&1 | head` with 141 and threw away the 13 that
            # says exactly what went wrong. A departed STDOUT reader does stop
            # the run: that stream IS what was asked for.
            _lose_the_diagnostic()
            return
        if target is sys.stdout:
            _answer_a_failed_standard_output_write(target, exc)
            return
        if not is_broken_pipe(exc):
            raise
        _reader_went_away(target)


class _SafeWriter:
    """A text stream that degrades what the wrapped stream cannot encode.

    Why
        Rich renders through a writer this module does not control, and it
        raises the same ``UnicodeEncodeError`` on a legacy codepage rather than
        substituting. Wrapping the writer applies the fallback to every segment
        rich emits without rich needing to know.

        The target is resolved at WRITE time, not at construction. A
        module-level ``Console(file=safe_stream())`` built at import would
        otherwise capture the interpreter's original stdout, and anything that
        later swaps ``sys.stdout`` - click's ``CliRunner``,
        ``contextlib.redirect_stdout``, pytest's capture - would be bypassed
        and its buffer would come back empty.

        It FOLLOWS a stream by name and never holds one. Holding one is what
        failed: handed ``sys.stderr`` in a process started without it, the
        writer held None, and None was also how it was told to follow stdout, so
        every log line meant for stderr was written into the JSON on stdout.
        A stream missing at write time gets the same stand-in ``main`` installs.
    """

    def __init__(self, *, err: bool) -> None:
        self._err = err

    def _target(self) -> TextIO | io.TextIOBase:
        stream = sys.stderr if self._err else sys.stdout
        if stream is not None:
            return stream
        return _MissingStandardError() if self._err else _MissingStandardOutput()

    def write(self, text: str) -> int:
        """Write `text`, degrading anything the current target cannot encode.

        Returns:
            How many characters were written, which is the whole of `text` when
            the target's reader has gone: there is nowhere left to put it and
            nothing for the caller to retry.
        """
        target = self._target()
        encoding = getattr(target, "encoding", None)
        try:
            return target.write(encode_safe(text, encoding if isinstance(encoding, str) else None))
        except OSError as exc:
            if self._err:
                _lose_the_diagnostic()
                return len(text)
            _answer_a_failed_standard_output_write(target, exc)
            return len(text)

    def flush(self) -> None:
        """Flush the current target, following the same per-stream rule as :func:`echo`."""
        target = self._target()
        try:
            target.flush()
        except OSError as exc:
            if self._err:
                _lose_the_diagnostic()
                return
            _answer_a_failed_standard_output_write(target, exc)

    def isatty(self) -> bool:
        """Report the target's tty-ness, so rich keeps its styling."""
        return self._target().isatty()

    @property
    def encoding(self) -> str | None:
        """Expose the target's encoding; rich inspects it."""
        encoding = getattr(self._target(), "encoding", None)
        return encoding if isinstance(encoding, str) else None


def safe_stream(*, err: bool = False) -> TextIO:
    """Wrap a stream so unencodable text degrades instead of raising.

    Use for a writer handed to a third-party renderer. For this project's own
    output use :func:`echo` instead.

    It also keeps a renderer's own broken-pipe handling out of the way, which is
    why the logging adapter routes through it. rich's ``Console.on_broken_pipe``
    runs ``os.dup2(devnull, sys.stdout.fileno())`` - hardcoded to STDOUT whichever
    stream actually broke - and then raises ``SystemExit(1)``, this tool's code
    for an actionable finding. Writing through this wrapper means rich never sees
    a ``BrokenPipeError`` to handle: the failure is answered here, on the stream
    that really broke.

    The return is typed as ``TextIO``, which is what
    ``lib_cli_exit_tools.print_exception_message(stream=...)`` declares and is
    an ``IO[str]``, which is what rich's ``Console(file=...)`` declares, rather
    than left as ``Any``. ``_SafeWriter`` implements the four members rich
    actually calls - ``write``, ``flush``, ``isatty``, ``encoding`` - and nothing
    else of the ABC, which is why the type has to be asserted here rather than
    inferred; asserting it once at this boundary is what keeps the call site
    typed, where ``Any`` erased the whole console.

    It takes no stream, only which of the two to follow, the way :func:`echo`
    does. A stream handed in is the value it had when the renderer was built,
    and in a process started without stderr that value is None - which a
    writer taking an optional stream cannot tell apart from being given none.

    Args:
        err: Follow ``sys.stderr`` instead of ``sys.stdout``. Either is read as
            it is at each write, which is what a module-level renderer needs so
            test harnesses can still capture the output.

    Returns:
        A writer with ``write``/``flush``/``isatty``/``encoding``.
    """
    return cast("TextIO", _SafeWriter(err=err))


class _SafeTee:
    """Writes every line to stdout and to stderr, each under its own stream's rule.

    Why
        A renderer asked for both streams at once otherwise builds one tee over
        the RAW streams, and rich's ``on_broken_pipe`` fires on it whichever half
        broke - pointing STDOUT at the null device when only stderr's reader
        left. Each half here is a :class:`_SafeWriter`, so a departed stdout
        reader leaves 141 and a departed stderr reader costs stderr alone. On
        the main thread the 141 is raised at the write; on the logging worker,
        where a raise would end only that thread, it is recorded and the final
        flush answers it. Either way the stderr half is written: a stdout
        failure raised from the first half used to skip the second.
    """

    def __init__(self) -> None:
        self._halves = (_SafeWriter(err=False), _SafeWriter(err=True))

    def write(self, text: str) -> int:
        """Write `text` to both halves, stdout first.

        Returns:
            The whole length: each half answers its own failure.
        """
        standard_output, standard_error = self._halves
        try:
            standard_output.write(text)
        finally:
            # The stderr half never raises, so this cannot mask the stdout answer.
            standard_error.write(text)
        return len(text)

    def flush(self) -> None:
        """Flush both halves, stdout first, the stderr half whatever stdout did."""
        standard_output, standard_error = self._halves
        try:
            standard_output.flush()
        finally:
            standard_error.flush()

    def isatty(self) -> bool:
        """Whether either half is a terminal, so rich styles for the one that is."""
        return any(half.isatty() for half in self._halves)

    @property
    def encoding(self) -> str | None:
        """Stdout's encoding, which rich renders for."""
        return self._halves[0].encoding


def safe_stream_to_both() -> IO[str]:
    """The writer :func:`safe_stream` is, for a renderer that writes to both streams.

    Returns:
        A writer with ``write``/``flush``/``isatty``/``encoding`` that follows
        ``sys.stdout`` and ``sys.stderr`` at each write.
    """
    return cast("IO[str]", _SafeTee())


__all__ = [
    "ASCII_FALLBACKS",
    "UnwritableStandardOutputError",
    "ascii_fallback",
    "echo",
    "encode_safe",
    "flush_streams_or_leave",
    "is_broken_pipe",
    "restore_original_streams",
    "safe_stream",
    "safe_stream_to_both",
    "say_standard_output_failed",
    "stand_in_for_missing_standard_streams",
    "standard_output_is_missing",
    "write_unless_the_reader_left",
]
