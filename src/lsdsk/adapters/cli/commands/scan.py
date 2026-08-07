"""The commands that look at storage: the report, the tables and the snapshot.

Contents:
    * :func:`run_default_report` - the whole machine, which a bare ``lsdsk`` runs
    * :func:`cli_topology` - a problem summary above the disk-to-controller tree
    * :func:`cli_controllers` - controllers and their PCIe placement
    * :func:`cli_disks` - one row per disk
    * :func:`cli_health` - wear, temperature and error counters
    * :func:`cli_slots` - the mainboard's PCIe ports and what occupies them
    * :func:`cli_smart` - every disk's SMART attributes
    * :func:`cli_findings` - every diagnosis in full
    * :func:`cli_snapshot` - capture the raw reading for replay or a bug report

Each command is one section of the default report, for when the reader already
knows which section they want.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import lib_log_rich.runtime
import rich_click as click
from lib_layered_config import Config
from pydantic import BaseModel
from rich.console import Console
from rich.text import Text

from lsdsk.adapters.config.history import HistorySettings, get_history_settings
from lsdsk.adapters.config.tunables import (
    DEFAULT_PIPED_WIDTH,
    DisplaySettings,
    get_display_settings,
    get_thresholds,
)
from lsdsk.adapters.hw import snapshot as snapshot_adapter
from lsdsk.adapters.render import report, theme
from lsdsk.adapters.render.tables import counter_legend
from lsdsk.domain.diagnostics import count_by_severity
from lsdsk.domain.enums import CliCommand, Environment, OutputFormat, Severity
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.models import Controller, Disk, Finding, Inventory, PcieSlot
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import get_cli_context
from ..envelope import emit_action
from ..exit_codes import ExitCode
from ..typed_click import option

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

PIPED_WIDTH = DEFAULT_PIPED_WIDTH

_REPLAY_OPTION = option(
    "--replay",
    "replay",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Render a snapshot captured earlier instead of reading this machine.",
)
_FORMAT_OPTION = option(
    "--format",
    "output_format",
    type=click.Choice([choice.value for choice in OutputFormat], case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)


def console_for_output(piped_width: int = PIPED_WIDTH) -> Console:
    """Build a console that writes through the encoding-safe stream."""
    return Console(file=safe_console.safe_stream(), width=_width(piped_width), highlight=False)


def _width(piped_width: int = PIPED_WIDTH) -> int | None:
    """Terminal width, or the configured fixed width when output is redirected."""
    console = Console()
    return None if console.is_terminal else piped_width


def effective_replay(ctx: click.Context, replay: Path | None) -> Path | None:
    """Resolve which snapshot to render, if any.

    A subcommand's own ``--replay`` wins over the global one, mirroring how
    ``--profile`` already behaves on this CLI.

    Args:
        ctx: The Click context.
        replay: The subcommand's own option value.

    Returns:
        The snapshot to render, or ``None`` to read this machine.
    """
    if replay is not None:
        return replay
    try:
        return get_cli_context(ctx).replay
    except RuntimeError:
        # Invoked directly in a test, without the root group having run.
        return None


def load_inventory(replay: Path | None) -> Inventory:
    """Read this machine, or a snapshot captured from another one.

    Args:
        replay: A snapshot file, or ``None`` to read this machine.

    Returns:
        The machine as the domain sees it.

    Raises:
        SystemExit: With a configuration exit code when the hardware or the
            snapshot cannot be read.
    """
    try:
        return snapshot_adapter.load(replay) if replay is not None else snapshot_adapter.collect()
    except ConfigurationError as error:
        safe_console.echo(f"Error: {error}", err=True)
        raise SystemExit(ExitCode.CONFIG_ERROR) from error
    except PermissionError as error:
        safe_console.echo(f"Error: {error}", err=True)
        raise SystemExit(ExitCode.PERMISSION_DENIED) from error


def resolve_history(ctx: click.Context) -> HistorySettings:
    """Settle how counter history behaves for this run.

    Three sources, in the usual order: the shipped default, the ``[history]``
    configuration section, then the global command-line options. Both options
    are global, next to ``--replay``, because every command's findings are
    judged against the same recorded past; putting them on each command would
    say the opposite.

    ``--no-record`` lands on ``enabled``, so one object answers both "where does
    it live" and "does this run add to it" rather than a pair nobody can name.
    It never suppresses READING, so findings stay graded against the past.

    Args:
        ctx: The Click context.

    Returns:
        The settled behaviour for this run.
    """
    try:
        cli_context = get_cli_context(ctx)
    except RuntimeError:
        # Invoked directly in a test, without the root group having run.
        return get_history_settings(Config({}, {}))
    settings = get_history_settings(cli_context.config, path_override=cli_context.history_file)
    if cli_context.no_record:
        return settings.model_copy(update={"enabled": False})
    return settings


class Analysis(NamedTuple):
    """A machine and what the rules concluded about it.

    Returned rather than a bare pair because two functions produce this exact
    shape and nine call sites unpack it: both halves are objects, so a swapped
    unpacking is a type error, but a NAMED result also says what the second half
    is without the reader following it back to its producer.
    """

    inventory: Inventory
    findings: tuple[Finding, ...]


class Tunables(NamedTuple):
    """The judgement and layout values settled for one run."""

    thresholds: Thresholds
    display: DisplaySettings


def note(text: str) -> Text:
    """A dimmed prose line for the console to wrap.

    Prose printed with a plain echo keeps its own length and runs off the side of
    a narrow terminal, while every table beside it fits itself. Sending it
    through the console makes the two behave alike.
    """
    return Text(text, style=theme.STYLE_UNKNOWN)


def resolve_tunables(ctx: click.Context) -> Tunables:
    """The judgement and layout values settled for this run.

    Args:
        ctx: The Click context, which carries the merged configuration.

    Returns:
        The thresholds the rules weigh against and the display settings.
    """
    try:
        config = get_cli_context(ctx).config
    except RuntimeError:
        # Invoked directly in a test, without the root group having run.
        config = Config({}, {})
    return Tunables(get_thresholds(config), get_display_settings(config))


def analyse_run(
    ctx: click.Context,
    replay: Path | None,
    output_format: OutputFormat,
) -> Analysis:
    """Read the machine, judge it against its recorded past, and sample it.

    Args:
        ctx: The Click context, which carries the global history options.
        replay: A snapshot to render instead of reading this machine.
        output_format: What the caller asked for.

    Returns:
        The machine and its findings.
    """
    from .history import analyse  # noqa: PLC0415 - deferred: history imports this module

    return analyse(replay, output_format, resolve_history(ctx), resolve_tunables(ctx).thresholds)


def exit_code_for(findings: Sequence[Finding]) -> ExitCode:
    """Map findings onto a process exit code.

    Zero when nothing was found, one when something was, which is what a
    monitoring system needs.  Hints alone are not a failure: they describe a
    ceiling, not a fault.

    Args:
        findings: The findings from one run.

    Returns:
        The exit code to leave with.
    """
    counts = count_by_severity(tuple(findings))
    actionable = counts[Severity.CRITICAL] + counts[Severity.WARNING]
    return ExitCode.GENERAL_ERROR if actionable else ExitCode.SUCCESS


class ScanData(BaseModel):
    """The payload half of the machine-readable envelope.

    The domain values are declared by their own types rather than as dicts.
    Pydantic serialises a frozen dataclass directly, so the schema is stated
    once and a renamed field is a type error here instead of a silently changed
    output contract. It also removes the dump-to-dict step entirely: one
    conversion at this boundary and none before it.
    """

    hostname: str
    board: str
    privileged: bool
    environment: Environment
    environment_detail: str
    devices_accessible: bool
    controllers: tuple[Controller, ...]
    disks: tuple[Disk, ...]
    slots: tuple[PcieSlot, ...]
    findings: tuple[Finding, ...]


class ScanEnvelope(BaseModel):
    """The machine-readable envelope another program consumes.

    A model rather than a hand-built dict because this is an output boundary and
    the contract another program is written against. ``command`` names the
    command that produced it, which a literal in a shared builder cannot do:
    every command then claims to be the same one.

    ``ok`` and ``skipped`` together say whether the answer is COMPLETE, which is
    the question a caller cannot otherwise ask: a machine reading only ``data``
    cannot tell a drive with no errors from a drive whose counters could not be
    read. A constant ``ok`` carried no information at all.
    """

    ok: bool
    command: CliCommand
    data: ScanData
    skipped: list[str]


def build_envelope(
    inventory: Inventory,
    findings: Sequence[Finding],
    command: CliCommand,
) -> ScanEnvelope:
    """Build the machine-readable envelope another program consumes.

    Args:
        inventory: The machine that was scanned.
        findings: What the diagnosis produced.
        command: Which command is emitting this. Required, with no default: a
            default here is what once made every command claim to be ``scan``.

    Returns:
        The envelope: ``ok``, ``command``, ``data`` and ``skipped``.
    """
    skipped = _skipped_readings(inventory)
    return ScanEnvelope(
        ok=not skipped,
        skipped=skipped,
        command=command,
        data=ScanData(
            hostname=inventory.hostname,
            board=inventory.board,
            privileged=inventory.privileged,
            # A consumer needs to know whose hardware this is and why a value is
            # missing, exactly as the printed report says in its caveat lines.
            environment=inventory.environment,
            environment_detail=inventory.environment_detail,
            devices_accessible=inventory.devices_accessible,
            controllers=inventory.controllers,
            disks=inventory.disks,
            slots=inventory.slots,
            findings=tuple(findings),
        ),
    )


def _skipped_readings(inventory: Inventory) -> list[str]:
    """Name what could not be read, so a caller can tell partial from complete.

    The human report says this in a caveat line. Without it here, a machine sees
    a null counter and cannot tell "this drive reports zero errors" from "nobody
    was allowed to ask", which are opposite conclusions.

    Args:
        inventory: The machine that was scanned.

    Returns:
        One entry per class of reading that was not taken, empty when the scan
        was complete.
    """
    skipped: list[str] = []
    if not inventory.privileged:
        if not inventory.devices_accessible:
            skipped.append("smart: no device nodes are exposed here, so elevating would not help")
        else:
            skipped.append("smart: needs root or Administrator")
        skipped.append("slot-numbers: needs root or Administrator")
    if not inventory.readings_are_physical:
        skipped.append("physical-link-rules: suppressed, the hypervisor invents these values")
    return skipped


def emit_json(inventory: Inventory, findings: Sequence[Finding], command: CliCommand) -> None:
    """Write the machine-readable envelope."""
    envelope = build_envelope(inventory, findings, command)
    safe_console.echo(envelope.model_dump_json(indent=2))


def run_default_view(
    replay: Path | None,
    *,
    settings: HistorySettings | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    display: DisplaySettings | None = None,
) -> None:
    """Open the interactive view, or print the page when nothing can be typed at.

    A bare ``lsdsk`` still answers "what is wrong with this machine" without
    being asked for a section. Where somebody is sitting at it, it does that
    interactively, because the whole machine on one page is more than a reader
    can take in at once. Anywhere else - a pipe, a redirect, a CI log, a
    subprocess - the page is printed exactly as before.

    "Somebody is sitting at it" means both ends are terminals, not just the
    output one: Textual reads key events from stdin, so ``lsdsk < /dev/null``
    would otherwise open a full-screen view nobody can quit.

    That test has a blind spot no test can close, so a caller stuck in it needs
    a way out that does not depend on being recognised: `lsdsk report` names the
    page outright.

    The exit code is the findings' either way, so ``lsdsk; echo $?`` means the
    same thing in both, and nothing that scripts this has to care which ran.

    Args:
        replay: A snapshot to render instead of reading this machine.
        settings: How counter history behaves for this run.
        thresholds: The values the rules judge by.
        display: The values the layout uses.

    Raises:
        SystemExit: Always, carrying the exit code the findings imply.
    """
    # BOTH ends, not just stdout: something has to press q.
    #
    # This CANNOT tell a person from an automation that allocates a terminal.
    # Measured in a Jupyter kernel: IPython runs `!cmd` under pexpect, which
    # gives the child a real pseudo-terminal on both ends, so it is
    # indistinguishable here from somebody sitting at a shell - and the CI
    # notebook job hung for 900 seconds waiting for a keypress that had no
    # keyboard behind it. `script`, expect and pty-allocating job runners look
    # the same. That is what `lsdsk report` is for.
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        run_default_report(replay, settings=settings, thresholds=thresholds, display=display)
        return

    from .history import analyse, read_history  # noqa: PLC0415 - deferred: history imports this module

    resolved = settings if settings is not None else get_history_settings(Config({}, {}))
    with lib_log_rich.runtime.bind(job_id="cli-tui", extra={"command": "tui"}):
        inventory, findings = analyse(replay, OutputFormat.HUMAN, resolved, thresholds)
        from lsdsk.adapters.tui import LsdskApp  # noqa: PLC0415 - keeps textual off the fast path

        history = read_history(inventory, resolved).history
        LsdskApp(inventory, history).run()
        raise SystemExit(exit_code_for(findings))


def run_default_report(
    replay: Path | None,
    *,
    settings: HistorySettings | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    display: DisplaySettings | None = None,
) -> None:
    """Render the whole machine on one page.

    Reached by a bare ``lsdsk`` whenever its output is not a terminal, and it is
    the form every script, pipe and CI log gets.

    Not a Click command: it has no name to invoke and registering one would put
    a redundant entry in ``--help`` beside the sections it already contains.

    Args:
        replay: A snapshot to render instead of reading this machine.
        settings: How counter history behaves for this run.
        thresholds: The judgement values the rules weigh against.
        display: Layout values, or the shipped ones.

    Raises:
        SystemExit: Always, carrying the exit code the findings imply.
    """
    from .history import analyse, read_history  # noqa: PLC0415 - deferred: history imports this module

    resolved = settings if settings is not None else get_history_settings(Config({}, {}))
    with lib_log_rich.runtime.bind(  # Not a CliCommand member: the default view emits no envelope, so it has
        # no name to appear under in one.
        job_id="cli-report",
        extra={"command": "report"},
    ):
        inventory, findings = analyse(replay, OutputFormat.HUMAN, resolved, thresholds)
        from lsdsk.adapters.render.full import render_full  # noqa: PLC0415 - keeps the import graph flat

        laid_out = display if display is not None else DisplaySettings()
        console = console_for_output(laid_out.piped_width)
        history = read_history(inventory, resolved).history
        console.print(render_full(inventory, findings, width=console.width, history=history, display=laid_out))
        raise SystemExit(exit_code_for(findings))


@click.command("report", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@click.pass_context
def cli_report(ctx: click.Context, replay: Path | None) -> None:
    """Show the whole machine on one page, which every section below is part of.

    What a bare `lsdsk` prints when its output is not a terminal, asked for by
    name. That is the point of it: at a terminal a bare `lsdsk` opens the
    interactive view instead, and some callers cannot be told apart from a
    person - a notebook cell, `script`, `expect` and pty-allocating job runners
    all present a terminal on both ends - so anything unattended should name the
    page rather than rely on being recognised.

    No `--format`: this is every section at once, and the machine-readable form
    of that is `lsdsk snapshot`, which captures the reading itself.
    """
    with lib_log_rich.runtime.bind(job_id="cli-report", extra={"command": "report"}):
        thresholds, display = resolve_tunables(ctx)
        run_default_report(
            effective_replay(ctx, replay),
            settings=resolve_history(ctx),
            thresholds=thresholds,
            display=display,
        )


@click.command("topology", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_topology(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """Show the problem summary and the disk-to-controller tree.

    This is one section of the page a bare `lsdsk` renders, not that whole page.
    """
    with lib_log_rich.runtime.bind(job_id="cli-topology", extra={"command": CliCommand.TOPOLOGY.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        logger.debug("Scanned %d disks on %d controllers", len(inventory.disks), len(inventory.controllers))

        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.TOPOLOGY)
        else:
            console = console_for_output(display.piped_width)
            console.print(report.render_header(inventory))
            console.print()
            console.print(report.render_verdict(findings, display.summary_limit))
            console.print()
            console.print(report.render_tree(inventory, findings, width=console.width))
        raise SystemExit(exit_code_for(findings))


@click.command("smart", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_smart(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """Show every disk's SMART attributes against its own thresholds."""
    with lib_log_rich.runtime.bind(job_id="cli-smart", extra={"command": CliCommand.SMART.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.SMART)
        else:
            from lsdsk.adapters.render.report import render_smart  # noqa: PLC0415 - keeps the import graph flat

            console = console_for_output(display.piped_width)
            console.print(render_smart(inventory, width=console.width))
        raise SystemExit(exit_code_for(findings))


@click.command("findings", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_findings(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """Explain every problem and improvement in full."""
    with lib_log_rich.runtime.bind(job_id="cli-findings", extra={"command": CliCommand.FINDINGS.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.FINDINGS)
        else:
            console = console_for_output(display.piped_width)
            console.print(report.render_header(inventory))
            console.print()
            console.print(report.render_findings(findings))
        raise SystemExit(exit_code_for(findings))


@click.command("controllers", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_controllers(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """List storage controllers, their PCIe placement and their free ports."""
    with lib_log_rich.runtime.bind(job_id="cli-controllers", extra={"command": CliCommand.CONTROLLERS.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.CONTROLLERS)
        else:
            from lsdsk.adapters.render.tables import render_controllers  # noqa: PLC0415 - keeps the import graph flat

            console = console_for_output(display.piped_width)
            console.print(render_controllers(inventory, findings, width=console.width))
        raise SystemExit(exit_code_for(findings))


@click.command("slots", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_slots(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """Show the mainboard's PCIe ports, what occupies them and what is free."""
    with lib_log_rich.runtime.bind(job_id="cli-slots", extra={"command": CliCommand.SLOTS.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.SLOTS)
        else:
            from lsdsk.adapters.render.report import render_slots  # noqa: PLC0415 - keeps the import graph flat

            console = console_for_output(display.piped_width)
            console.print(render_slots(inventory, width=console.width))
        raise SystemExit(exit_code_for(findings))


@click.command("disks", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_disks(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """List every disk with its identity and its interface speed."""
    with lib_log_rich.runtime.bind(job_id="cli-disks", extra={"command": CliCommand.DISKS.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.DISKS)
        else:
            from lsdsk.adapters.render.tables import render_disks  # noqa: PLC0415 - keeps the import graph flat

            console = console_for_output(display.piped_width)
            console.print(render_disks(inventory, findings, width=console.width))
        raise SystemExit(exit_code_for(findings))


@click.command("health", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@_FORMAT_OPTION
@click.pass_context
def cli_health(ctx: click.Context, replay: Path | None, output_format: str) -> None:
    """Show wear, temperature, hours and error counters for every disk."""
    with lib_log_rich.runtime.bind(job_id="cli-health", extra={"command": CliCommand.HEALTH.value}):
        inventory, findings = analyse_run(ctx, effective_replay(ctx, replay), OutputFormat(output_format.lower()))
        display = resolve_tunables(ctx).display
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_json(inventory, findings, CliCommand.HEALTH)
        else:
            from lsdsk.adapters.render.tables import render_health  # noqa: PLC0415 - keeps the import graph flat

            console = console_for_output(display.piped_width)
            from .history import read_history  # noqa: PLC0415 - deferred: history imports this module

            history = read_history(inventory, resolve_history(ctx)).history
            console.print(render_health(inventory, findings, width=console.width, history=history))
            # Through the console rather than a plain echo, so prose wraps to the
            # terminal instead of running off the side of a narrow one. The
            # tables already fit themselves; a bare echo does not.
            legend = counter_legend(inventory, history)
            if legend:
                console.print("")
                console.print(note(legend))
            if not inventory.privileged:
                console.print("")
                console.print(
                    note("Running unprivileged. Wear, error counters and SMART attributes need root or Administrator.")
                )
        raise SystemExit(exit_code_for(findings))


@click.command("tui", context_settings=CLICK_CONTEXT_SETTINGS)
@_REPLAY_OPTION
@click.pass_context
def cli_tui(ctx: click.Context, replay: Path | None) -> None:
    """Open the interactive view, with a page per question."""
    with lib_log_rich.runtime.bind(job_id="cli-tui", extra={"command": "tui"}):
        inventory = load_inventory(effective_replay(ctx, replay))
        from lsdsk.adapters.tui import LsdskApp  # noqa: PLC0415 - keeps textual off the fast path

        # Without this the Trend page always says nothing has been recorded and
        # the Health page loses every rising mark, on a machine whose history is
        # sitting on disk and which `lsdsk health` reads correctly.
        from .history import read_history  # noqa: PLC0415 - deferred: history imports this module

        history = read_history(inventory, resolve_history(ctx)).history
        LsdskApp(inventory, history).run()
        raise SystemExit(ExitCode.SUCCESS)


@click.command("snapshot", context_settings=CLICK_CONTEXT_SETTINGS)
@_FORMAT_OPTION
@option(
    "--output",
    "-o",
    "output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    required=True,
    help="Where to write the snapshot.",
)
@click.pass_context
def cli_snapshot(ctx: click.Context, output: Path, output_format: str) -> None:
    """Capture this machine's raw reading for replay elsewhere.

    The snapshot holds the reading, not the rendered result, so replaying it
    runs the same decoding and diagnosis a live run does.  That makes it a
    reproducible bug report as well as a way to inspect a server from your desk.

    Refuses rather than ignores a global ``--replay``. This command always reads
    the machine it runs on, so honouring the flag would mean re-serialising
    somebody else's capture, and ignoring it wrote THIS machine's reading into a
    file the caller believed held the other one - mislabelled data, silently, at
    exit 0. Copying a capture is a job for ``cp``.
    """
    with lib_log_rich.runtime.bind(job_id="cli-snapshot", extra={"command": "snapshot"}):
        if effective_replay(ctx, None) is not None:
            safe_console.echo(
                "Error: snapshot always captures the machine it runs on, so --replay does not apply. "
                "Copy the capture file itself, or drop --replay to capture this machine.",
                err=True,
            )
            raise SystemExit(ExitCode.INVALID_ARGUMENT)
        try:
            capture = snapshot_adapter.read_current_machine()
        except ConfigurationError as error:
            safe_console.echo(f"Error: {error}", err=True)
            raise SystemExit(ExitCode.CONFIG_ERROR) from error
        snapshot_adapter.save(capture, output)
        if OutputFormat(output_format.lower()) is OutputFormat.JSON:
            emit_action("snapshot", {"path": str(output), "schema": snapshot_adapter.SCHEMA_VERSION})
        else:
            safe_console.echo(f"Wrote {output}")
        raise SystemExit(ExitCode.SUCCESS)


__all__ = [
    "build_envelope",
    "cli_controllers",
    "cli_disks",
    "cli_findings",
    "cli_health",
    "cli_snapshot",
    "cli_topology",
    "cli_tui",
    "effective_replay",
    "exit_code_for",
    "load_inventory",
    "run_default_report",
]
