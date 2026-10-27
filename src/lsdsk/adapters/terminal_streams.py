"""Which standard streams the interactive view needs, and which of them is not a terminal.

System Role:
    Adapter layer, standard library only. It sits outside ``adapters/cli`` because
    the entry point asks it before the CLI is imported: the startup notice has to
    decide whether to show anything while that import is still to come, and
    ``lsdsk.adapters.cli`` is the whole command line. The router asks the same
    functions, so the two decisions cannot disagree.

Contents:
    * :func:`streams_that_are_not_terminals` - name each needed stream that is not a terminal.
    * :func:`names_of_the_streams_the_view_needs` - name every needed stream on this platform.
    * :func:`drawing_stream` - the stream Textual draws on, where it is not stdout.
"""

from __future__ import annotations

import sys
from typing import TextIO


def streams_that_are_not_terminals() -> list[str]:
    """Name each standard stream the interactive view needs that is not a terminal.

    Textual reads key events from stdin, so ``lsdsk < /dev/null`` would open a
    view nobody can quit, and stdout is asked because a redirected stdout means
    the reader wants the printed page. The third is the stream the view DRAWS
    on, which Textual chooses by platform exactly as :func:`drawing_stream`
    does: its POSIX driver draws on ``sys.__stderr__``, so ``lsdsk 2>err.log``
    at a terminal opened a view nobody could see - measured, the screen stayed
    on the shell's text, 161,934 bytes of escape sequences went into the file,
    and the run ended only on a blind ``q``; with ``2>&-`` there was nothing to
    draw on at all. Its Windows driver draws on stdout, which is already asked,
    so a Windows ``lsdsk 2>log`` keeps its view.

    Returns:
        ``"standard input"``, ``"standard output"`` and, where the view draws
        on it, ``"standard error"``, each included when that stream is missing
        or not a terminal. Empty when somebody is sitting at every one of them.
    """
    return [name for name, stream in _streams_the_view_needs() if not (stream is not None and stream.isatty())]


def names_of_the_streams_the_view_needs() -> list[str]:
    """Name every standard stream the interactive view needs on this platform.

    Returns:
        ``"standard input"`` and ``"standard output"``, and ``"standard error"``
        where the view draws on it - so a refusal names the requirement that
        holds here rather than another platform's.
    """
    return [name for name, _ in _streams_the_view_needs()]


def _streams_the_view_needs() -> list[tuple[str, TextIO | None]]:
    """Pair each stream the interactive view needs with its name.

    Returns:
        stdin and stdout, then the drawing stream where it is a third one.
    """
    streams: list[tuple[str, TextIO | None]] = [("standard input", sys.stdin), ("standard output", sys.stdout)]
    drawing = drawing_stream()
    if drawing is not None:
        streams.append(drawing)
    return streams


def drawing_stream() -> tuple[str, TextIO | None] | None:
    """Name the stream Textual draws on when it is not one already asked.

    Textual picks its driver from ``sys.platform == "win32"`` (``App.get_driver_class``):
    the Windows driver writes to ``sys.__stdout__``, the POSIX one to
    ``sys.__stderr__``. It is the ORIGINAL stderr because that is the object the
    driver holds - ``sys.stderr`` may be a stand-in this tool installed.

    Returns:
        ``("standard error", sys.__stderr__)`` off Windows; None on Windows,
        where the view draws on the stdout the caller already asks about.
    """
    if sys.platform == "win32":
        return None
    return ("standard error", sys.__stderr__)


__all__ = ["drawing_stream", "names_of_the_streams_the_view_needs", "streams_that_are_not_terminals"]
