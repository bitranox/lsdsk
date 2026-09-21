"""The interactive view's palette, and the layer that puts it on the screen.

The printed palette has to survive a black console and a white one at once,
which puts a saturated hue's ceiling at about 4.2:1 - measured, and the reason
``theme.py`` cannot simply be brightened when somebody finds it dim on their
terminal.  The interactive view paints its own background, so it can carry
colours the printed one cannot, and it is judged against that background
instead.

**How a colour gets swapped, and the one role that cannot be.**  Every style the
render layer emits names its colour in hex, so the layer here maps a printed hue
to this palette's by the hex itself: unambiguous, and it needs to know nothing
about which role produced it.  A role with no hue in print is the exception,
because there is nothing to key on - ``header``, ``note`` and ``identifier`` are
all the bare string ``bold`` there, and mapping by string would paint every
device path and every note the header's gold.  So the header's hue is passed to
the two places that DRAW a header instead, and the third place a header appears
(a ``DataTable``'s own column row) is Textual's to draw and takes it from the
stylesheet.

System Role:
    Adapter layer, presentation.  Consumes the render layer's vocabulary;
    decides nothing about what anything means.
"""

from __future__ import annotations

from dataclasses import fields
from functools import lru_cache
from typing import TYPE_CHECKING, Final

from rich.style import Style

from ..render import theme

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
    from rich.text import Text

#: What the interactive view draws in: the printed palette's roles, lifted for
#: one dark background rather than four backgrounds at once, plus a header hue
#: the printed view has no room for.  Every value here is held above the
#: body-text contrast floor by ``test_tui_palette.py``, measured against the
#: backgrounds the app actually paints rather than the ones it declares.
PALETTE: Final = theme.Palette(
    critical="#FF6B6B",
    warning="#E0A33E",
    at_capability="#4FC98A",
    hint="#5BC0E8",
    opportunity="#F08A4B",
    unknown="#A8B2C4",
    header="#E3B341",
)


# Bounded rather than @cache, for the reason tree._header_cells is: the key is
# a caller-supplied palette, so nothing in the type stops the cache growing.
# One palette ships, and the tests build a second.
@lru_cache(maxsize=4)
def _pairs(palette: theme.Palette) -> tuple[tuple[str, str], ...]:
    """Every printed hue and its counterpart, computed once per palette.

    The pairs are a tuple rather than the mapping itself, because a cached
    mapping is a mutable object every caller shares: one caller that edited it
    would change what every other caller recolours with, silently and for the
    rest of the run.
    """
    return tuple(
        (printed, interactive)
        for printed, interactive in (
            (str(getattr(theme.PRINTED, field.name)), str(getattr(palette, field.name)))
            for field in fields(theme.Palette)
        )
        if printed
    )


def hue_map(palette: theme.Palette = PALETTE) -> dict[str, str]:
    """Each printed colour, and what this palette draws it as.

    Built from the pair of palettes rather than written out, so a role added to
    :class:`theme.Palette` is mapped without anyone remembering to add it here -
    which is the whole reason the roles are named in one type.

    Args:
        palette: The palette to map onto.

    Returns:
        Printed hex to interactive hex, for every role that has a printed hue.

    Example:
        >>> hue_map()[theme.STYLE_CRITICAL_COLOUR] == PALETTE.critical
        True
        >>> theme.PRINTED.header in hue_map()
        False
    """
    return dict(_pairs(palette))


def restyle(style: str, palette: theme.Palette = PALETTE) -> str:
    """One style string, with every printed hue replaced by this palette's.

    Args:
        style: A Rich style string as the render layer wrote it.
        palette: The palette to draw in.

    Returns:
        The same style, in this palette's colours.

    Example:
        >>> restyle(theme.STYLE_FAILING) == f"bold {PALETTE.critical}"
        True
        >>> restyle("bold")
        'bold'
        >>> restyle("")
        ''
    """
    for printed, interactive in _pairs(palette):
        style = style.replace(printed, interactive)
    return style


def retext(text: Text, palette: theme.Palette = PALETTE) -> Text:
    """One piece of Rich text, span by span, in this palette's colours.

    The spans are rewritten rather than the text re-rendered, so the words, the
    widths and the wrapping are untouched: a line that fitted still fits.

    Args:
        text: The text as the render layer built it.
        palette: The palette to draw in.

    Returns:
        A copy in this palette's colours.

    Example:
        >>> from rich.text import Text
        >>> line = Text("6G", style=theme.STYLE_AT_CAPABILITY)
        >>> str(retext(line).style) == PALETTE.at_capability
        True
    """
    out = text.copy()
    if isinstance(out.style, str):
        out.style = restyle(out.style, palette)
    out.spans = [
        span._replace(style=restyle(span.style, palette)) if isinstance(span.style, str) else span for span in out.spans
    ]
    return out


class Recoloured:
    """Whatever it wraps, drawn in the interactive palette.

    For a renderable built of nested tables and text - the detail card - where
    rewriting the pieces would mean reaching into every builder that made one.
    The swap happens on the finished SEGMENTS instead, after Rich has resolved
    every style, so a colour is mapped wherever in the structure it came from.

    A segment carrying no colour is left exactly as it is, which is what keeps
    an identifier bold and hueless rather than painting it the header's gold.
    """

    def __init__(self, renderable: RenderableType, palette: theme.Palette = PALETTE) -> None:
        """Wrap a renderable.

        Args:
            renderable: What to draw.
            palette: The palette to draw it in.
        """
        self.renderable = renderable
        self.palette = palette

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Render the wrapped renderable, mapping each segment's colour."""
        mapping = {printed.upper(): interactive for printed, interactive in _pairs(self.palette)}
        for segment in console.render(self.renderable, options):
            style = segment.style
            colour = None if style is None or style.color is None else style.color.triplet
            interactive = None if colour is None else mapping.get(colour.hex.upper())
            if interactive is None or style is None:
                yield segment
                continue
            yield segment.__class__(segment.text, style + Style(color=interactive), segment.control)


__all__ = ["PALETTE", "Recoloured", "hue_map", "restyle", "retext"]
