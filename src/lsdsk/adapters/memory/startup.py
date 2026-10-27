"""In-memory startup notice: draws nothing, writes each step into a journal.

The testing container answers the startup port with this, and a test that
passes its own journal reads the steps in order beside the other ports' steps.
"""

from __future__ import annotations


class StartupNoticeInMemory:
    """Records ``start``, ``pause``, ``resume`` and ``stop`` in the order they were taken.

    Example:
        >>> notice = StartupNoticeInMemory()
        >>> notice.start(); notice.pause(); notice.stop()
        >>> notice.journal
        ['start', 'pause', 'stop']
    """

    def __init__(self, journal: list[str] | None = None) -> None:
        """Write into ``journal``, or into a fresh list when none is given.

        Args:
            journal: The list each step is appended to.
        """
        self.journal: list[str] = journal if journal is not None else []

    def start(self) -> None:
        """Record the start."""
        self.journal.append("start")

    def pause(self) -> None:
        """Record a pause."""
        self.journal.append("pause")

    def resume(self) -> None:
        """Record a resume."""
        self.journal.append("resume")

    def stop(self) -> None:
        """Record a stop."""
        self.journal.append("stop")


__all__ = ["StartupNoticeInMemory"]
