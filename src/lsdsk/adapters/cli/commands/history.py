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
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import lib_log_rich.runtime
import rich_click as click
from pydantic import BaseModel, ValidationError

from lsdsk.adapters.history.store import load_history, save_history
from lsdsk.adapters.hw.snapshot import CaptureEnvelope
from lsdsk.adapters.textfile import read_text_bounded
from lsdsk.domain.diagnostics import diagnose
from lsdsk.domain.enums import CliCommand, OutputFormat
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import History, has_new_readings, record
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..envelope import emit_action
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
    """

    history: History
    writable: bool


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
        return HistoryRead(History(hostname=inventory.hostname), writable=False)


def record_reading(
    inventory: Inventory,
    read: HistoryRead,
    settings: HistorySettings,
    captured_at: str | None = None,
    *,
    announce: bool = True,
) -> bool:
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
        Whether a sample was written.
    """
    history = read.history
    # A store that could not be read is still a store. Writing this run's
    # readings over it replaces an accumulated record with a single sample, and
    # every refusal reason reaches here: a renamed host, a newer schema, a file
    # too large to read, malformed JSON. None of them is a reason to delete it.
    if not read.writable:
        return False
    if not settings.enabled or not has_new_readings(history, inventory.disks):
        return False
    first_ever = not settings.path.exists()
    stamp = captured_at or datetime.now(UTC).isoformat()
    updated = record(history, inventory.disks, stamp, cap=settings.max_samples_per_drive)
    try:
        save_history(updated, settings.path)
    except OSError as error:
        safe_console.echo(f"Warning: could not record counter history: {error}", err=True)
        return False
    if first_ever and announce:
        # Said once per machine, so a run that writes to disk is never a silent
        # surprise, and never again after that.
        safe_console.echo(f"Recording disk error counters to {settings.path} (--no-record turns this off).", err=True)
    return True


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
    inventory = load_inventory(replay)
    read = read_history(inventory, settings)
    findings = diagnose(inventory, history=read.history, thresholds=thresholds)
    if replay is None and output_format is OutputFormat.HUMAN:
        record_reading(inventory, read, settings)
    return Analysis(inventory, findings)


def _capture_stamp(replay: Path | None) -> str | None:
    """When a replayed capture was taken, or ``None`` for a live run.

    Read through the same Pydantic envelope that validates a snapshot rather
    than by reaching into the raw mapping, so the field is typed at exactly one
    place. A capture that cannot be read at all is not an error here: the stamp
    is for display, and the reading itself has already succeeded by this point.
    """
    if replay is None:
        return None
    try:
        return CaptureEnvelope.model_validate_json(read_text_bounded(replay, what="a snapshot")).captured_at
    except (ConfigurationError, ValidationError):
        return None


class RecordResult(BaseModel):
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
    type=click.Choice([choice.value for choice in OutputFormat], case_sensitive=False),
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
def cli_record(ctx: click.Context, replay: Path | None, output_format: str) -> None:
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
        extra={"command": "record"},
    ):
        # Resolve once: a bare ``replay`` here would honour ``record --replay`` and
        # silently drop the root group's ``--replay``, sampling this machine into
        # the store under its own hostname while the caller asked for another's.
        target = effective_replay(ctx, replay)
        inventory = load_inventory(target)
        read = read_history(inventory, settings)
        wrote = record_reading(inventory, read, settings, captured_at=_capture_stamp(target), announce=False)
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_action(
                "record",
                RecordResult(recorded=wrote, store=str(settings.path), drives=len(inventory.disks)),
                # A run that stored nothing is not a failure: it means no drive's
                # own clock has advanced since the last reading, so there is
                # nothing new to say. A caller has to be able to tell that from a
                # run that could not read anything at all.
                skipped=[] if wrote else ["no drive has advanced its power-on hours since the last reading"],
            )
        raise SystemExit(ExitCode.SUCCESS)


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
    type=click.Choice([choice.value for choice in OutputFormat], case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)
@click.pass_context
def cli_trend(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """Show what each error counter is doing over time, not just its total.

    A counter is a lifetime total the drive keeps in its own non-volatile
    table, so it survives reboots and says nothing about when the damage
    happened. This says whether it is still happening.
    """
    with lib_log_rich.runtime.bind(job_id="cli-trend", extra={"command": CliCommand.TREND.value}):
        chosen = OutputFormat(output_format.lower())
        settings = resolve_history(ctx)
        thresholds, display = resolve_tunables(ctx)
        target = effective_replay(ctx, replay)
        inventory, findings = analyse(target, chosen, settings, thresholds)
        if chosen is OutputFormat.JSON:
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
    "analyse",
    "cli_record",
    "cli_trend",
    "read_history",
    "record_reading",
]
