"""The whole machine on one page.

Every other view answers one question. This one answers "what is this machine",
which is what you want when you have just been handed a server, when you are
writing a ticket, or when you are pasting a report to somebody who cannot log
in.

Order is deliberate: what is wrong comes first, then how the machine is put
together, then the detail behind both. Somebody who stops reading after the
first screen has still seen everything actionable.

Lives in its own module because it composes both other renderers, and
:mod:`.tables` already imports :mod:`.report`; putting it in either would make
that import circular.

System Role:
    Adapter layer, presentation.  Composes existing renderers and adds nothing
    of its own, so a section can never disagree with the view it came from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from ...domain.history import History
from ..config.tunables import DEFAULT_PIPED_WIDTH, DisplaySettings
from . import report, tables, theme
from .trend import render_trend

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType

    from ...domain.models import Finding, Inventory

# Width assumed when the output is not going to a terminal.
DEFAULT_WIDTH = DEFAULT_PIPED_WIDTH


def _heading(title: str) -> Text:
    """Render a section heading in the same style the tables title themselves.

    The tables carry their own titles, so a section that has none needs one that
    looks identical or the page reads as though it changed format halfway down.
    """
    return Text(title, style="bold")


def _legend(text: str) -> tuple[RenderableType, ...]:
    """The counter legend as a section, or nothing when there is none to give."""
    return (Text(text, style=theme.STYLE_UNKNOWN),) if text else ()


def render_full(
    inventory: Inventory,
    findings: Sequence[Finding],
    width: int = DEFAULT_WIDTH,
    history: History | None = None,
    display: DisplaySettings | None = None,
) -> RenderableType:
    """Render every view of one machine, in one page.

    Args:
        inventory: The machine.
        findings: What the diagnosis produced.
        width: Terminal width, which decides how many columns each table fits.
        history: Counter samples recorded on earlier runs. Without them the
            trend section explains that there is nothing to compare yet.
        display: Layout values, or the shipped ones. Threaded through because
            two of its keys are only honoured by the sections below: without it
            `summary_limit` was read from configuration and passed nowhere at
            all, and `wear_row_floor_percent` was honoured by `lsdsk trend`
            alone, so the same setting changed one view and silently not the
            others.

    Returns:
        The complete report.
    """
    host = inventory.hostname
    blank = Text("")
    history = history or History(hostname=host)
    laid_out = display if display is not None else DisplaySettings()
    sections: list[RenderableType] = [
        report.render_header(inventory),
        blank,
        report.render_verdict(findings, laid_out.summary_limit),
        blank,
        _heading(f"Topology on {host}"),
        report.render_tree(inventory, findings, width=width, expand_virtual=laid_out.expand_virtual),
        blank,
        tables.render_controllers(inventory, findings, width=width),
        blank,
        tables.render_disks(inventory, findings, width=width, expand_virtual=laid_out.expand_virtual),
        blank,
        tables.render_health(inventory, findings, width=width, history=history),
        *_legend(tables.counter_legend(inventory, history)),
        blank,
        report.render_smart(inventory, width=width),
        blank,
        report.render_slots(inventory, width=width),
        blank,
        render_trend(inventory, history, width=width, wear_floor=laid_out.wear_row_floor_percent),
        blank,
        _heading(f"Findings on {host}"),
        report.render_findings(findings),
    ]
    return Group(*sections)


__all__ = ["DEFAULT_WIDTH", "render_full"]
