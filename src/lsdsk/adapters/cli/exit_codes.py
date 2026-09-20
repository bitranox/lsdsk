"""POSIX-conventional exit codes for CLI error paths.

Provides a single :class:`ExitCode` enum so every ``SystemExit`` raised by a
CLI command carries a meaningful, grep-friendly integer instead of a bare ``1``.

Signal codes 130 and 143 are informational constants only - the application never
raises ``SystemExit`` with those values; ``lib_cli_exit_tools`` translates the
signal. 141 is NOT one of them, however much it looks like one: nothing
translates a broken pipe, because click catches the ``EPIPE`` in its own
``main`` and calls ``sys.exit(1)`` before anything here runs, and 1 is this
tool's code for an actionable finding. So ``adapters/cli/safe_console`` raises
141 itself at the write that fails.

Contents:
    * :class:`ExitCode` - IntEnum of all exit codes used by this application.
    * :func:`outranks_a_departed_reader` - which codes stand when the pipe also broke
    * :func:`error_type_for` - the name the failure envelope gives a code
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final


class ExitCode(IntEnum):
    """POSIX-conventional exit codes for CLI error paths.

    Values follow sysexits.h and errno conventions where applicable:

    * 0-1: generic success / failure
    * 2: click's usage error, which this tool returns unchanged
    * 13: EACCES
    * 22: EINVAL
    * 78: EX_CONFIG (sysexits.h)
    * 128+N: signal N. 130 and 143 are informational, raised by nobody here;
      141 is raised by :mod:`lsdsk.adapters.cli.safe_console` when a reader
      closes the pipe, since click would otherwise report that as a 1.

    ``2`` is click's code for a usage error and is declared here rather than left
    out. lsdsk raises ``click.UsageError`` itself for a malformed ``--set``
    override and ``click.BadParameter`` for an unreadable octal mode, so a caller
    meets 2 from this tool's own code as well as from the parser; and the failure
    envelope names the code it leaves with, so a code with no name here would be
    a hole in that contract rather than a silence.

    What the name must not do is imply lsdsk chose 2 for one particular case. An
    earlier ``FILE_NOT_FOUND = 2`` read exactly that way and invited the wrong
    inference from a caller branching on it, while an unreadable ``--replay`` path
    is only one of the usage errors: an unknown option, an unknown command, a
    missing required argument and a bad ``--format`` choice all produce it too.
    ``USAGE_ERROR`` is what every one of them has in common.

    One code is deliberately absent. There is no timeout code: lsdsk issues no
    subprocesses and makes no network requests, so nothing it does can time out at
    the process level; an ioctl that stalls is bounded by the driver and recorded
    against the one drive rather than ending the run.

    Example:
        >>> ExitCode.SUCCESS
        <ExitCode.SUCCESS: 0>
        >>> int(ExitCode.CONFIG_ERROR)
        78
    """

    SUCCESS = 0
    GENERAL_ERROR = 1
    USAGE_ERROR = 2
    PERMISSION_DENIED = 13
    INVALID_ARGUMENT = 22
    CONFIG_ERROR = 78
    SIGNAL_INT = 130
    BROKEN_PIPE = 141
    SIGNAL_TERM = 143


#: The codes that say the run could not START, as opposed to what its output said.
#:
#: A usage error is in here whichever end produced it: click's parser refusing an
#: unknown option, or this tool's own ``click.UsageError`` for a malformed
#: ``--set``. Both mean the run never started.
_RUN_DID_NOT_START: Final[frozenset[int]] = frozenset(
    {
        int(ExitCode.USAGE_ERROR),
        int(ExitCode.PERMISSION_DENIED),
        int(ExitCode.INVALID_ARGUMENT),
        int(ExitCode.CONFIG_ERROR),
    }
)


def outranks_a_departed_reader(code: int) -> bool:
    """Whether `code` still stands once the reader has gone too.

    The contract (user, 2026-09-20). A code that says the run could not START is
    true whoever was reading, because there was never any output for that reader
    to lose: a mistyped ``--section`` is a mistyped ``--section`` whether it was
    piped into ``head`` or into a file. So 2, 13, 22 and 78 stand.

    A code that says what the output CONTAINED does not stand, because it was not
    delivered. ``lsdsk report | head -5`` on a failing machine has shown the
    reader five lines of a report; leaving 1 there would tell a monitoring check
    it had received a complete verdict. 141 says "what you asked for was not
    delivered", which is the only thing that is true of a truncated run, so 0 and
    1 both yield to it.

    This is asked of a code the run DECIDED. A reader that leaves while the
    command is still writing raises 141 at that write, before any command code
    exists, and there is nothing for this to rank.

    Args:
        code: The exit code the run decided on.

    Returns:
        Whether to keep it rather than answer with ``BROKEN_PIPE``.

    Example:
        >>> outranks_a_departed_reader(int(ExitCode.INVALID_ARGUMENT))
        True
        >>> outranks_a_departed_reader(int(ExitCode.GENERAL_ERROR))
        False
        >>> outranks_a_departed_reader(int(ExitCode.SUCCESS))
        False
    """
    return code in _RUN_DID_NOT_START


def error_type_for(code: int) -> str:
    """The name the failure envelope's ``error.type`` carries for `code`.

    One decider for the pair, so the name a caller reads and the code the process
    leaves can never disagree: the name IS the member's, and a new code cannot be
    added without its name arriving with it.

    A code no member holds is reported as the code itself rather than as the
    nearest member. That case is not reachable from this tool's own paths - every
    code it leaves is declared above - but a ``ClickException`` a library defines
    carries whatever ``exit_code`` it likes, and answering with a member's name
    there would tell a caller the two agree when they do not.

    Args:
        code: The exit code the run is leaving with.

    Returns:
        The member's name, or ``EXIT_<code>`` when no member holds it.

    Example:
        >>> error_type_for(78)
        'CONFIG_ERROR'
        >>> error_type_for(2)
        'USAGE_ERROR'
        >>> error_type_for(99)
        'EXIT_99'
    """
    try:
        return ExitCode(code).name
    except ValueError:
        return f"EXIT_{code}"


__all__ = ["ExitCode", "error_type_for", "outranks_a_departed_reader"]
