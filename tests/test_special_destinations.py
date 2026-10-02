"""A destination that is not a regular file is written into or refused, never replaced.

The two stores write by renaming a temporary file over the destination, which is
right for a file and wrong for anything else: the rename REPLACES the directory
entry. Measured before this, ``snapshot -o <fifo>`` left a regular 0600 file
where the FIFO had been and the reader waiting on it got nothing, and as root
``snapshot -o /dev/null`` replaced the system's null device with a snapshot.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from lsdsk.adapters.atomicfile import replace_atomically
from lsdsk.adapters.history.store import save_history
from lsdsk.adapters.hw.snapshot import save
from lsdsk.domain.history import History

HAS_FIFO = hasattr(os, "mkfifo")

CAPTURE: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}}


def _drain(fifo: Path, into: list[bytes]) -> threading.Thread:
    """Read the FIFO to its end on a thread, as the process at the other end would."""

    def read() -> None:
        with fifo.open("rb") as stream:
            into.append(stream.read())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    return reader


@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_a_snapshot_into_a_fifo_reaches_its_reader_and_leaves_the_fifo(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo, 0o644)
    received: list[bytes] = []
    reader = _drain(fifo, received)

    save(CAPTURE, fifo)
    reader.join(timeout=10)

    assert not reader.is_alive(), "the reader never saw the end of the stream"
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode), "the FIFO was replaced by a regular file"
    assert stat.S_IMODE(os.lstat(fifo).st_mode) == 0o644, "a FIFO that is not ours had its mode narrowed"
    assert received and b'"hostname"' in received[0], f"the reader received {received!r}"


@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_the_history_store_refuses_a_destination_that_is_not_a_file(tmp_path: Path) -> None:
    """The store has no in-place path, because a store written in place can be half-written."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    with pytest.raises(OSError, match="not a regular file"):
        save_history(History(hostname="box"), fifo)

    assert stat.S_ISFIFO(os.lstat(fifo).st_mode), "the refusal still replaced the FIFO"


@pytest.mark.os_posix
def test_a_character_device_is_never_renamed_over(tmp_path: Path) -> None:
    """``/dev/null`` itself, the case that destroys a system when run as root.

    Unprivileged, the temporary file cannot be created in ``/dev`` at all, so a
    test that merely saves there passes either way, and so does the first
    assertion below: the alternative is reached through the no-temporary-file
    fallback too. The second one is what separates the two. With NO alternative
    a device must be refused for what it is, before any temporary file is
    attempted - which is the path root takes and the rename used to sit on.
    """
    calls: list[Path] = []

    replace_atomically(Path("/dev/null"), "{}", mode=0o600, without_a_temporary_file=lambda path, _: calls.append(path))

    assert calls == [Path("/dev/null")], "a character device went down the rename path"
    with pytest.raises(OSError, match="not a regular file"):
        replace_atomically(Path("/dev/null"), "{}", mode=0o600)
    assert not list(tmp_path.iterdir()), "the control: nothing should have been written beside the test"


def test_a_regular_file_is_still_replaced_atomically(tmp_path: Path) -> None:
    """The control: the guard must not divert the case it exists beside."""
    target = tmp_path / "store.json"
    target.write_text("old", encoding="utf-8")
    calls: list[Path] = []

    replace_atomically(target, "new", mode=0o600, without_a_temporary_file=lambda path, _: calls.append(path))

    assert calls == []
    assert target.read_text(encoding="utf-8") == "new"
