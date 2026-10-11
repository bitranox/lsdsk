"""The reaper of a killed write's temporary files, driven through its own function.

It runs on every recording run inside the store's lock, so what it must NOT
touch matters as much as what it removes: a name ``mkstemp`` cannot have made, a
link, and a directory it cannot read or write.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from lsdsk.adapters.history.store import ABANDONED_TEMPORARY_SECONDS, remove_abandoned_temporaries

OLD = ABANDONED_TEMPORARY_SECONDS * 12


def _age(path: Path, seconds: float) -> None:
    """Set `path`'s own modification time (a link's, not its target's) `seconds` into the past."""
    then = path.lstat().st_mtime - seconds
    os.utime(path, (then, then), follow_symlinks=False)


def _leftover(directory: Path, name: str) -> Path:
    """A regular file as a killed write leaves it: old enough to be abandoned."""
    path = directory / name
    path.write_text("{", encoding="utf-8")
    _age(path, OLD)
    return path


@pytest.mark.os_agnostic
def test_a_name_mkstemp_could_not_have_made_is_not_taken_for_a_leftover(tmp_path: Path) -> None:
    """A real ``tempfile.mkstemp`` name is reaped; one with a capital, which mkstemp never draws, is not."""
    store = tmp_path / "history.json"
    descriptor, name = tempfile.mkstemp(dir=tmp_path, prefix=f".{store.name}.", suffix=".tmp")
    os.close(descriptor)
    made = Path(name)
    made.write_text("{", encoding="utf-8")
    _age(made, OLD)
    upper = _leftover(tmp_path, ".history.json.K3J9X2AB.tmp")
    mixed = _leftover(tmp_path, ".history.json.k3j9X2ab.tmp")

    remove_abandoned_temporaries(store)

    assert not made.exists(), "the name mkstemp makes was not reaped, so the test proves nothing"
    assert upper.exists(), "an upper-case name was taken for a leftover"
    assert mixed.exists(), "a mixed-case name was taken for a leftover"


@pytest.mark.os_posix
def test_an_old_link_named_like_a_leftover_is_not_removed(tmp_path: Path) -> None:
    """Only a regular file is reaped: a link is somebody's arrangement, never a killed write's file."""
    store = tmp_path / "history.json"
    target = tmp_path / "elsewhere"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / ".history.json.k3j9x2ab.tmp"
    link.symlink_to(target)
    _age(link, OLD)
    regular = _leftover(tmp_path, ".history.json.q8w7e6rt.tmp")

    remove_abandoned_temporaries(store)

    assert not regular.exists(), "the regular leftover was not reaped, so the link case proved nothing"
    assert link.is_symlink(), "an old link named like a leftover was removed"


@pytest.mark.os_agnostic
def test_a_directory_that_cannot_be_listed_is_left_without_an_error(tmp_path: Path) -> None:
    """Housekeeping never fails a recording: a missing or non-directory parent is simply skipped."""
    remove_abandoned_temporaries(tmp_path / "absent" / "history.json")
    plain_file = tmp_path / "plain"
    plain_file.write_text("x", encoding="utf-8")
    remove_abandoned_temporaries(plain_file / "history.json")


@pytest.mark.os_posix
def test_a_leftover_that_cannot_be_removed_is_left_without_an_error(tmp_path: Path) -> None:
    """A leftover in a directory the run may not write is skipped, and the later ones are still reached."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions, so nothing here can be made unremovable")
    store = tmp_path / "history.json"
    stuck = _leftover(tmp_path, ".history.json.k3j9x2ab.tmp")
    tmp_path.chmod(0o500)
    try:
        remove_abandoned_temporaries(store)
        assert stuck.exists()
    finally:
        tmp_path.chmod(0o700)
