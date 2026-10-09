"""The commands and the sampling policy for counter history.

Contents:
    * :func:`analyse` - read the machine, judge it against its recorded past,
      and record this reading when it has anything new to say
    * :func:`cli_record` - take a sample and print nothing, for a timer
    * :func:`cli_trend` - what every watched counter is doing over time

A drive holds the running total of its own errors and has never held the past,
so the tool has to keep that itself or it can only ever report how much damage
there has ever been, never whether it is still happening.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

import lib_log_rich.runtime
import rich_click as click

from lsdsk.adapters.history.store import (
    HistoryRead,
    history_lock,
    load_history,
    remove_abandoned_temporaries,
    save_history,
)
from lsdsk.adapters.hw.capture import CaptureEnvelope
from lsdsk.adapters.textfile import read_json_bounded
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import ActionCommand, CliCommand, OutputFormat
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import History, has_new_readings, has_recordable_drive, record
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS, FORMAT_OPTION
from ..envelope import ActionResult, emit_action, fail
from ..exit_codes import ExitCode
from ..typed_click import option
from .scan import (
    Analysis,
    TrendEntry,
    console_for_output,
    effective_replay,
    emit_json,
    exit_code_for,
    load_inventory,
    note_the_findings_this_page_left_out,
    resolve_history,
    resolve_tunables,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from lsdsk.adapters.config.history import HistorySettings
    from lsdsk.domain.models import Inventory
    from lsdsk.domain.thresholds import Thresholds

logger = logging.getLogger(__name__)

#: Stores whose refusal has already been reported this run. A command that
#: reads the history twice must not say the same thing to the operator twice.
_ANNOUNCED_REFUSALS: set[Path] = set()


def forget_announced_refusals() -> None:
    """Begin a run with nothing already said about any store.

    The set above is scoped to a RUN, and a process can hold several: `main`
    is the documented embedder entry, so a second `main([...])` in one process
    is a second run and must be told about a store it cannot read. Left
    unreset, only the first one ever was.
    """
    _ANNOUNCED_REFUSALS.clear()


def read_history(inventory: Inventory, settings: HistorySettings) -> HistoryRead:
    """Load this machine's recorded history, or start an empty one.

    A store that cannot be read is reported and then stood down from rather than
    failing the run: the diagnosis of the hardware in front of you does not
    depend on it, and refusing to say anything about a failing drive because a
    state file is malformed would be the wrong trade.

    Standing down from a store is not the same as being free to replace it. The
    file holds the only copy of a record that cannot be rebuilt from the
    hardware, so a refusal to read it also withdraws permission to write it;
    otherwise the advice in the warning is already impossible to follow by the
    time anybody reads it.

    Args:
        inventory: The machine as this run read it.
        settings: How counter history behaves for this run.

    Returns:
        What has been recorded, and whether the file may be replaced.
    """
    try:
        loaded = load_history(settings.path, hostname=inventory.hostname, cap=settings.max_samples_per_drive)
        return HistoryRead(loaded, writable=True, store=settings.path)
    except ConfigurationError as error:
        # Said once per run. `health` reads the store twice, once through
        # `analyse` and once for the table, and printed the whole refusal twice.
        if settings.path not in _ANNOUNCED_REFUSALS:
            _ANNOUNCED_REFUSALS.add(settings.path)
            safe_console.echo(f"Warning: ignoring counter history: {error}", err=True)
            safe_console.echo(
                f"Not recording this run, so {settings.path} is left as it is. "
                "Move it aside or point --history-file elsewhere to start a new record.",
                err=True,
            )
        return HistoryRead(
            History(hostname=inventory.hostname), writable=False, refusal=str(error), store=settings.path
        )


class RecordOutcome(StrEnum):
    """What a run did about the counter store, and why when it did nothing.

    One member per reason, because ``record --format json`` reports them apart and
    a caller acts differently on each: a run with nothing new is healthy, while a
    store that may not be replaced and a write that failed both mean the record
    has stopped growing. Collapsed into one boolean they were indistinguishable -
    measured 2026-09-20, all four emitted the same sentence byte for byte.

    The two failure members mirror ``snapshot``, which splits a refused write the
    same way, because the errnos a filesystem produces overlap the codes this tool
    means something by.
    """

    RECORDED = "recorded"
    NOTHING_NEW = "nothing new"
    NO_DRIVE_READABLE = "no drive readable"
    STORE_NOT_READABLE = "store not readable"
    RECORDING_OFF = "recording off"
    NOT_PERMITTED = "not permitted"
    COULD_NOT_WRITE = "could not write"


class RecordAttempt(NamedTuple):
    """What came of adding this reading to the store.

    Attributes:
        outcome: What happened.
        detail: The reader's or the filesystem's own words, for the outcomes that
            have any. ``None`` otherwise.
        history: The store as it was written, when a sample was stored, so a
            caller that draws from it need not read the file back. ``None``
            otherwise.
    """

    outcome: RecordOutcome
    detail: str | None = None
    history: History | None = None

    @property
    def stored(self) -> bool:
        """Whether a sample reached the store."""
        return self.outcome is RecordOutcome.RECORDED


#: The outcomes that mean the store could not be written, as opposed to should not be.
_WRITE_FAILED: Final[frozenset[RecordOutcome]] = frozenset({RecordOutcome.NOT_PERMITTED, RecordOutcome.COULD_NOT_WRITE})


#: The sentence for each outcome, as a template over ``{path}`` (the store) and
#: ``{detail}`` (the reader's or the filesystem's own words). One entry per member,
#: which ``test_every_record_outcome_has_its_own_sentence`` holds: a table cannot be
#: checked for exhaustiveness the way a ``match`` can.
_SENTENCES: Final[dict[RecordOutcome, str | None]] = {
    RecordOutcome.RECORDED: None,
    RecordOutcome.NOTHING_NEW: "no drive has advanced its power-on hours since the last reading",
    RecordOutcome.NO_DRIVE_READABLE: "no drive's power-on hours could be read, so there was nothing to store{detail}",
    RecordOutcome.RECORDING_OFF: "--no-record was given, so this run judged the counters without adding to them",
    RecordOutcome.STORE_NOT_READABLE: "left the existing store at {path} alone, because it could not be read: {detail}",
    RecordOutcome.NOT_PERMITTED: "not allowed to write counter history to {path}: {detail}",
    RecordOutcome.COULD_NOT_WRITE: "could not write counter history to {path}: {detail}",
}


def why_nothing_was_stored(attempt: RecordAttempt, path: Path) -> str | None:
    """The one sentence ``record`` reports for `attempt`.

    Args:
        attempt: What came of the write.
        path: The store, so every sentence that is about a file names it.

    Returns:
        The sentence, or ``None`` when a sample was stored and there is nothing
        to report.

    Example:
        >>> from pathlib import Path
        >>> why_nothing_was_stored(RecordAttempt(RecordOutcome.RECORDED), Path("h.json")) is None
        True
        >>> why_nothing_was_stored(RecordAttempt(RecordOutcome.RECORDING_OFF), Path("h.json"))
        '--no-record was given, so this run judged the counters without adding to them'
    """
    template = _SENTENCES[attempt.outcome]
    # Formatting substitutes the values without re-reading them as templates, so
    # a brace in a filesystem's message stays a brace.
    return None if template is None else template.format(path=path, detail=attempt.detail or "")


def record_exit_code(attempt: RecordAttempt) -> ExitCode:
    """The code ``record`` leaves for `attempt`.

    Non-zero exactly when the record has stopped growing: a write that failed, and
    a store that could not be read and so may not be replaced. Nothing new to add
    is the store left alone on purpose, which is what the command is for. It has
    to be non-zero there, because ``record`` prints nothing at all in human mode
    and the code is then the only channel a timer has - measured before this, a
    store that could not be written, and one that could not be read, both left 0
    and said nothing on stdout.

    The write split follows ``snapshot`` rather than the errno, which would
    collide: EACCES is 13 and so is this tool's own permission code, while ENOSPC
    is 28, which means nothing here. An unreadable store is 78, the code this tool
    already gives a file it cannot use, and not 74: nothing was written, so an
    I/O code would send somebody looking at the wrong half of the operation.

    A run that could read no drive's power-on hours is 1: the record is not
    growing, but nothing about the store is wrong, so none of the store codes
    fits. ``record`` reports no findings, so 1 cannot be read as one here.

    Args:
        attempt: What came of the write.

    Returns:
        The exit code.

    Example:
        >>> record_exit_code(RecordAttempt(RecordOutcome.NOTHING_NEW))
        <ExitCode.SUCCESS: 0>
        >>> record_exit_code(RecordAttempt(RecordOutcome.STORE_NOT_READABLE, "malformed"))
        <ExitCode.CONFIG_ERROR: 78>
        >>> record_exit_code(RecordAttempt(RecordOutcome.NOT_PERMITTED, "denied"))
        <ExitCode.PERMISSION_DENIED: 13>
        >>> record_exit_code(RecordAttempt(RecordOutcome.COULD_NOT_WRITE, "full"))
        <ExitCode.IO_ERROR: 74>
        >>> record_exit_code(RecordAttempt(RecordOutcome.NO_DRIVE_READABLE, ""))
        <ExitCode.GENERAL_ERROR: 1>
    """
    match attempt.outcome:
        case RecordOutcome.STORE_NOT_READABLE:
            return ExitCode.CONFIG_ERROR
        case RecordOutcome.NOT_PERMITTED:
            return ExitCode.PERMISSION_DENIED
        case RecordOutcome.COULD_NOT_WRITE:
            return ExitCode.IO_ERROR
        case RecordOutcome.NO_DRIVE_READABLE:
            return ExitCode.GENERAL_ERROR
        case RecordOutcome.RECORDED | RecordOutcome.NOTHING_NEW | RecordOutcome.RECORDING_OFF:
            return ExitCode.SUCCESS


def warn_if_the_store_was_not_written(attempt: RecordAttempt, path: Path) -> None:
    """Report a failed write for a command that records only incidentally.

    ``report`` and ``health`` record because they happen to have read the
    counters, so a store they cannot write is a warning and never the answer:
    refusing to diagnose the hardware in front of somebody because a state file
    is read-only would be the wrong trade. ``record`` itself wants the opposite,
    which is why the reporting is the caller's rather than
    :func:`record_reading`'s.

    Args:
        attempt: What came of the write.
        path: The store, named here because the detail leaves it out when the
            filesystem's own error named it.
    """
    if attempt.outcome in _WRITE_FAILED:
        safe_console.echo(f"Warning: could not record counter history to {path}: {attempt.detail}", err=True)


def _in_its_own_words(error: OSError, store: Path) -> str:
    """The filesystem's error as a sentence about the store, without naming the store twice.

    ``str(OSError)`` ends with the filename it carries, and every sentence that
    reports this detail already names the store, so a refusal about the store
    itself read ``... to X: [Errno 11] ...: 'X'``. The filename stays when it is
    a DIFFERENT file - the lock, a temporary file, a directory in the way -
    because there it is the part that says where the fault is.

    Args:
        error: What the write raised.
        store: The store the sentence already names.

    Returns:
        The detail to report.

    Example:
        >>> from pathlib import Path
        >>> _in_its_own_words(OSError(11, "held too long", "h.json"), Path("h.json"))
        '[Errno 11] held too long'
        >>> _in_its_own_words(OSError(13, "denied", ".h.json.lock"), Path("h.json"))
        "[Errno 13] denied: '.h.json.lock'"
    """
    if error.filename is None or error.filename2 is not None or Path(error.filename) != store:
        return str(error)
    return f"[Errno {error.errno}] {error.strerror}" if error.errno is not None else str(error.strerror)


def _why_not_to_record(inventory: Inventory, read: HistoryRead, settings: HistorySettings) -> RecordAttempt | None:
    """The reason this reading is not added to the store, or ``None`` to add it."""
    # A store that could not be read is still a store. Writing this run's
    # readings over it replaces an accumulated record with a single sample, and
    # every refusal reason reaches here: a renamed host, a newer schema, a file
    # too large to read, malformed JSON. None of them is a reason to delete it.
    if not read.writable:
        return RecordAttempt(RecordOutcome.STORE_NOT_READABLE, read.refusal)
    if not settings.enabled:
        return RecordAttempt(RecordOutcome.RECORDING_OFF)
    # Asked before "nothing new", which is a claim that every drive was asked and
    # none had moved. With no drive's clock read that claim is false, and a
    # sampler without privilege would report a healthy hour for as long as it ran.
    if not has_recordable_drive(inventory.disks):
        why = "" if inventory.privileged else " (reading SMART needs root or Administrator)"
        return RecordAttempt(RecordOutcome.NO_DRIVE_READABLE, why)
    if not has_new_readings(read.history, inventory.disks):
        return RecordAttempt(RecordOutcome.NOTHING_NEW)
    return None


def record_reading(
    inventory: Inventory,
    read: HistoryRead,
    settings: HistorySettings,
    captured_at: str | None = None,
    *,
    announce: bool = True,
) -> RecordAttempt:
    """Add this reading to the store, when it has anything new to say.

    Args:
        inventory: The machine as this run read it.
        read: What has been recorded so far, and whether it may be replaced.
        settings: How counter history behaves for this run.
        captured_at: When the hardware was read. Defaults to now, which is right
            for a live run and wrong for a snapshot taken last year, so the
            replay path passes the capture's own stamp.
        announce: Name the store the first time a machine records anything.

    Returns:
        What came of it, as the outcome plus whatever the refusal said. Reporting
        is the caller's, because the two callers want opposite things from a
        failed write: see :func:`warn_if_the_store_was_not_written`.
    """
    declined = _why_not_to_record(inventory, read, settings)
    if declined is not None:
        return declined
    try:
        with history_lock(settings.path):
            # Decided HERE, holding the lock, rather than before trying for it:
            # two runs starting against an empty store both used to peek at
            # `path.exists()` before either had written anything, so both read
            # "nothing here yet" and both announced once their turn to write
            # came round. The lock serialises every write, so whichever run
            # is actually about to create the file is the only one that can
            # ever see it absent at this point.
            first_ever = not settings.path.exists()
            # Read again under the lock: `read` may be a copy from before
            # another run stored its reading, and folding into it would write
            # that reading away.
            attempt = _record_into_the_current_store(inventory, settings, captured_at)
    except PermissionError as error:
        return RecordAttempt(RecordOutcome.NOT_PERMITTED, _in_its_own_words(error, settings.path))
    except OSError as error:
        return RecordAttempt(RecordOutcome.COULD_NOT_WRITE, _in_its_own_words(error, settings.path))
    if attempt.stored and first_ever and announce:
        # Said once per machine, so a run that writes to disk is never a silent
        # surprise, and never again after that.
        safe_console.echo(f"Recording disk error counters to {settings.path} (--no-record turns this off).", err=True)
    return attempt


def _record_into_the_current_store(
    inventory: Inventory, settings: HistorySettings, captured_at: str | None
) -> RecordAttempt:
    """Fold this reading into the store as it is on disk now, and write it back.

    Raises:
        OSError: If the store cannot be written; the caller names which kind.
    """
    current = read_history(inventory, settings)
    declined = _why_not_to_record(inventory, current, settings)
    if declined is not None:
        return declined
    # Under the lock the caller holds, so no run of ours is mid-write.
    remove_abandoned_temporaries(settings.path)
    stamp = captured_at or datetime.now(UTC).isoformat()
    updated = record(current.history, inventory.disks, stamp, cap=settings.max_samples_per_drive)
    save_history(updated, settings.path)
    return RecordAttempt(RecordOutcome.RECORDED, history=updated)


def analyse(
    replay: Path | None,
    output_format: OutputFormat,
    settings: HistorySettings,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    read_machine: Callable[[], Inventory] | None = None,
) -> Analysis:
    """Read the machine, judge it against its past, and record this reading.

    Recording is skipped for a replay, because the samples belong to whichever
    machine produced the snapshot rather than to this one, and for JSON output,
    because a command in a pipeline should not mutate state on the side.

    Args:
        replay: A snapshot to render instead of reading this machine.
        output_format: What the caller asked for.
        settings: How counter history behaves for this run.
        thresholds: The judgement values the rules weigh against.
        read_machine: How a live run reads the hardware, or ``None`` for the
            platform reader. The seam a test drives a live run through.

    Returns:
        The machine, its findings, and the counter store as it stands after
        this run: the copy judged against, or the one this run wrote. A caller
        that draws the counters draws from it rather than reading the file again.
        A live JSON run writes nothing, but the store it returns already holds
        this run's reading, so the envelope's trend is judged against the same
        history the table is.
    """
    if replay is None and read_machine is not None:
        inventory = read_machine()
    else:
        inventory = load_inventory(replay, output_format=output_format)
    read = read_history(inventory, settings)
    findings = diagnose(inventory, history=read.history, thresholds=thresholds)
    if replay is None and output_format is OutputFormat.HUMAN:
        attempt = record_reading(inventory, read, settings)
        warn_if_the_store_was_not_written(attempt, settings.path)
        if attempt.history is not None:
            read = HistoryRead(attempt.history, writable=True, store=settings.path)
    elif replay is None and _why_not_to_record(inventory, read, settings) is None:
        stamp = datetime.now(UTC).isoformat()
        folded = record(read.history, inventory.disks, stamp, cap=settings.max_samples_per_drive)
        read = HistoryRead(folded, writable=True, store=settings.path)
    return Analysis(inventory, findings, read)


def _capture_stamp(replay: Path | None) -> str | None:
    """When a replayed capture was taken, or ``None`` for a live run.

    Read through the same Pydantic envelope that validates a snapshot rather
    than by reaching into the raw mapping, so the field is typed at exactly one
    place. A capture that cannot be read at all is not an error here: the stamp
    is for display, and the reading itself has already succeeded by this point.

    Parsed through `read_json_bounded` rather than by handing the text to
    Pydantic, because Pydantic's own parser resolves a repeated key
    last-writer-wins exactly as `json.loads` does, and this is the only other
    place a file from outside this tool is turned into JSON. The caller reads
    the same file through the guarded path first, so nothing unguarded reaches
    here today - but that is an ordering, and an ordering is one edit from not
    holding.
    """
    if replay is None:
        return None
    try:
        return CaptureEnvelope.model_validate(read_json_bounded(replay, what="a snapshot")).captured_at
    # ValueError rather than ValidationError, which is a subclass of it: a
    # repeated key is refused as the plain error json.loads raises, and the
    # stamp is for display either way.
    except (ConfigurationError, ValueError):
        return None


class RecordResult(ActionResult):
    """What one `record` run stored, and where.

    `recorded` false is not on its own a failure: `outcome` says which of the
    reasons it was. ``nothing new`` means no drive's own clock has moved since the
    last reading, which is a healthy run, so it carries `ok` true and nothing in
    `skipped`; the outcomes that mean the record has stopped growing - including
    ``no drive readable``, a run that could not read any drive's clock - carry
    their sentence in `skipped` and a non-zero exit code.
    """

    recorded: bool
    outcome: RecordOutcome
    store: str
    drives: int


@click.command("record", context_settings=CLICK_CONTEXT_SETTINGS)
@FORMAT_OPTION
@option(
    "--replay",
    "replay",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Fold a snapshot captured earlier into the history instead of reading this machine.",
)
@click.pass_context
def cli_record(ctx: click.Context, replay: Path | None, output_format: OutputFormat) -> None:
    """Record this machine's error counters, printing nothing unless asked.

    Meant for a timer, so the human form is silent. `--format json` emits the
    usual envelope, which is how a scheduled job tells a run that stored a
    reading from one that had nothing new to store. Every other command
    records on its own when it has something new, so this exists for
    unattended sampling on a schedule rather than as a step to remember.
    """
    settings = resolve_history(ctx)
    if not settings.enabled:
        # A contradiction rather than a setting: on every other command
        # --no-record means "judge against the store without adding to it",
        # which this command has nothing left to do. Obeyed quietly it was the
        # worst of both - exit 0, nothing stored, and in the human form, which
        # is the one a timer runs, not a word on either stream - so a sampler
        # inheriting the flag from a wrapper never recorded and never said so.
        # Refused the way snapshot refuses a global --replay, for the same
        # reason: guessing which of two incompatible things a caller meant is
        # how a scheduled job runs wrong for a year.
        fail(
            "record exists to add this reading to the counter store, so --no-record leaves it nothing to do.",
            ExitCode.INVALID_ARGUMENT,
            output_format=output_format,
            hint="Drop --no-record to sample, or run `lsdsk trend` to judge the counters without adding to them.",
        )
    # Not a CliCommand member: CliCommand names the report pages, and `record`
    # is an acting command. It does emit an envelope, through emit_action.
    with lib_log_rich.runtime.bind(
        job_id="cli-record",
        extra={"command": ActionCommand.RECORD.value},
    ):
        # Resolve once: a bare ``replay`` here would honour ``record --replay`` and
        # silently drop the root group's ``--replay``, sampling this machine into
        # the store under its own hostname while the caller asked for another's.
        target = effective_replay(ctx, replay)
        inventory = load_inventory(target, output_format=output_format)
        read = read_history(inventory, settings)
        attempt = record_reading(inventory, read, settings, captured_at=_capture_stamp(target), announce=False)
        # One sentence per reason rather than one for all of them. A run that
        # stored nothing because no drive's clock has advanced is healthy; a store
        # it may not replace, and a write that failed, both mean the record has
        # stopped growing, and a scheduled job has to be able to tell them apart.
        reason = why_nothing_was_stored(attempt, settings.path)
        code = record_exit_code(attempt)
        if code is not ExitCode.SUCCESS:
            # Said on stderr as well, because the human form of this command is
            # silent by design and would otherwise report a failed write with
            # nothing but an exit code.
            safe_console.echo(f"Error: {reason}", err=True)
        if output_format is OutputFormat.JSON:
            # Only a run whose record stopped growing skips anything. Nothing new
            # is the store left alone on purpose, so it reports ok and says what
            # it did in `outcome`: a timer that reads `ok` first, as the skill
            # teaches, must not alarm on a healthy hour.
            emit_action(
                ActionCommand.RECORD,
                RecordResult(
                    recorded=attempt.stored,
                    outcome=attempt.outcome,
                    store=str(settings.path),
                    drives=len(inventory.disks),
                ),
                skipped=[] if code is ExitCode.SUCCESS or reason is None else [reason],
            )
        raise SystemExit(code)


@click.command("trend", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--replay",
    "replay",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Render a snapshot captured earlier instead of reading this machine.",
)
@FORMAT_OPTION
@click.pass_context
def cli_trend(ctx: click.Context, replay: Path | None, output_format: OutputFormat) -> None:
    """Show what each error counter is doing over time, not just its total.

    A counter is a lifetime total the drive keeps in its own non-volatile
    table, so it survives reboots and says nothing about when the damage
    happened. This says whether it is still happening.
    """
    with lib_log_rich.runtime.bind(job_id="cli-trend", extra={"command": CliCommand.TREND.value}):
        settings = resolve_history(ctx)
        thresholds, display = resolve_tunables(ctx)
        target = effective_replay(ctx, replay)
        inventory, findings, read = analyse(target, output_format, settings, thresholds)
        if output_format is OutputFormat.JSON:
            from lsdsk.adapters.render.trend import trend_rows  # noqa: PLC0415 - keeps the import graph flat

            rows = trend_rows(inventory, read.history, display.wear_row_floor_percent)
            trend = [TrendEntry(device=row.disk.path, counter=row.kind, trend=row.trend) for row in rows]
            emit_json(inventory, findings, CliCommand.TREND, history=read, trend=trend)
        else:
            from lsdsk.adapters.render.trend import render_trend  # noqa: PLC0415 - keeps the import graph flat

            console = console_for_output(display.piped_width)
            console.print(
                render_trend(
                    inventory,
                    read.history,
                    width=console.width,
                    wear_floor=display.wear_row_floor_percent,
                    store_refusal=read.refusal,
                )
            )
            # No subjects: the trend section draws counters, never a finding, so
            # everything the machine has to report is somewhere else.
            note_the_findings_this_page_left_out(findings, (), console)
        raise SystemExit(exit_code_for(findings))


__all__ = [
    "HistoryRead",
    "RecordAttempt",
    "RecordOutcome",
    "RecordResult",
    "analyse",
    "cli_record",
    "cli_trend",
    "forget_announced_refusals",
    "read_history",
    "record_exit_code",
    "record_reading",
    "warn_if_the_store_was_not_written",
    "why_nothing_was_stored",
]
