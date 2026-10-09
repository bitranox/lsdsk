"""``write_through`` forces the bytes out on the file it wrote when asked to.

The atomic path renames the temporary file over the target, so the bytes must be
on disk before the rename can publish them: otherwise a crash leaves an empty
file where the old one stood. Nothing observed the ``fsync``, so deleting it kept
the suite green. ``os.fsync`` is the one thing spied, because it is the stdlib
edge a test cannot observe any other way; the file and its descriptor are real.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from lsdsk.adapters.atomicfile import write_through

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def spy_on_fsync(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every descriptor handed to ``os.fsync`` while still syncing it for real."""
    synced: list[int] = []
    real = os.fsync

    def recording(descriptor: int) -> None:
        synced.append(descriptor)
        real(descriptor)

    monkeypatch.setattr(os, "fsync", recording)
    return synced


def test_a_synced_write_forces_the_written_files_descriptor_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "out.txt"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT)
    synced = spy_on_fsync(monkeypatch)

    write_through(descriptor, "body", sync=True)

    assert synced == [descriptor], f"fsync was not called on the written file's descriptor: {synced}"
    assert target.read_text(encoding="utf-8") == "body"


def test_an_unsynced_write_never_calls_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: ``sync=False`` is the character-device path, where fsync would fail."""
    target = tmp_path / "out.txt"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT)
    synced = spy_on_fsync(monkeypatch)

    write_through(descriptor, "body", sync=False)

    assert synced == [], f"fsync ran although sync was off: {synced}"
    assert target.read_text(encoding="utf-8") == "body"
