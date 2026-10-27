"""Console script entry point with production wiring.

This module provides the entry point for console scripts (pip-installed commands).
It wires production services from the composition layer before invoking the CLI.

System Role:
    Sits at package level (outside adapters) to properly wire composition into
    the adapters layer without violating clean architecture layer constraints.
"""

from __future__ import annotations

import functools
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .application.ports import ShowsStartupProgress

#: What a Ctrl+C leaves, the shell's 128 + SIGINT; the CLI answers a later one the same way.
_INTERRUPTED = 130


def main(
    argv: Sequence[str] | None = None,
    *,
    start_notice: Callable[[list[str]], ShowsStartupProgress] | None = None,
) -> int:
    """Console script entry point with production services wired.

    The CLI and the composition are imported here, inside the guard, rather
    than at the top of the module: they are most of the start's import time,
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
        Exit code from CLI execution, 130 for an interrupt before it started.
    """
    args = list(argv) if argv is not None else sys.argv[1:]
    try:
        if start_notice is None:
            from .adapters.startup_notice import notice_for_this_start  # noqa: PLC0415 - deferred into the interrupt guard

            start_notice = notice_for_this_start
        notice = start_notice(args)
    except KeyboardInterrupt:
        return _INTERRUPTED
    notice.start()
    try:
        try:
            from .adapters.cli.main import main as cli_main  # noqa: PLC0415 - deferred into the interrupt guard
            from .composition import build_production  # noqa: PLC0415 - deferred into the interrupt guard
        except KeyboardInterrupt:
            return _INTERRUPTED
        return cli_main(args, services_factory=functools.partial(build_production, startup=notice))
    finally:
        notice.stop()


__all__ = ["main"]
