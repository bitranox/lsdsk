"""Reading a JSON file that came from somewhere else, without trusting its size.

Two files reach this tool from outside it: a capture handed to ``--replay``, and
the counter history at ``--history-file``. Both are validated against a Pydantic
model, but validation happens *after* ``read_text`` has already materialised the
whole file, so a schema guard cannot defend against the file simply being huge.
Pointing ``--replay`` at a disk image rather than a capture is a typo, not an
attack, and the answer it deserves is an immediate "that is not a capture".

What the ceiling bounds is the FILE. The footprint is decided by how many
ENTRIES the file holds, which the models bound separately - see
:data:`MAX_INPUT_BYTES` for the measurement and
:data:`lsdsk.adapters.validation.MAX_ENTRIES` for that second bound.

System Role:
    Adapter-layer input boundary shared by the snapshot and history stores.

Contents:
    * :data:`MAX_INPUT_BYTES` - the ceiling both boundaries refuse above.
    * :func:`read_text_bounded` - read a file, or refuse it for its size.
    * :func:`read_json_bounded` - the same read, parsed, refusing a repeated key.
    * :func:`fits_a_bounded_read` - whether a file this tool is about to write
      is one it could read back.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from lsdsk.domain.errors import ConfigurationError, MissingFileError
from lsdsk.domain.text import visible_text

if TYPE_CHECKING:
    from pathlib import Path

# Longest prefix first: a UTF-32LE BOM (FF FE 00 00) starts with the UTF-16LE
# BOM (FF FE), so checking the two-byte marks first would misread a UTF-32
# file as UTF-16 followed by two NUL bytes of "content". The codec named is
# the family's UNSUFFIXED one (``utf-16``, ``utf-32``), never ``-le``/``-be``:
# those keep the BOM as a literal U+FEFF character in the decoded text, which
# is exactly what made json.loads refuse a BOM-carrying capture with
# "Unexpected UTF-8 BOM" even once the bytes decoded without error. The
# unsuffixed codec both picks the byte order FROM the mark this loop already
# matched and strips it.
_BOM_CODECS: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\xfe\xff", "utf-16"),
    (b"\xff\xfe", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)

# Measured, not guessed: the largest capture from the real machines in
# tests/fixtures/hw is 148 KB for 19 drives, so a capture costs roughly 8 KB per
# drive. This leaves room for a machine with hundreds of drives and still
# refuses a mistyped path to a log or a disk image in constant time. Both files
# are also WRITTEN by this tool, and each writer refuses a file past this ceiling
# (fits_a_bounded_read), so nothing it writes is one it cannot read back.
#
# It bounds the FILE and not the footprint. A parsed document is several Python
# objects and then a model per entry, so the cost is linear in ENTRY COUNT with a
# constant of a few hundred, and an entry can be as short as a dozen bytes:
# 200,000 empty PCI entries in a 4 MB capture cost `findings` 7 s and 703 MB,
# and 4.5 million in 58.5 MB cost 156 s and 14.4 GB. So every collection either
# model declares is ALSO bounded in entries, before any entry is validated, by
# lsdsk.adapters.validation.MAX_ENTRIES; that constant carries the figure and
# why no real machine approaches it. The entry bound does not make parsing
# cheap, though: it is checked on the parsed document, so a file inside this
# ceiling is always parsed WHOLE before any count is refused. Measured at the
# ceiling, a 67 MB capture of 5.7 million `"k":{}` entries cost 7 to 8 s and
# 1.8 GB of peak memory before it was refused at 78. That is the worst a file
# under the ceiling costs - seconds and a couple of gigabytes, not the minutes
# and tens of gigabytes an unbounded count cost - and a document past either
# bound is still refused in this tool's own words.
MAX_INPUT_BYTES = 64 * 1024 * 1024


def fits_a_bounded_read(body: str) -> bool:
    """Whether a file holding ``body`` would be accepted by the bounded read.

    Both files this tool reads back are files it also WRITES, and a writer with
    no bound of its own can produce one past :data:`MAX_INPUT_BYTES`: its own
    reader then refuses it, so a history store stops growing for good and a
    snapshot can never be replayed. The comparison is the reader's own, on the
    same constant and on the UTF-8 bytes the writers produce, so writer and
    reader cannot disagree about the boundary.

    Args:
        body: The whole file, as it would be written.

    Returns:
        Whether the read would accept it.

    Example:
        >>> fits_a_bounded_read("{}")
        True
        >>> fits_a_bounded_read("x" * (MAX_INPUT_BYTES + 1))
        False
    """
    return len(body.encode("utf-8")) <= MAX_INPUT_BYTES


def read_text_bounded(path: Path, *, what: str, errors: str = "strict") -> str:
    """Read a UTF-8 text file, refusing one too large to be what it claims.

    Bounded twice, because neither check alone is enough. The directory entry
    is consulted first, so a mistyped path to a disk image is refused for one
    ``stat`` and is never resident. That entry cannot be trusted to describe
    the content, though: a character device, a FIFO and nearly everything under
    ``/proc`` report a size of 0 whatever they go on to deliver, so the read
    itself also stops one byte past the ceiling and refuses there. A stream
    under the ceiling still loads, which is what keeps
    ``--replay <(ssh host cat capture.json)`` working.

    A file UNDER the ceiling is read whole, and what it costs once parsed is
    :data:`MAX_INPUT_BYTES`'s to say: the ceiling bounds the file, not the
    footprint.

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
    return _read_bytes_bounded(path, what=what).decode("utf-8", errors=errors)


def _read_bytes_bounded(path: Path, *, what: str) -> bytes:
    """Read a file's bytes, refusing one too large to be what it claims.

    Shared by :func:`read_text_bounded`, which always assumes UTF-8, and
    :func:`read_json_bounded`, which decodes by whatever BOM the bytes carry
    first. The bound is on the BYTES a caller handed this reader, which for a
    multi-byte encoding is a stronger ceiling than the character count it
    represents - a UTF-32 capture costs four bytes per character where the
    same content in UTF-8 costs mostly one, so it reaches :data:`MAX_INPUT_BYTES`
    at a quarter of the character count. That is conservative rather than
    wrong: a real capture or history file is orders of magnitude under either
    ceiling, and the alternative - measuring a decoded length before deciding
    whether to decode at all - is the same unbounded-materialisation problem
    this function exists to avoid.

    Args:
        path: The file to read.
        what: What the file was expected to be, for the refusal message.

    Returns:
        The file's raw bytes.

    Raises:
        MissingFileError: If the file is not there.
        ConfigurationError: If the file cannot be read, or is larger than
            :data:`MAX_INPUT_BYTES`.
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

    return raw


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
    text = _decode_bounded_bytes_by_bom(_read_bytes_bounded(path, what=what))
    return json.loads(text, object_pairs_hook=_object_without_repeated_keys)


def _decode_bounded_bytes_by_bom(raw: bytes) -> str:
    """Decode `raw` by whichever BOM it opens with, or as strict UTF-8 with none.

    A capture saved through Windows PowerShell 5.1's ``>`` (``Out-File``) is
    UTF-16LE with a BOM, and ``Out-File -Encoding utf8`` is UTF-8 with one;
    PS 5.1 is the default shell on Windows 10 and 11, and the documented
    ``ssh host lsdsk snapshot -o - > capture.json`` recipe goes through it.
    Decoding such a file as plain UTF-8 either raises immediately on the
    UTF-16/32 byte pattern, or - for a UTF-8 BOM - succeeds and leaves a
    leading U+FEFF character that ``json.loads`` then refuses on its own
    terms, as "Unexpected UTF-8 BOM". Both read as this tool's exit 78 either
    way, for a file that is perfectly good JSON once the mark is honoured.

    A file with NO recognised BOM is decoded strict UTF-8, unchanged from
    before this existed: still refused with the same 78 when it is not valid
    UTF-8, since a file this tool did not write and cannot identify by a mark
    is not one to guess about.

    Args:
        raw: The bytes :func:`_read_bytes_bounded` already bounded.

    Returns:
        The decoded text, with any BOM consumed rather than left as a
        character `json.loads` would then refuse.

    Raises:
        UnicodeDecodeError: If the bytes do not decode under the codec their
            BOM names, or - carrying none - are not valid UTF-8.
    """
    for bom, codec in _BOM_CODECS:
        if raw.startswith(bom):
            return raw.decode(codec)
    return raw.decode("utf-8")


def _object_without_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object, refusing it if a key is given more than once.

    Called for every object in the document, nested ones included, which is
    where the repeat can hide: the sections a capture is read by are one
    level down and the samples in a history store are three.
    """
    entry: dict[str, Any] = {}
    for key, value in pairs:
        if key in entry:
            # Quoted inert and cut short: the key is the file's, so it can carry
            # an escape sequence or be most of the file.
            message = f"the key '{visible_text(key)}' is given twice in one object"
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


__all__ = ["MAX_INPUT_BYTES", "fits_a_bounded_read", "read_json_bounded", "read_text_bounded"]
