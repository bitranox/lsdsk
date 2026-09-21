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

# Metadata
from .__init__conf__ import print_info

# Composition exports (wired adapters)
from .composition import get_config

__all__ = [
    "get_config",
    "print_info",
]
