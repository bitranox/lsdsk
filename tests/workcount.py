"""Count the work an action does, for the scaling arms that must not race a clock.

A scaling arm doubles its input and requires the work to roughly double with
it. Timing that work fails on a loaded runner: on a shared host at load 27 to
42 a fixed build measured the defect's own growth in six full runs while
passing alone every time, and a ratio of two 30 ms figures cannot tell a slow
neighbour from a quadratic loop. A count is the same on every run, every
platform and every load.

What is counted is calls made, Python and C, plus Python lines executed.
Calls alone miss a rescan written as a plain ``for key, value in
index.items(): if key == ...`` inside the function that looks one entry up:
that makes one call per LOOKUP rather than one per entry, so a quadratic
rewrite of two builder lookups left a call-counting arm green. Every pass of
such a loop executes a line, which a trace hook sees and a profile hook
cannot.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    # A stub-only alias: typeshed spells the recursive tracer type once, and
    # sys.settrace is declared against it.
    from _typeshed import TraceFunction

T = TypeVar("T")


def work_to_run(action: Callable[[], T]) -> tuple[T, int]:
    """Run ``action`` and count the calls it made and the lines it executed.

    Whatever profile and trace hooks were installed before are restored
    afterwards, so a coverage tracer running the suite is suspended for the
    action rather than lost.

    Args:
        action: What to run, taking nothing.

    Returns:
        What the action returned, and the calls plus lines it took.
    """
    work = 0

    def calls(_frame: FrameType, event: str, _arg: object) -> None:
        nonlocal work
        if event in {"call", "c_call"}:
            work += 1

    def lines(_frame: FrameType, event: str, _arg: object) -> TraceFunction:
        nonlocal work
        if event == "line":
            work += 1
        return lines

    previous_profile = sys.getprofile()
    previous_trace = sys.gettrace()
    sys.setprofile(calls)
    sys.settrace(lines)
    try:
        result = action()
    finally:
        sys.settrace(previous_trace)
        sys.setprofile(previous_profile)
    return result, work
