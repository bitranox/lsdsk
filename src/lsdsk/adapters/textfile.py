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
    * :func:`read_json_bounded` - the same read, parsed, refusing a repeated key.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from lsdsk.domain.errors import ConfigurationError, MissingFileError

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

    Bounded twice, because neither check alone is enough. The directory entry
    is consulted first, so a mistyped path to a disk image is refused for one
    ``stat`` and is never resident. That entry cannot be trusted to describe
    the content, though: a character device, a FIFO and nearly everything under
    ``/proc`` report a size of 0 whatever they go on to deliver, so the read
    itself also stops one byte past the ceiling and refuses there. A stream
    under the ceiling still loads, which is what keeps
    ``--replay <(ssh host lsdsk snapshot -o -)`` working.

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
        MissingFileError: If the file is not there. A subclass of the below, so
            only a caller that has to tell an absent file from an unreadable one
            asks for it.
        ConfigurationError: If the file cannot be read, or is larger than
            :data:`MAX_INPUT_BYTES`.

    Example:
        A directory this example owns, because a fixed path is not absent
        everywhere: ``/nonexistent`` is the ``nobody`` account's home on a
        Debian or Ubuntu box, mode 0700, so the stat refuses rather than
        answering no and the refusal is a different one.

        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     read_text_bounded(Path(directory) / "absent.json", what="a snapshot")
        Traceback (most recent call last):
        ...
        lsdsk.domain.errors.MissingFileError: Could not read a snapshot at ...
    """
    try:
        size = path.stat().st_size
    except OSError as error:
        raise _unreadable(path, what, error) from error

    if size > MAX_INPUT_BYTES:
        message = (
            f"{path} is {size / 1024 / 1024:.1f} MB, which is far larger than {what} ever is "
            f"(the limit is {MAX_INPUT_BYTES // 1024 // 1024} MB). Check the path."
        )
        raise ConfigurationError(message)

    try:
        with path.open("rb") as handle:
            # One byte past the ceiling: enough to know the file is over it
            # without ever holding more than that, which is the same shape
            # ``read_bundled_pci_ids`` uses for the decompressed database.
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as error:
        raise _unreadable(path, what, error) from error

    if len(raw) > MAX_INPUT_BYTES:
        # Deliberately no figure: the read stopped early, so the size is not
        # something this branch measured and must not be stated as if it were.
        message = (
            f"{path} is larger than {what} ever is (the limit is {MAX_INPUT_BYTES // 1024 // 1024} MB). Check the path."
        )
        raise ConfigurationError(message)

    return raw.decode("utf-8", errors=errors)


def read_json_bounded(path: Path, *, what: str) -> Any:
    """Read a bounded file and parse it as JSON, refusing one that repeats a key.

    JSON says nothing about an object naming one key twice, and CPython
    resolves it last-writer-wins with no signal at all. Both files that reach
    this tool from outside it are keyed maps read back by key, so a repeat is
    a value silently replaced by another: measured on a capture, a graphics
    device repeating a SAS HBA's address left the machine reporting four
    controllers where it has five, moved ten drives to "not attached to a
    known controller" and grew a root complex that does not exist, and a
    repeated `block` section emptied the machine and turned `lsdsk health`
    from exit 1 into exit 0 on a drive carrying 99,345 CRC errors.

    Refused rather than resolved, because nothing here can tell which of the
    two values was meant. Neither writer can produce one - a Python dict has
    no repeated key to dump - so a file carrying one was not written by
    lsdsk, and picking either value would be the tool reporting something it
    did not read.

    Args:
        path: The file to read.
        what: What the file was expected to be, for the refusal message.

    Returns:
        Whatever the document holds, untyped as JSON always is; the caller's
        model is what gives it a shape.

    Raises:
        MissingFileError: If the file is not there.
        ConfigurationError: If the file cannot be read or is too large.
        ValueError: If any object in it names one key twice. Left as the
            plain error `json.loads` already raises for a malformed number,
            so both callers' existing handlers turn it into the same refusal
            every other unreadable document gets.

    Example:
        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     store = Path(directory) / 'twice.json'
        ...     _ = store.write_text('{"a": 1, "a": 2}', encoding='utf-8')
        ...     read_json_bounded(store, what='a snapshot')
        Traceback (most recent call last):
        ...
        ValueError: the key 'a' is given twice in one object
    """
    return json.loads(read_text_bounded(path, what=what), object_pairs_hook=_object_without_repeated_keys)


def _object_without_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object, refusing it if a key is given more than once.

    Called for every object in the document, nested ones included, which is
    where the repeat can hide: the sections a capture is read by are one
    level down and the samples in a history store are three.
    """
    entry: dict[str, Any] = {}
    for key, value in pairs:
        if key in entry:
            message = f"the key {key!r} is given twice in one object"
            raise ValueError(message)
        entry[key] = value
    return entry


def _unreadable(path: Path, what: str, error: OSError) -> ConfigurationError:
    """The refusal a failed read deserves, typed by WHY it failed.

    A file that is not there is a different answer from one that is there and
    will not open, and the reader is the only place that knows which happened:
    by the time a caller asks ``Path.exists`` the OSError has been swallowed and
    both read as absent. Both remain configuration errors, so a caller that
    wants neither distinction is unaffected.

    ``NotADirectoryError`` counts as not there, and provably so: a path component
    is a regular file, so nothing can exist below it, now or ever. Read as merely
    unreadable it made the history store report that it had left an existing store
    alone - a sentence about a file that cannot exist - and a timer pointed at such
    a path then recorded nothing for as long as it ran, with exit 0 and a reason
    that was false. Classified here rather than at that caller, because
    ``Path.exists`` cannot tell an absent file from one this process may not look
    at, which is the distinction this function exists to keep.
    """
    message = f"Could not read {what} at {path}: {error}"
    if isinstance(error, FileNotFoundError | NotADirectoryError):
        return MissingFileError(message)
    return ConfigurationError(message)


__all__ = ["MAX_INPUT_BYTES", "read_json_bounded", "read_text_bounded"]
