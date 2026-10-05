"""A run reads the counter store once to judge and draw, and a recording run once more under the lock.

`report`, `trend` and `health` each judged the counters against the store and
then read it again to draw the table, so every run paid for two full reads of a
file that can be megabytes, and a store that changed between the two was judged
by one copy and drawn from another. Those sections share one read now.

A run that RECORDS reads twice by design: once to judge, and again while it
holds the store's lock, because another run may have stored a reading between
the two and folding into the first copy would write that reading away. The
second read is pinned too, so neither count can drift unnoticed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.commands import history as history_command

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"


def _seeded_store(cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path) -> Path:
    """A store holding one reading, so every later read has something to read."""
    store = tmp_path / "history.json"
    seeded = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "record", "--replay", str(FIXTURES / "linux-sas-hba.json")],
        obj=production_factory,
    )
    assert seeded.exit_code == 0 and store.exists(), (
        "the control: the store this test counts reads of was never written"
    )
    return store


def _count_the_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every path the history command reads a store from, from now on."""
    loads: list[Path] = []
    real = history_command.load_history

    def counted(path: Path, **kwargs: Any) -> Any:
        loads.append(path)
        return real(path, **kwargs)

    # A delegating spy at the one name both reads resolve: it changes no
    # behaviour, it only counts, because no output says how often a file was read.
    monkeypatch.setattr(history_command, "load_history", counted)
    return loads


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", [["report"], ["trend"], ["health"]])
def test_a_command_reads_the_counter_store_once(
    command: list[str],
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seeded_store(cli_runner, production_factory, tmp_path)
    loads = _count_the_reads(monkeypatch)

    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), *command, "--replay", str(FIXTURES / "linux-sas-hba-later.json")],
        obj=production_factory,
    )

    assert result.stdout, f"{command}: no output, so this asserted nothing"
    assert loads == [store], f"{command} read the store {len(loads)} times"


@pytest.mark.os_agnostic
def test_a_recording_run_reads_the_store_again_under_the_lock(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seeded_store(cli_runner, production_factory, tmp_path)
    loads = _count_the_reads(monkeypatch)

    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "record", "--replay", str(FIXTURES / "linux-sas-hba-later.json")],
        obj=production_factory,
    )

    # The control: a run that stored nothing would never have taken the lock.
    assert result.exit_code == 0, f"the recording run failed: {result.output!r}"
    assert loads == [store, store], f"a recording run read the store {len(loads)} times"
