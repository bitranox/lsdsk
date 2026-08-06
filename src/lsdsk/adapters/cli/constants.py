"""Shared CLI constants.

Centralizes configuration values used across CLI modules to ensure consistency.

Contents:
    * :data:`CLICK_CONTEXT_SETTINGS` - Shared Click settings for help display.
"""

from __future__ import annotations

from typing import Final

#: Shared Click context flags so help output stays consistent across commands.
CLICK_CONTEXT_SETTINGS: Final[dict[str, list[str]]] = {"help_option_names": ["-h", "--help"]}

#: Character budget used when printing truncated tracebacks.

#: Character budget used when verbose tracebacks are enabled.

__all__ = [
    "CLICK_CONTEXT_SETTINGS",
]
