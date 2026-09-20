"""CLI entry point and execution wrapper.

Provides the main entry point used by console scripts and ``python -m``
execution, ensuring consistent error handling and traceback restoration.

Contents:
    * :func:`main` - Primary entry point for CLI execution.
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

import click
import lib_cli_exit_tools
import lib_log_rich.runtime

from lsdsk import __init__conf__

from ..config.loader import get_config
from ..config.tunables import DisplaySettings, get_display_settings
from . import safe_console
from .context import (
    apply_traceback_preferences,
    restore_traceback_state,
    snapshot_traceback_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from lsdsk.composition import AppServices


def _display_settings() -> DisplaySettings:
    """Layout settings for the error path, falling back to the shipped ones.

    This runs while handling an exception, possibly one raised by configuration
    loading itself, so a failure here must not replace the error the user needs
    to see with a second one.
    """
    try:
        return get_display_settings(get_config())
    except Exception:
        return DisplaySettings()


def _answer_a_refused_command_line(exc: click.ClickException, args: Sequence[str]) -> None:
    """Add the failure envelope for a command line click refused, if JSON was asked for.

    The prose on stderr is unchanged and already written by the time this runs:
    this is an addition for the machine, not a move. Without it a ``--format
    json`` pipeline met the one failure class that answered in prose alone, and
    could not tell a mistyped option from a command that produced no data.

    Which command failed comes from click's own context rather than from the
    command line, so only the format itself is read twice. ``info_name`` is the
    subcommand for an error inside one and the program name for an error before
    any was resolved, which is what an unknown command is.

    The write goes through the same guard as the prose, on stdout: a reader that
    has already left costs this diagnostic and nothing else, because the code
    click decided is 2 and
    :func:`~.exit_codes.outranks_a_departed_reader` says a refusal stands whoever
    was reading.

    Args:
        exc: The exception click raised while parsing.
        args: The command line it was parsing, without the program name.
    """
    # Deferred for the same reason as the `cli` import above: importing main stays cheap.
    from .envelope import UNNAMED_COMMAND, asked_for_json, emit_error  # noqa: PLC0415 - deferred: see above

    if not asked_for_json(args):
        return
    context = exc.ctx if isinstance(exc, click.UsageError) else None
    named = context.info_name if context is not None else None
    safe_console.write_unless_the_reader_left(
        lambda: emit_error(named or UNNAMED_COMMAND, exc.exit_code, exc.format_message()),
        err=False,
    )


def _run_cli(argv: Sequence[str] | None, *, services_factory: Callable[[], AppServices]) -> int:
    """Execute the CLI with exception handling.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        services_factory: Factory function that returns AppServices. Passed via ctx.obj.

    Returns:
        Exit code produced by the command.
    """
    from .root import cli  # noqa: PLC0415 - deferred: lazy-loads the command tree so importing main stays cheap

    # Use Click's native invocation with obj parameter since lib_cli_exit_tools.run_cli
    # doesn't support passing obj. We replicate its behavior while adding obj support.
    args = list(argv) if argv is not None else sys.argv[1:]

    try:
        cli.main(
            args=args,
            prog_name=__init__conf__.shell_command,
            obj=services_factory,
            standalone_mode=False,
        )
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.ClickException as exc:
        # Through the guard: the BrokenPipeError from printing a usage message is
        # raised INSIDE this handler, so unguarded it escapes main() itself and the
        # caller is told nothing about the usage error it actually made.
        safe_console.write_unless_the_reader_left(exc.show)
        _answer_a_refused_command_line(exc, args)
        return exc.exit_code
    except SystemExit as exc:
        # A command raising SystemExit is stating the code it means to leave
        # with, which is ordinary control flow: `scan` exits 1 when it finds
        # something wrong. Falling through to the handler below would print
        # "SystemExit: 1" at the user as though the tool had broken.
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    except BaseException as exc:
        # Catch BaseException (not just Exception) to handle SystemExit, KeyboardInterrupt,
        # and all errors at the CLI boundary. This ensures consistent error formatting via
        # lib_cli_exit_tools regardless of exception type. Intentional, not a bug.
        tracebacks_enabled = bool(getattr(lib_cli_exit_tools.config, "traceback", False))
        apply_traceback_preferences(tracebacks_enabled)
        # Read from configuration rather than the module constants: the two keys
        # are documented in the shipped [display] section, and a documented key
        # nothing reads is a lie in the config file.
        display = _display_settings()
        length_limit = display.traceback_verbose_limit if tracebacks_enabled else display.traceback_summary_limit
        safe_console.write_unless_the_reader_left(
            lambda: lib_cli_exit_tools.print_exception_message(trace_back=tracebacks_enabled, length_limit=length_limit)
        )
        return lib_cli_exit_tools.get_system_exit_code(exc)


def main(
    argv: Sequence[str] | None = None,
    *,
    restore_traceback: bool = True,
    services_factory: Callable[[], AppServices] | None = None,
) -> int:
    """Execute the CLI with error handling and return the exit code.

    Provides the single entry point used by console scripts and
    ``python -m`` execution so that behaviour stays identical across transports.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        restore_traceback: Whether to restore prior traceback configuration after execution.
        services_factory: Factory function returning AppServices. Required.
            Callers outside the adapters layer should pass ``build_production``.

    Returns:
        Exit code reported by the CLI run.

    Raises:
        ValueError: If services_factory is not provided.

    Example:
        >>> from lsdsk.composition import build_production
        >>> exit_code = main(["--help"], services_factory=build_production)  # doctest: +SKIP
        >>> exit_code == 0  # doctest: +SKIP
        True
    """
    if services_factory is None:
        raise ValueError("services_factory is required. Pass build_production from composition layer.")

    previous_state = snapshot_traceback_state()
    try:
        code = _run_cli(argv, services_factory=services_factory)
    finally:
        if restore_traceback:
            restore_traceback_state(previous_state)
        # Only shutdown logging from main thread to avoid killing logging for other threads.
        is_main_thread = threading.current_thread() is threading.main_thread()
        if is_main_thread and lib_log_rich.runtime.is_initialised():
            lib_log_rich.runtime.shutdown()

    # AFTER the logging shutdown, not around it: lib_log_rich is queue-based, so a
    # line logged during the run reaches stderr only when that drain runs. Flushing
    # before it left those bytes in the buffer to be retried at interpreter
    # shutdown, where the failure is outside every handler and reads as 120.
    #
    # Through the flush, not straight out: a command whose output fits Python's
    # block buffer has not touched the pipe yet, so the reader leaving would
    # otherwise surface only in that same exit flush. A successful flush returns
    # the code unchanged.
    try:
        return safe_console.flush_streams_or_leave(code)
    finally:
        # Last of all: this hands back a descriptor that may be a pipe nobody is
        # reading, so anything writing afterwards would raise where no handler is
        # left. Until then such a write goes quietly to the null device.
        safe_console.restore_original_streams()


__all__ = ["main"]
