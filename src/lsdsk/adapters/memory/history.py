"""In-memory counter history, for tests and for the testing composition root.

Keeps samples in a process-local dictionary so the history rules and every
command that reads them can be exercised with no filesystem at all.

System Role:
    Adapter-layer test double for the history ports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.history import History

if TYPE_CHECKING:
    from pathlib import Path

_RECORDED: dict[str, History] = {}


def read_history_in_memory(*, hostname: str, path: Path | None = None) -> History:
    """Return what was written for this machine, or an empty history.

    Args:
        hostname: The machine being read.
        path: Ignored; kept so the signature matches the port.

    Returns:
        The stored history, empty when nothing was written.

    Example:
        >>> reset_history_in_memory()
        >>> read_history_in_memory(hostname="box").series
        ()
    """
    del path
    return _RECORDED.get(hostname, History(hostname=hostname))


def write_history_in_memory(history: History, *, path: Path | None = None) -> None:
    """Store a history for the machine it names.

    Args:
        history: What to store.
        path: Ignored; kept so the signature matches the port.

    Example:
        >>> reset_history_in_memory()
        >>> write_history_in_memory(History(hostname="box"))
        >>> read_history_in_memory(hostname="box").hostname
        'box'
    """
    del path
    _RECORDED[history.hostname] = history


def reset_history_in_memory() -> None:
    """Forget everything written, so one test cannot leak into the next.

    Example:
        >>> write_history_in_memory(History(hostname="box"))
        >>> reset_history_in_memory()
        >>> read_history_in_memory(hostname="box").series
        ()
    """
    _RECORDED.clear()


__all__ = [
    "read_history_in_memory",
    "reset_history_in_memory",
    "write_history_in_memory",
]
