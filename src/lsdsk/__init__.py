"""Public package surface: configuration and package metadata.

lsdsk is a command-line tool, so its public Python surface is deliberately
small. Everything a reader wants is behind the CLI or, for a program, behind
``lsdsk <command> --format json``.
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
