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

from lsdsk.adapters.history.store import load_history, save_history
from lsdsk.adapters.hw.capture import CaptureEnvelope
from lsdsk.adapters.textfile import read_json_bounded
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import ActionCommand, CliCommand, OutputFormat
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import History, has_new_readings, record
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..envelope import ActionResult, emit_action
from ..exit_codes import ExitCode
from ..typed_click import option
from .scan import (
    Analysis,
    console_for_output,
    effective_replay,
    emit_json,
    exit_code_for,
    load_inventory,
    resolve_history,
    resolve_tunables,
)

if TYPE_CHECKING:
    from lsdsk.adapters.config.history import HistorySettings
    from lsdsk.domain.models import Inventory
    from lsdsk.domain.thresholds import Thresholds

logger = logging.getLogger(__name__)

#: Stores whose refusal has already been reported this run. A command that
#: reads the history twice must not say the same thing to the operator twice.
_ANNOUNCED_REFUSALS: set[Path] = set()


class HistoryRead(NamedTuple):
    """What the store held, and whether this run may write over it.

    The two are separate answers. An unreadable store still yields an empty
    history so the hardware is diagnosed anyway, but it must never be treated as
    "there was nothing here", because that is indistinguishable from an empty
    store right up until the moment it is overwritten.

    Attributes:
        history: What has been recorded, empty when there is nothing usable.
        writable: Whether this run may replace the file. False only when a store
            is present and could not be read.
        refusal: Why it may not be replaced, as the sentence the reader produced.
            ``None`` when nothing refused. Carried rather than left on stderr
            because ``record --format json`` reports this cause apart from the
            others, and a caller parsing stdout cannot see a warning.
    """

    history: History
    writable: bool
    refusal: str | None = None


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
        return HistoryRead(load_history(settings.path, hostname=inventory.hostname), writable=True)
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
        return HistoryRead(History(hostname=inventory.hostname), writable=False, refusal=str(error))


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
    """

    outcome: RecordOutcome
    detail: str | None = None

    @property
    def stored(self) -> bool:
        """Whether a sample reached the store."""
        return self.outcome is RecordOutcome.RECORDED


#: The outcomes that mean the store could not be written, as opposed to should not be.
_WRITE_FAILED: Final[frozenset[RecordOutcome]] = frozenset({RecordOutcome.NOT_PERMITTED, RecordOutcome.COULD_NOT_WRITE})


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
    match attempt.outcome:
        case RecordOutcome.RECORDED:
            return None
        case RecordOutcome.NOTHING_NEW:
            return "no drive has advanced its power-on hours since the last reading"
        case RecordOutcome.RECORDING_OFF:
            return "--no-record was given, so this run judged the counters without adding to them"
        case RecordOutcome.STORE_NOT_READABLE:
            return f"left the existing store at {path} alone, because it could not be read: {attempt.detail}"
        case RecordOutcome.NOT_PERMITTED:
            return f"not allowed to write counter history to {path}: {attempt.detail}"
        case RecordOutcome.COULD_NOT_WRITE:
            return f"could not write counter history to {path}: {attempt.detail}"


def record_exit_code(attempt: RecordAttempt) -> ExitCode:
    """The code ``record`` leaves for `attempt`.

    A write that FAILED is the only non-zero answer: the other outcomes are the
    store being left alone on purpose, which is what the command is for. It has
    to be non-zero somewhere, because ``record`` prints nothing at all in human
    mode and the code is then the only channel a timer has - measured before this,
    a store that could not be written left 0 and said nothing on either stream.

    The split follows ``snapshot`` rather than the errno, which would collide:
    EACCES is 13 and so is this tool's own permission code, while ENOSPC is 28,
    which means nothing here.

    Args:
        attempt: What came of the write.

    Returns:
        The exit code.

    Example:
        >>> record_exit_code(RecordAttempt(RecordOutcome.NOTHING_NEW))
        <ExitCode.SUCCESS: 0>
        >>> record_exit_code(RecordAttempt(RecordOutcome.NOT_PERMITTED, "denied"))
        <ExitCode.PERMISSION_DENIED: 13>
        >>> record_exit_code(RecordAttempt(RecordOutcome.COULD_NOT_WRITE, "full"))
        <ExitCode.GENERAL_ERROR: 1>
    """
    if attempt.outcome is RecordOutcome.NOT_PERMITTED:
        return ExitCode.PERMISSION_DENIED
    if attempt.outcome is RecordOutcome.COULD_NOT_WRITE:
        return ExitCode.GENERAL_ERROR
    return ExitCode.SUCCESS


def warn_if_the_store_was_not_written(attempt: RecordAttempt) -> None:
    """Report a failed write for a command that records only incidentally.

    ``report`` and ``health`` record because they happen to have read the
    counters, so a store they cannot write is a warning and never the answer:
    refusing to diagnose the hardware in front of somebody because a state file
    is read-only would be the wrong trade. ``record`` itself wants the opposite,
    which is why the reporting is the caller's rather than
    :func:`record_reading`'s.

    Args:
        attempt: What came of the write.
    """
    if attempt.outcome in _WRITE_FAILED:
        safe_console.echo(f"Warning: could not record counter history: {attempt.detail}", err=True)


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
    history = read.history
    # A store that could not be read is still a store. Writing this run's
    # readings over it replaces an accumulated record with a single sample, and
    # every refusal reason reaches here: a renamed host, a newer schema, a file
    # too large to read, malformed JSON. None of them is a reason to delete it.
    if not read.writable:
        return RecordAttempt(RecordOutcome.STORE_NOT_READABLE, read.refusal)
    if not settings.enabled:
        return RecordAttempt(RecordOutcome.RECORDING_OFF)
    if not has_new_readings(history, inventory.disks):
        return RecordAttempt(RecordOutcome.NOTHING_NEW)
    first_ever = not settings.path.exists()
    stamp = captured_at or datetime.now(UTC).isoformat()
    updated = record(history, inventory.disks, stamp, cap=settings.max_samples_per_drive)
    try:
        save_history(updated, settings.path)
    except PermissionError as error:
        return RecordAttempt(RecordOutcome.NOT_PERMITTED, str(error))
    except OSError as error:
        return RecordAttempt(RecordOutcome.COULD_NOT_WRITE, str(error))
    if first_ever and announce:
        # Said once per machine, so a run that writes to disk is never a silent
        # surprise, and never again after that.
        safe_console.echo(f"Recording disk error counters to {settings.path} (--no-record turns this off).", err=True)
    return RecordAttempt(RecordOutcome.RECORDED)


def analyse(
    replay: Path | None,
    output_format: OutputFormat,
    settings: HistorySettings,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
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

    Returns:
        The machine and its findings.
    """
    inventory = load_inventory(replay, output_format=output_format)
    read = read_history(inventory, settings)
    findings = diagnose(inventory, history=read.history, thresholds=thresholds)
    if replay is None and output_format is OutputFormat.HUMAN:
        warn_if_the_store_was_not_written(record_reading(inventory, read, settings))
    return Analysis(inventory, findings)


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

    `recorded` false is not a failure: it means no drive's own clock has moved
    since the last reading, so there was nothing new to store.
    """

    recorded: bool
    store: str
    drives: int


@click.command("record", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--format",
    "output_format",
    type=click.Choice(OutputFormat, case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)
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
            emit_action(
                ActionCommand.RECORD,
                RecordResult(recorded=attempt.stored, store=str(settings.path), drives=len(inventory.disks)),
                skipped=[] if reason is None else [reason],
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
@option(
    "--format",
    "output_format",
    type=click.Choice(OutputFormat, case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)
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
        inventory, findings = analyse(target, output_format, settings, thresholds)
        if output_format is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.TREND)
        else:
            from lsdsk.adapters.render.trend import render_trend  # noqa: PLC0415 - keeps the import graph flat

            history = read_history(inventory, settings).history
            console = console_for_output(display.piped_width)
            console.print(
                render_trend(inventory, history, width=console.width, wear_floor=display.wear_row_floor_percent)
            )
        raise SystemExit(exit_code_for(findings))


__all__ = [
    "HistoryRead",
    "RecordAttempt",
    "RecordOutcome",
    "analyse",
    "cli_record",
    "cli_trend",
    "read_history",
    "record_exit_code",
    "record_reading",
    "warn_if_the_store_was_not_written",
    "why_nothing_was_stored",
]
