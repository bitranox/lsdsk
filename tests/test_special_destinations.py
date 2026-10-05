"""A destination that is not a regular file is written into or refused, never replaced.

The two stores write by renaming a temporary file over the destination, which is
right for a file and wrong for anything else: the rename REPLACES the directory
entry. Measured before this, ``snapshot -o <fifo>`` left a regular 0600 file
where the FIFO had been and the reader waiting on it got nothing, and as root
``snapshot -o /dev/null`` replaced the system's null device with a snapshot.
"""

from __future__ import annotations

import contextlib
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


def _release_a_blocked_reader(fifo: Path) -> None:
    """Give a reader stuck opening ``fifo`` its end of stream, so a failing arm leaves no thread blocked."""
    with contextlib.suppress(OSError):
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))


@pytest.mark.os_posix
@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_a_link_to_a_fifo_is_written_into_and_both_are_left_standing(tmp_path: Path) -> None:
    """``-o /dev/stdout`` on a pipe is this shape: a link, in a directory, to a FIFO.

    Measured before this: the link was renamed over with a regular 0600 file in
    any writable directory - as root, ``/dev`` itself - and the FIFO's reader got
    nothing at all.
    """
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo, 0o644)
    link = tmp_path / "stdout"
    link.symlink_to(fifo)
    received: list[bytes] = []
    reader = _drain(fifo, received)

    try:
        save(CAPTURE, link)
    finally:
        reader.join(timeout=10)
        _release_a_blocked_reader(fifo)
        reader.join(timeout=10)

    assert link.is_symlink(), "the link was replaced by a regular file"
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode), "the FIFO behind the link was replaced"
    assert received and b'"hostname"' in received[0], f"the reader received {received!r}"


@pytest.mark.os_posix
def test_a_link_to_a_character_device_is_written_into_not_renamed_over(tmp_path: Path) -> None:
    """``/dev/stdout`` for root: a link in a directory where the temporary file CAN be created."""
    link = tmp_path / "null"
    link.symlink_to("/dev/null")

    save(CAPTURE, link)

    assert link.is_symlink(), "the link was replaced by a regular file"
    assert link.readlink() == Path("/dev/null")
    assert [entry.name for entry in tmp_path.iterdir()] == ["null"], "a temporary file was left behind"


@pytest.mark.os_posix
@pytest.mark.skipif(not HAS_FIFO, reason="a FIFO needs os.mkfifo")
def test_the_history_store_refuses_a_link_to_a_fifo_as_it_refuses_the_fifo(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    link = tmp_path / "history.json"
    link.symlink_to(fifo)

    with pytest.raises(OSError, match="not a regular file"):
        save_history(History(hostname="box"), link)

    assert link.is_symlink(), "the refusal still replaced the link"


@pytest.mark.os_posix
def test_a_link_to_a_regular_file_is_still_replaced_never_traversed(tmp_path: Path) -> None:
    """The control: following a link is for a device or a FIFO, never for somebody's file."""
    victim = tmp_path / "victim"
    victim.write_text("theirs", encoding="utf-8")
    link = tmp_path / "store.json"
    link.symlink_to(victim)

    replace_atomically(link, "ours", mode=0o600)

    assert victim.read_text(encoding="utf-8") == "theirs", "the write traversed the link into its target"
    assert not link.is_symlink() and link.read_text(encoding="utf-8") == "ours"


@pytest.mark.os_posix
def test_a_dangling_link_is_still_replaced(tmp_path: Path) -> None:
    """The control: a link to nothing is not followed into creating its target."""
    link = tmp_path / "store.json"
    link.symlink_to(tmp_path / "nowhere")

    replace_atomically(link, "ours", mode=0o600)

    assert not (tmp_path / "nowhere").exists(), "the write created the link's target"
    assert not link.is_symlink() and link.read_text(encoding="utf-8") == "ours"
