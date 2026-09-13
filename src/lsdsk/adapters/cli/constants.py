"""Shared CLI constants.

Centralizes configuration values used across CLI modules to ensure consistency.

Contents:
    * :data:`CLICK_CONTEXT_SETTINGS` - Shared Click settings for help display.
    * :data:`TREE_DENSITY_TOKENS` - The machine-readable density tokens.
"""

from __future__ import annotations

from typing import Final

from lsdsk.domain.enums import TreeDensity

#: Shared Click context flags so help output stays consistent across commands.
CLICK_CONTEXT_SETTINGS: Final[dict[str, list[str]]] = {"help_option_names": ["-h", "--help"]}

#: The tokens the ``--tree-density`` choices register, in one list so the
#: option after a subcommand, the global one on the root group and the test
#: that pins the contract name the same vocabulary. Sorted, so the choice's
#: metavar lists them deterministically.
TREE_DENSITY_TOKENS: Final[tuple[str, ...]] = tuple(sorted(str(density) for density in TreeDensity))

__all__ = [
    "CLICK_CONTEXT_SETTINGS",
    "TREE_DENSITY_TOKENS",
]
