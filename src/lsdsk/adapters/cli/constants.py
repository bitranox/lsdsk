"""Shared CLI constants.

Centralizes configuration values used across CLI modules to ensure consistency.

Contents:
    * :data:`CLICK_CONTEXT_SETTINGS` - Shared Click settings for help display.
    * :data:`TREE_DENSITY_TOKENS` - The machine-readable density tokens.
    * :data:`FORMAT_OPTION` - The ``--format`` option every answering command takes.
    * :data:`REFUSED_FORMAT_OPTION` - The hidden twin, for a command with no machine form.
"""

from __future__ import annotations

from typing import Final

import rich_click as click

from lsdsk.domain.enums import OutputFormat, TreeDensity

from .typed_click import option

#: Shared Click context flags so help output stays consistent across commands.
CLICK_CONTEXT_SETTINGS: Final[dict[str, list[str]]] = {"help_option_names": ["-h", "--help"]}

#: The tokens the ``--tree-density`` choices register, in one list so the
#: option after a subcommand, the global one on the root group and the test
#: that pins the contract name the same vocabulary. In the enum's own order,
#: which runs from least detail to most, so the metavar teaches the same climb
#: the interactive ``d`` key walks. It was sorted alphabetically, which agreed
#: with that order only by accident of these three spellings.
TREE_DENSITY_TOKENS: Final[tuple[str, ...]] = tuple(str(density) for density in TreeDensity)

#: The ``--format`` option, declared once for every command that answers in both
#: forms. Four commands hand-rolled a byte-identical copy while ten reused the
#: original, which is the drift this module's own docstring exists to prevent:
#: the help text, the default and the case-sensitivity are a CONTRACT, and four
#: copies are four places for it to stop agreeing.
FORMAT_OPTION = option(
    "--format",
    "output_format",
    type=click.Choice(OutputFormat, case_sensitive=False),
    default=OutputFormat.HUMAN.value,
    show_default=True,
    help="Human-readable output, or JSON for another program to consume.",
)

__all__ = [
    "CLICK_CONTEXT_SETTINGS",
    "FORMAT_OPTION",
    "TREE_DENSITY_TOKENS",
]
