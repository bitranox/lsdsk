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
from ...domain.thresholds import DEFAULT_THRESHOLDS
from ..config.tunables import DEFAULT_PIPED_WIDTH, DisplaySettings, Tunables
from ..history.store import HistoryRead
from . import report, tables, theme, tree
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
    history: HistoryRead | None = None,
    tunables: Tunables | None = None,
) -> RenderableType:
    """Render every view of one machine, in one page.

    Args:
        inventory: The machine.
        findings: What the diagnosis produced.
        width: Terminal width, which decides how many columns each table fits.
        history: What the counter store gave up - the samples recorded on
            earlier runs, and the reason it could not be read when that is what
            happened. One value rather than two, because the trend section is
            drawn from both and a page given the samples without the reason
            reports a refused store as a machine nobody has ever recorded.
        tunables: What this run judges and lays out by, or the shipped values.
            Threaded through because these keys are only honoured by the
            sections below: without it `summary_limit` was read from
            configuration and passed nowhere at all, `wear_row_floor_percent`
            was honoured by `lsdsk trend` alone, and the wear thresholds
            reached the rules while the table beside them still judged at the
            shipped 80.

    Returns:
        The complete report.
    """
    host = inventory.hostname
    blank = Text("")
    read = history if history is not None else HistoryRead(History(hostname=host), writable=True)
    recorded = read.history
    settled = tunables if tunables is not None else Tunables(DEFAULT_THRESHOLDS, DisplaySettings())
    laid_out = settled.display
    sections: list[RenderableType] = [
        report.render_header(inventory),
        blank,
        report.render_verdict(findings, laid_out.summary_limit),
        blank,
        _heading(f"Topology on {host}"),
        tree.render_fabric(
            inventory,
            findings,
            width,
            tree.FabricView(density=laid_out.tree_density, expand_virtual=laid_out.expand_virtual),
            settled.thresholds,
        ),
        blank,
        tables.render_controllers(inventory, findings, width=width),
        blank,
        tables.render_disks(
            inventory, findings, width=width, expand_virtual=laid_out.expand_virtual, wwn_width=laid_out.wwn_width
        ),
        blank,
        tables.render_health(inventory, findings, width=width, history=recorded, thresholds=settled.thresholds),
        *_legend(tables.counter_legend(inventory, recorded)),
        blank,
        report.render_smart(inventory, width=width),
        blank,
        report.render_slots(inventory, width=width),
        blank,
        render_trend(
            inventory,
            recorded,
            width=width,
            wear_floor=laid_out.wear_row_floor_percent,
            store_refusal=read.refusal,
        ),
        blank,
        _heading(f"Findings on {host}"),
        report.render_findings(findings),
    ]
    return Group(*sections)


__all__ = ["DEFAULT_WIDTH", "render_full"]
