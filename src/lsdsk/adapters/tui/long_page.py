"""A page of long text, laid out once per width and drawn a line at a time.

The SMART and findings pages are one renderable each that grows with the
machine: a table per drive, a paragraph per finding. A ``Static`` inside a
``VerticalScroll`` has Textual render such a renderable IN FULL several times
over whenever the page is shown, resized or refilled - once to measure its
height at each width the layout tries, and again to draw it - and on a machine
of a thousand drives each full render is most of a second. Measured through the
pilot: five full renders and 3.4 s to open the SMART page of 1000 drives that
way, three on a resize.

This widget renders the renderable once for the width it is shown at, keeps
the lines, and hands the compositor only the ones in view. Showing the page
again at the same size renders nothing at all, and scrolling costs the height
of the window rather than the length of the page.

What a reader sees is the same as before, line for line: the lines are made the
way Textual makes a ``Static``'s, and the width they are made at is the one a
``VerticalScroll`` would have given a ``Static`` - the whole width when the page
fits in the window, less the vertical scrollbar when it does not.

System Role:
    Adapter. A Textual widget the interactive view composes; it knows nothing
    of drives or findings.
"""

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING

from rich.segment import Segment
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rich.console import RenderableType
    from textual import getters
    from textual.app import App
    from textual.geometry import Region

__all__ = ["LongPage"]


class LongPage(ScrollView, can_focus=True):
    """A scrolling page of long text, rendered once per width and drawn a line at a time.

    Example:
        >>> page = LongPage(id="smart-body")
        >>> page.line_count
        0
    """

    if TYPE_CHECKING:
        # Textual declares ``app`` as an unparameterised ``App``, which strict
        # typing reads as partially unknown. This page asks it only for the
        # console, which every app has, whatever it returns from ``run``.
        app = getters.app(App[object])

    DEFAULT_CSS = """
    LongPage {
        width: 1fr;
        height: 1fr;
        overflow-x: hidden;
        overflow-y: auto;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 - Textual's own name for a widget's id
        """Build an empty page.

        Args:
            id: The widget's id.
        """
        super().__init__(id=id)
        self._renderable: RenderableType = ""
        self._lines: list[Strip] = []
        #: The size the lines in hand were laid out for, so a page shown again
        #: at that size renders nothing. ``None`` until the first layout, and
        #: again after new content arrives.
        self._laid_out_for: Size | None = None
        #: The width the lines in hand were rendered at, which is all that
        #: decides them.
        self._rendered_at: int | None = None

    @property
    def line_count(self) -> int:
        """How many lines the page runs to at the width it was last laid out at."""
        return len(self._lines)

    def update(self, renderable: RenderableType) -> None:
        """Replace what the page shows.

        Laid out when the page is next drawn, so a page on a tab nobody has
        opened costs nothing: rendering it there would be the cost this widget
        exists to avoid.

        Args:
            renderable: What to show.
        """
        self._renderable = renderable
        self._laid_out_for = None
        self._rendered_at = None
        self.refresh()

    def render_lines(self, crop: Region) -> list[Strip]:
        """Draw the window, laying the page out first if its size changed since it was last laid out.

        Laid out here rather than on a resize event, because Textual does not
        send one to a widget that was resized while its tab was hidden and is
        then shown again: measured, the page kept the lines of its old width and
        the compositor cropped them.

        Args:
            crop: The part of the widget to draw.

        Returns:
            The lines of that part.
        """
        self._lay_out()
        return super().render_lines(crop)

    def _lay_out(self) -> None:
        """Render the page for the width a ``VerticalScroll`` would have given it, once per width."""
        size = self.size
        if size.width <= 0 or size.height <= 0 or size == self._laid_out_for:
            return
        self._laid_out_for = size
        width = size.width
        # The scrollbar takes its columns only when the page does not fit at the
        # whole width, which is how a VerticalScroll decides it: laid out whole
        # first, and narrowed once it overflows. Asking whether it fits stops
        # after one line more than the window, so a long page pays for a
        # window's worth of that question rather than for a second full render.
        if self._line_total(self._render_lines(width), stop_after=size.height + 1) > size.height:
            width = max(width - self.styles.scrollbar_size_vertical, 1)
        # Only the width decides the lines. The window's height moves on every
        # page switch - the record panel below it takes what its record needs -
        # and re-rendering the page for that would be the full render this
        # widget exists to avoid.
        if width == self._rendered_at:
            return
        self._rendered_at = width
        self._lines = [Strip(line) for line in self._render_lines(width)]
        self.virtual_size = Size(width, len(self._lines))
        self.refresh()

    def _render_lines(self, width: int) -> Iterable[list[Segment]]:
        """Render the page at one width into lines, lazily, as Textual renders a ``Static``.

        Args:
            width: The width to lay the page out at.

        Returns:
            The lines, each a list of segments, produced as they are asked for.
        """
        console = self.app.console
        options = self.app.console_options.update(highlight=False, width=width)
        renderable = self.post_render(self._renderable, self.visual_style.rich_style)
        segments = console.render(renderable, options.update_width(width))
        return Segment.split_and_crop_lines(segments, width, include_new_lines=False, pad=False)

    @staticmethod
    def _line_total(lines: Iterable[list[Segment]], *, stop_after: int) -> int:
        """Count lines, but never past ``stop_after``."""
        return sum(1 for _ in islice(lines, stop_after))

    def render_line(self, y: int) -> Strip:
        """Draw one line of the window.

        Args:
            y: The line within the window, counted from its top.

        Returns:
            The page's line at that height, or a blank one past its end.
        """
        index = y + self.scroll_offset.y
        if 0 <= index < len(self._lines):
            return self._lines[index]
        return Strip.blank(self.size.width, self.visual_style.rich_style)
