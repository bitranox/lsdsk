"""A run reads the counter store once, however many of its sections draw from it.

`report`, `trend` and `health` each judged the counters against the store and
then read it again to draw the table, so every run paid for two full reads of a
file that can be megabytes, and a store that changed between the two was judged
by one copy and drawn from another.
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


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", [["report"], ["trend"], ["health"]])
def test_a_command_reads_the_counter_store_once(
    command: list[str],
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "history.json"
    seeded = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "record", "--replay", str(FIXTURES / "linux-sas-hba.json")],
        obj=production_factory,
    )
    assert seeded.exit_code == 0 and store.exists(), (
        "the control: the store this test counts reads of was never written"
    )

    loads: list[Path] = []
    real = history_command.load_history

    def counted(path: Path, **kwargs: Any) -> Any:
        loads.append(path)
        return real(path, **kwargs)

    # A delegating spy at the one name read_history resolves: it changes no
    # behaviour, it only counts, because no output says how often a file was read.
    monkeypatch.setattr(history_command, "load_history", counted)

    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), *command, "--replay", str(FIXTURES / "linux-sas-hba-later.json")],
        obj=production_factory,
    )

    assert result.stdout, f"{command}: no output, so this asserted nothing"
    assert loads == [store], f"{command} read the store {len(loads)} times"
