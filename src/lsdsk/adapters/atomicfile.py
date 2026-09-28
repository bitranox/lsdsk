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
import os
import tempfile
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
    ambient umask on its way to ``mode``.

    Args:
        path: The destination. Its directory must already exist.
        body: The whole file.
        mode: The permission bits the file ends with.
        without_a_temporary_file: What to do instead when no temporary file can
            be created beside the destination, called with ``path`` and
            ``body``. Nothing has been written at that point; only the
            atomicity is impossible. The snapshot writer writes in place there.
            ``None`` - the history store - lets the refusal propagate, since a
            history that cannot be replaced atomically is one a failed write
            could destroy.

    Raises:
        OSError: If no temporary file can be created and no alternative was
            given, or the write, the sync or the rename fails. The original
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
