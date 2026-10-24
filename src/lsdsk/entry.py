"""Console script entry point with production wiring.

This module provides the entry point for console scripts (pip-installed commands).
It wires production services from the composition layer before invoking the CLI.

System Role:
    Sits at package level (outside adapters) to properly wire composition into
    the adapters layer without violating clean architecture layer constraints.
"""

from __future__ import annotations

#: What a Ctrl+C leaves, the shell's 128 + SIGINT; the CLI answers a later one the same way.
_INTERRUPTED = 130


def main() -> int:
    """Console script entry point with production services wired.

    The CLI and the composition are imported here, inside the guard, rather
    than at the top of the module: they are most of the start's import time,
    and a Ctrl+C in that window used to print a traceback through the import
    machinery before any handler of this project existed.

    Returns:
        Exit code from CLI execution, 130 for an interrupt before it started.
    """
    try:
        from .adapters.cli.main import main as cli_main  # noqa: PLC0415 - deferred into the interrupt guard
        from .composition import build_production  # noqa: PLC0415 - deferred into the interrupt guard
    except KeyboardInterrupt:
        return _INTERRUPTED
    return cli_main(services_factory=build_production)


__all__ = ["main"]
