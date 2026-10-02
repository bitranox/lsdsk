"""The lock beside the counter store: what it opens, and how long it waits.

The lock file sits in the store's own directory, which a ``--history-file``
can put anywhere - a shared or group-writable directory included. Opened with
a plain ``O_CREAT``, a symlink planted at the lock's name was followed and its
target created with the store's mode, as whoever ran ``record``: measured, a
link to ``victim/x`` left ``victim/x`` behind and the run exited 0.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.history.store import history_lock

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
SNAPSHOT = FIXTURES / "linux-sas-hba.json"


def _lock_of(store: Path) -> Path:
    """The lock file ``history_lock`` takes for ``store``."""
    return store.with_name(f".{store.name}.lock")


def _plant_a_link(tmp_path: Path) -> tuple[Path, Path]:
    """A store in a shared directory whose lock name is a link to a file elsewhere."""
    shared, victim = tmp_path / "shared", tmp_path / "victim" / "x"
    shared.mkdir()
    victim.parent.mkdir()
    store = shared / "history.json"
    _lock_of(store).symlink_to(victim)
    return store, victim


@pytest.mark.os_posix
def test_the_lock_refuses_a_symlink_planted_at_its_name(tmp_path: Path) -> None:
    store, victim = _plant_a_link(tmp_path)

    with pytest.raises(OSError, match=r"lock"), history_lock(store):
        pass

    assert not victim.exists(), "the lock followed a planted link and created its target"


@pytest.mark.os_posix
def test_the_lock_refuses_a_lock_name_that_is_not_a_regular_file(tmp_path: Path) -> None:
    """A FIFO at the lock's name would otherwise be opened and locked as if it were the lock."""
    store = tmp_path / "history.json"
    os.mkfifo(_lock_of(store))

    with pytest.raises(OSError, match=r"not a regular file"), history_lock(store, wait=0.1):
        pass


@pytest.mark.os_posix
def test_a_planted_lock_link_is_a_write_that_failed_not_a_recorded_run(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    store, victim = _plant_a_link(tmp_path)

    result = cli_runner.invoke(
        cli, ["--history-file", str(store), "record", "--replay", str(SNAPSHOT)], obj=production_factory
    )

    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a traceback: {result.exception!r}"
    )
    assert result.exit_code == ExitCode.IO_ERROR, f"a refused lock left exit {result.exit_code}: {result.output!r}"
    assert not victim.exists(), "the run created the planted link's target"
    assert not store.exists(), "the run wrote a store it held no lock for"


def test_a_plain_lock_is_still_taken(tmp_path: Path) -> None:
    """The control: the guard must not refuse the lock file it creates itself."""
    store = tmp_path / "history.json"

    with history_lock(store):
        pass
    with history_lock(store):  # and again, now that the file exists
        pass

    assert _lock_of(store).is_file()
