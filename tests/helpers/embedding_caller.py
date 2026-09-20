"""Call ``main()`` in-process with a broken stdout and report what fd 1 became.

Shipped as a FILE and run as one, rather than driven with ``python -c``: the
probe has to break its own fd 1 before importing anything, and its verdict has
to travel on a channel this program shares with nothing else - which neither
stdout (broken here on purpose) nor stderr (lsdsk logs there) is.

The pipe's read end is closed first, so every write to fd 1 raises the same
``BrokenPipeError`` a departed reader produces. What fd 1 NAMES is read with
``os.fstat`` rather than by writing to it, because a write to the null device
succeeds silently - which is the whole defect under test.

Usage:
    python embedding_caller.py <verdict-path> <lsdsk argument>...
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def break_stdout() -> None:
    """Point fd 1 at a pipe whose reader has already gone."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    os.dup2(write_end, 1)
    os.close(write_end)


def identity(descriptor: int) -> list[int]:
    """Return what file `descriptor` names, as the pair a ``dup2`` elsewhere would change.

    Args:
        descriptor: The file descriptor to identify.

    Returns:
        Its device and inode numbers.
    """
    status = os.fstat(descriptor)
    return [status.st_dev, status.st_ino]


def main() -> int:
    """Run one lsdsk command through ``main()`` and record fd 1 before and after.

    Returns:
        Zero, so a non-zero exit from this program means the probe itself broke.
    """
    verdict = Path(sys.argv[1])
    argv = sys.argv[2:]

    break_stdout()
    before = identity(1)

    from lsdsk.adapters.cli.main import main as cli_main
    from lsdsk.composition import build_production

    code = cli_main(argv, services_factory=build_production)
    after = identity(1)

    verdict.write_text(json.dumps({"code": code, "before": before, "after": after}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
