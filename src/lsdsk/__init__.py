"""Public package surface: configuration and package metadata.

lsdsk is a command-line tool, so what this module exports is deliberately small:
configuration and package metadata, which is what a program embedding the tool
asks the package itself for. Everything a reader wants is behind the CLI or, for
a program, behind ``lsdsk <command> --format json``.

That is not the whole Python API. A caller who wants the hardware without a
subprocess reaches into the modules directly - ``lsdsk.adapters.hw.snapshot``
for the reading and ``lsdsk.domain.diagnostics`` for the rules - which
``skills/lsdsk/SKILL.md`` documents. Read this ``__all__`` as the front door
rather than as the extent of what is callable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Metadata
from .__init__conf__ import print_info

if TYPE_CHECKING:
    from .composition import get_config


def __getattr__(name: str) -> object:
    """Import the wired configuration loader on first use rather than with the package.

    The console script's first act is ``from lsdsk.entry import main``, which
    imports this package before any handler of lsdsk exists. Importing the
    composition here made that a quarter of a second of imports in which a
    Ctrl+C printed a traceback; deferred, the entry point imports it inside the
    guard that answers an interrupt with 130.

    Args:
        name: The attribute asked for.

    Returns:
        ``get_config``, the configuration loader the composition wires.

    Raises:
        AttributeError: For any other name, as a module without it would.
    """
    if name == "get_config":
        from .composition import get_config as loader  # noqa: PLC0415 - deferred: see the docstring

        return loader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "get_config",
    "print_info",
]
