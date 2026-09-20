"""Every descriptor a write opens is closed, however the write ends.

``mkstemp`` and ``os.open`` hand back a RAW descriptor, and nothing owns it
until the wrapper around it has been constructed. A failure in between leaks it
silently: the run still reports the error it met, so the only evidence is the
process's own descriptor table, which no test looking at output can see.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.hw import snapshot

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CAPTURE: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "x", "pci": {}}


def _next_free_descriptor() -> int:
    """The number the next ``open`` would be given.

    A count of open descriptors is what this measures, without reading
    ``/proc``: both platforms hand out the lowest free number, so the number
    itself rises by one for every descriptor that was opened and not closed.
    """
    probe = os.open(os.devnull, os.O_RDONLY)
    os.close(probe)
    return probe


@pytest.fixture
def fdopen_refuses(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the wrapper around a raw descriptor fail, leaving the descriptor open.

    The true external edge, and the only one: the descriptor is handed out by
    the C library and taken over by ``os.fdopen``, so there is no seam of ours
    between them to inject at.
    """

    def refuse(*_args: object, **_kwargs: object) -> object:
        message = "no stream for this descriptor"
        raise OSError(message)

    monkeypatch.setattr(snapshot.os, "fdopen", refuse)
    yield


@pytest.mark.os_agnostic
def test_a_failed_save_closes_the_descriptor_it_opened(tmp_path: Path, fdopen_refuses: None) -> None:
    """The atomic path: ``mkstemp`` succeeded and the wrapper around it did not."""
    del fdopen_refuses
    before = _next_free_descriptor()

    with pytest.raises(OSError, match="no stream for this descriptor"):
        snapshot.save(CAPTURE, tmp_path / "capture.json")

    assert _next_free_descriptor() == before, "the temporary file's descriptor was left open"
    assert not list(tmp_path.glob(".*.tmp")), "a temporary file was left behind"


@pytest.mark.os_posix
def test_a_failed_write_in_place_closes_the_descriptor_it_opened(tmp_path: Path, fdopen_refuses: None) -> None:
    """The fallback path has the identical shape around ``os.open``.

    Reached the way the fallback is always reached - a directory that can hold
    no temporary file - rather than by calling the private function, so the
    test would still fail if the fallback were rewired.
    """
    del fdopen_refuses
    destination = tmp_path / "capture.json"
    destination.touch()
    tmp_path.chmod(0o500)
    before = _next_free_descriptor()
    try:
        with pytest.raises(OSError):
            snapshot.save(CAPTURE, destination)
    finally:
        tmp_path.chmod(0o700)

    assert _next_free_descriptor() == before, "the destination's descriptor was left open"
