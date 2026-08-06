"""An in-memory stand-in for printing the package's own metadata.

The production form writes a formatted table to stdout, which a test driving the
CLI has no reason to want and no way to inject around. Recording the calls
instead lets a test assert that the command reached it, and in what state, which
is what the two tests that used to patch ``__init__conf__`` were really after.

System Role:
    Adapter layer, in-memory test double.
"""

from __future__ import annotations

#: Every call made since the last reset, for a test to inspect.
CALLS: list[None] = []


def print_info_in_memory() -> None:
    """Record that the metadata would have been printed.

    Example:
        >>> reset_info_in_memory()
        >>> print_info_in_memory()
        >>> len(CALLS)
        1
    """
    CALLS.append(None)


def reset_info_in_memory() -> None:
    """Forget every recorded call.

    Example:
        >>> reset_info_in_memory()
        >>> CALLS
        []
    """
    CALLS.clear()


__all__ = ["CALLS", "print_info_in_memory", "reset_info_in_memory"]
