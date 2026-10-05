"""Replacing a file this tool owns, so that a failed write leaves the old one intact.

Two files are written by this tool and read back by it later: a snapshot and
the counter history. Both are replaced the same way - a temporary file in the
destination's own directory, written, forced out, narrowed to its mode, then
renamed over the destination - and both have to close the raw descriptor
``mkstemp`` hands back however the write ends. That sequence lives here once, so
a writer cannot carry a copy of it that forgot the descriptor.

System Role:
    Adapter-layer output boundary shared by the snapshot and history stores.

Contents:
    * :func:`replace_atomically` - write a whole file and rename it into place.
    * :func:`write_through` - write through a raw descriptor and always close it.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def write_through(descriptor: int, body: str, *, sync: bool) -> None:
    """Write the body through a raw descriptor and close it however that ends.

    ``os.fdopen`` takes ownership of the descriptor only once it RETURNS, so a
    failure inside it leaves the descriptor open with nothing holding it: the
    caller's cleanup can unlink the file it named and still leak the handle.
    Measured before this existed - one refused save moved the next free
    descriptor up by one.

    Args:
        descriptor: A descriptor nothing else owns yet.
        body: The whole file.
        sync: Whether to force the bytes out before the descriptor is closed.
            The atomic path does, because the rename that follows must not be
            able to publish an empty file after a crash. An in-place write to a
            character device does not: ``fsync`` on one fails with ``EINVAL``
            rather than meaning anything.

    Raises:
        OSError: If ``os.fdopen``, the write, the flush, ``fsync`` or the close
            fails - a full disk, an I/O error, or ``EINVAL`` from ``fsync`` on a
            character device when ``sync`` is set. The original exception
            propagates unchanged, and the descriptor is closed either way.
            Both callers let it through: :func:`replace_atomically` removes its
            temporary file first, and the snapshot writer's in-place path
            reports it as the failed write it is.
        UnicodeEncodeError: If ``body`` holds a character UTF-8 cannot encode,
            which is a lone surrogate. Neither caller can produce one - both
            bodies are JSON a serialiser wrote, and ``json.dumps`` and pydantic
            each escape or refuse a surrogate - so this is a contract for a new
            caller rather than a path anything takes today. The descriptor is
            closed here too.

    Example:
        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     target = Path(directory) / "out.txt"
        ...     write_through(os.open(target, os.O_WRONLY | os.O_CREAT), "hi", sync=False)
        ...     target.read_text(encoding="utf-8")
        'hi'
    """
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        stream.write(body)
        if sync:
            stream.flush()
            os.fsync(stream.fileno())


class _Destination(Enum):
    """What a write has to do with whatever stands at its destination."""

    #: A regular file, nothing at all, or a link to either or to nothing: renamed over.
    REPLACE = auto()
    #: A character device or a FIFO itself: written into by the caller's in-place writer.
    WRITE_INTO = auto()
    #: A symlink whose target is a character device or a FIFO: written into through the link.
    WRITE_THROUGH_THE_LINK = auto()


def _is_a_stream(mode: int) -> bool:
    """Whether ``mode`` is a character device or a FIFO, which are written into rather than replaced."""
    return stat.S_ISCHR(mode) or stat.S_ISFIFO(mode)


def _destination_at(path: Path) -> _Destination:
    """What stands at ``path``, as far as a write to it is concerned.

    A rename replaces the directory entry, which is right for a file and
    destroys anything else: a FIFO a reader is waiting on, or - for root - the
    system's own ``/dev/null``. A symlink is replaced deliberately when it leads
    to a file or to nothing, so a link planted at the destination is never
    traversed into somebody's file. A link to a character device or a FIFO is
    the exception, because that is what ``/dev/stdout`` is: replacing it put a
    regular 0600 file in ``/dev`` for root, and for anyone left a FIFO's reader
    with nothing.

    Raises:
        OSError: If ``path`` exists and is neither a regular file, a symlink, a
            character device nor a FIFO - a block device or a socket, which no
            caller could mean to write a JSON document into.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return _Destination.REPLACE
    if stat.S_ISLNK(mode):
        try:
            target = path.stat().st_mode
        # A dangling link, a loop or a target this process may not look at:
        # nothing says a stream is behind it, so it is replaced as before.
        except OSError:
            return _Destination.REPLACE
        return _Destination.WRITE_THROUGH_THE_LINK if _is_a_stream(target) else _Destination.REPLACE
    if stat.S_ISREG(mode):
        return _Destination.REPLACE
    if _is_a_stream(mode):
        return _Destination.WRITE_INTO
    raise OSError(errno.EINVAL, f"{path} is not a regular file, a character device or a FIFO", str(path))


def _write_through_the_link(path: Path, body: str) -> None:
    """Write into the character device or FIFO a symlink leads to.

    The open follows the link, which is the point. What it reached is checked
    on the DESCRIPTOR rather than trusted from the earlier look, so a link
    retargeted at a regular file in between is refused rather than written
    through: that is the traversal the rename path exists to prevent. No
    ``O_CREAT``, so a link that dangles by now creates nothing, and no sync,
    which on a character device fails with ``EINVAL`` rather than meaning
    anything.

    Raises:
        OSError: If the open or the write fails, or the link no longer leads to
            a character device or a FIFO (``errno.EINVAL``).
    """
    descriptor = os.open(path, os.O_WRONLY)
    if not _is_a_stream(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        message = f"{path} no longer leads to a character device or a FIFO, so it is not written through"
        raise OSError(errno.EINVAL, message, str(path))
    write_through(descriptor, body, sync=False)


def replace_atomically(
    path: Path,
    body: str,
    *,
    mode: int,
    without_a_temporary_file: Callable[[Path, str], None] | None = None,
) -> None:
    """Replace ``path`` with ``body`` so that a failure leaves the old file as it was.

    The temporary file is created in the destination's directory, because a
    rename is only atomic within one filesystem. A rename never follows the last
    component either, so a symlink planted at the destination is replaced
    rather than traversed, and the file is never briefly readable at the
    ambient umask on its way to ``mode``. The one link that is followed is a
    link to a character device or a FIFO, such as ``/dev/stdout``, and only
    when the caller accepts a write that is not atomic: it is written through,
    since replacing it would put a regular file where the stream was.

    Args:
        path: The destination. Its directory must already exist.
        body: The whole file.
        mode: The permission bits the file ends with.
        without_a_temporary_file: What to do instead when the destination
            cannot be replaced by a rename, called with ``path`` and ``body``:
            no temporary file can be created beside it, or it is a character
            device or a FIFO, which a rename would destroy rather than write.
            Nothing has been written at that point. The snapshot writer writes
            in place there. A symlink to a character device or a FIFO is
            not handed to it, because an in-place writer refuses a link; this
            function writes through that link itself, but only when an
            alternative was given. ``None`` - the history store - refuses
            every such destination instead, since a history that cannot be
            replaced atomically is one a failed write could destroy.

    Raises:
        OSError: If no temporary file can be created, or the destination is
            not a regular file, and no alternative was given; if it is a block
            device or a socket whatever was given; if a link that led to a
            character device or a FIFO no longer does when it is opened; or if
            the write, the sync or the rename fails. The original
            exception propagates, so a ``PermissionError`` stays one. The
            temporary file is removed and any previous file at ``path`` is
            untouched.

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     target = Path(directory) / "store.json"
        ...     replace_atomically(target, "{}", mode=0o600)
        ...     target.read_text(encoding="utf-8")
        '{}'
    """
    destination = _destination_at(path)
    if destination is not _Destination.REPLACE:
        if without_a_temporary_file is None:
            raise OSError(errno.EINVAL, f"{path} is not a regular file, so it cannot be replaced", str(path))
        # The caller's in-place writer refuses a symlink, which is right for
        # the no-temporary-file case it exists for; a link that leads to a
        # stream is written through here instead.
        if destination is _Destination.WRITE_THROUGH_THE_LINK:
            _write_through_the_link(path, body)
        else:
            without_a_temporary_file(path, body)
        return
    try:
        handle, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    except OSError:
        if without_a_temporary_file is None:
            raise
        without_a_temporary_file(path, body)
        return
    temporary = Path(temporary_name)
    try:
        write_through(handle, body, sync=True)
        # mkstemp already creates at 0600; setting it explicitly means the
        # guarantee does not rest on that, and a umask cannot widen it. A
        # filesystem that does not carry modes is not a failure to write.
        with contextlib.suppress(OSError):
            temporary.chmod(mode)
        temporary.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


__all__ = ["replace_atomically", "write_through"]
