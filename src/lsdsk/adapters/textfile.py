"""Reading a JSON file that came from somewhere else, without trusting its size.

Two files reach this tool from outside it: a capture handed to ``--replay``, and
the counter history at ``--history-file``. Both are validated against a Pydantic
model, but validation happens *after* ``read_text`` has already materialised the
whole file, so a schema guard cannot defend against the file simply being huge.
Pointing ``--replay`` at a disk image rather than a capture is a typo, not an
attack, and the answer it deserves is an immediate "that is not a capture"
rather than a machine that swaps itself to death first.

System Role:
    Adapter-layer input boundary shared by the snapshot and history stores.

Contents:
    * :data:`MAX_INPUT_BYTES` - the ceiling both boundaries refuse above.
    * :func:`read_text_bounded` - read a file, or refuse it for its size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lsdsk.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

# Measured, not guessed: the largest capture from the real machines in
# tests/fixtures/hw is 148 KB for 19 drives, so a capture costs roughly 8 KB per
# drive. This leaves room for a machine with hundreds of drives and still
# refuses a mistyped path to a log or a disk image in constant time. A history
# store is smaller again, being bounded to MAX_SAMPLES_PER_DRIVE per drive.
MAX_INPUT_BYTES = 64 * 1024 * 1024


def read_text_bounded(path: Path, *, what: str, errors: str = "strict") -> str:
    """Read a UTF-8 text file, refusing one too large to be what it claims.

    The size is taken from the directory entry rather than by reading and
    counting, so an oversized file costs one ``stat`` and is never resident.

    Args:
        path: The file to read.
        what: What the file was expected to be, for the refusal message.
        errors: How to handle bytes that are not valid UTF-8. Strict by default,
            because a capture or a store that will not decode is a file this
            tool should refuse rather than silently misread. ``pci.ids`` is the
            exception: it is a system database carrying vendor names in mixed
            encodings, and a replacement character in one vendor string is
            better than losing the whole database.

    Returns:
        The file's contents.

    Raises:
        ConfigurationError: If the file cannot be read, or is larger than
            :data:`MAX_INPUT_BYTES`.

    Example:
        >>> from pathlib import Path
        >>> read_text_bounded(Path("/nonexistent"), what="a snapshot")
        Traceback (most recent call last):
        ...
        lsdsk.domain.errors.ConfigurationError: Could not read a snapshot at /nonexistent: ...
    """
    try:
        size = path.stat().st_size
    except OSError as error:
        message = f"Could not read {what} at {path}: {error}"
        raise ConfigurationError(message) from error

    if size > MAX_INPUT_BYTES:
        message = (
            f"{path} is {size / 1024 / 1024:.1f} MB, which is far larger than {what} ever is "
            f"(the limit is {MAX_INPUT_BYTES // 1024 // 1024} MB). Check the path."
        )
        raise ConfigurationError(message)

    try:
        return path.read_text(encoding="utf-8", errors=errors)
    except OSError as error:
        message = f"Could not read {what} at {path}: {error}"
        raise ConfigurationError(message) from error


__all__ = ["MAX_INPUT_BYTES", "read_text_bounded"]
