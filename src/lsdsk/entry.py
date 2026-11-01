"""Console script entry point with production wiring.

This module provides the entry point for console scripts (pip-installed commands).
It wires production services from the composition layer before invoking the CLI.

System Role:
    Sits at package level (outside adapters) to properly wire composition into
    the adapters layer without violating clean architecture layer constraints.
"""

import sys

#: ``typing.TYPE_CHECKING`` without importing ``typing``. This module is imported
#: before any handler of lsdsk exists, so it imports nothing it can do without:
#: no ``typing``, no ``functools``, not even ``__future__`` - hence the quoted
#: annotations. ``sys`` is always loaded already, so importing it finds nothing.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .application.ports import ShowsStartupProgress
    from .composition import AppServices

#: What a Ctrl+C leaves, the shell's 128 + SIGINT; the CLI answers a later one the same way.
_INTERRUPTED = 130


def main(
    argv: "Sequence[str] | None" = None,
    *,
    start_notice: "Callable[[list[str]], ShowsStartupProgress] | None" = None,
) -> int:
    """Console script entry point with production services wired.

    The CLI and the composition are imported inside the guard, in
    :func:`_run_with`, rather than at the top of the module: they are most of the start's import time,
    and a Ctrl+C in that window used to print a traceback through the import
    machinery before any handler of this project existed.

    The startup line starts BEFORE that import, because the import is most of
    the silence it fills, and it is stopped on every way out of here. The CLI
    receives it through the services and gives it way, resumes and stops it on
    the way into the view.

    Args:
        argv: The arguments after the program name; ``sys.argv[1:]`` when None.
        start_notice: Decides from the arguments which startup line this run
            gets; the one that draws on an interactive start when None.

    Returns:
        Exit code from CLI execution, 130 for an interrupt the CLI did not answer
        itself: one while the line starts or stops, or while lsdsk imports itself.
    """
    args = list(argv) if argv is not None else sys.argv[1:]
    try:
        if start_notice is None:
            from .adapters.startup_notice import notice_for_this_start  # noqa: PLC0415 - deferred into the interrupt guard

            start_notice = notice_for_this_start
        return _run_with(start_notice(args), args)
    except KeyboardInterrupt:
        return _INTERRUPTED


def _run_with(notice: "ShowsStartupProgress", args: "list[str]") -> int:
    """Draw `notice`, import and run the CLI, and stop the notice on every way out.

    The start is inside the ``try`` as well: a Ctrl+C that lands while the line
    starts reaches the ``finally`` like one during the import, and the caller's
    guard answers it with 130.

    Args:
        notice: The startup line this run gets.
        args: The arguments after the program name.

    Returns:
        Exit code from CLI execution.
    """
    try:
        notice.start()
        from .adapters.cli.main import main as cli_main  # noqa: PLC0415 - deferred into the interrupt guard
        from .composition import build_production  # noqa: PLC0415 - deferred into the interrupt guard

        def services() -> "AppServices":
            return build_production(startup=notice)

        return cli_main(args, services_factory=services)
    finally:
        notice.stop()


__all__ = ["main"]
