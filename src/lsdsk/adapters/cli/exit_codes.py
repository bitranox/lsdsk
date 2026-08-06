"""POSIX-conventional exit codes for CLI error paths.

Provides a single :class:`ExitCode` enum so every ``SystemExit`` raised by a
CLI command carries a meaningful, grep-friendly integer instead of a bare ``1``.

Signal codes (130, 141, 143) are informational constants only — the application
never raises ``SystemExit`` with these values; ``lib_cli_exit_tools`` handles
signal-to-exit-code translation automatically.

Contents:
    * :class:`ExitCode` — IntEnum of all exit codes used by this application.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """POSIX-conventional exit codes for CLI error paths.

    Values follow sysexits.h and errno conventions where applicable:

    * 0-1: generic success / failure
    * 13: EACCES
    * 22: EINVAL
    * 78: EX_CONFIG (sysexits.h)
    * 128+N: signal N (informational only)

    Two codes a caller will see are deliberately absent, because this enum is
    the codes lsdsk RAISES and neither of those is one:

    * ``2`` comes from Click and means a usage error, of which an unreadable
      ``--replay`` path is only one case: an unknown option, an unknown command,
      a missing required argument and a bad ``--format`` choice all produce it
      too. Declaring a ``FILE_NOT_FOUND = 2`` here read as though lsdsk chose it
      for the missing-file case specifically, and invited exactly the wrong
      inference from a caller branching on it.
    * There is no timeout code. lsdsk issues no subprocesses and makes no
      network requests, so nothing it does can time out at the process level; an
      ioctl that stalls is bounded by the driver and recorded against the one
      drive rather than ending the run.

    Example:
        >>> ExitCode.SUCCESS
        <ExitCode.SUCCESS: 0>
        >>> int(ExitCode.CONFIG_ERROR)
        78
    """

    SUCCESS = 0
    GENERAL_ERROR = 1
    PERMISSION_DENIED = 13
    INVALID_ARGUMENT = 22
    CONFIG_ERROR = 78
    SIGNAL_INT = 130
    BROKEN_PIPE = 141
    SIGNAL_TERM = 143


__all__ = ["ExitCode"]
