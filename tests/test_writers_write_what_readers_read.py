"""Anything this tool writes for itself to read back, its reader accepts.

The history store and a snapshot are both read back through the same size bound
as any file from outside the tool, so a writer with no bound of its own could
produce a file its own reader refuses. For the store every later run then
ignores it, stops recording for good and leaves ``record`` at exit 0; a snapshot
simply can never be replayed.

The boundary is held at exactly the read limit and one byte past it. The file is
padded with a model name made of four-byte characters, because the bound is on
BYTES and every character of a model name is walked once by the text cleaner:
sixty-four megabytes of ASCII cost several seconds to validate, a quarter as
many characters costs a quarter of that.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.history.store import load_history, save_history
from lsdsk.adapters.hw import snapshot
from lsdsk.adapters.textfile import MAX_INPUT_BYTES
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import DiskSeries, History, Sample

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

HOST = "linux-sas-hba"
WIDE = "\U0001f4be"  # a floppy disk, four bytes in UTF-8
SNAPSHOT = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


def _padded(model: str) -> History:
    """A one-drive history whose size is decided by its model name.

    Built without validation because the text cleaner is the cost the padding
    would otherwise pay three times over; the reader, which is what is under
    test, validates it in full.
    """
    series = DiskSeries.model_construct(
        identity="pad",
        model=model,
        samples=(Sample(power_on_hours=1, captured_at="t", crc_errors=0),),
    )
    return History.model_construct(hostname=HOST, series=(series,))


def _model_of_bytes(count: int) -> str:
    """A model name that is exactly `count` bytes in UTF-8."""
    wide, narrow = divmod(count, len(WIDE.encode("utf-8")))
    return WIDE * wide + "x" * narrow


def _model_filling(path: Path, target: int) -> str:
    """The model name that makes the WRITER's own file exactly `target` bytes.

    Measured from what the writer produced for an empty name rather than from a
    format assumed here, so the arithmetic follows whatever layout it chooses.
    """
    save_history(_padded(""), path)
    overhead = path.stat().st_size
    path.unlink()
    return _model_of_bytes(target - overhead)


@pytest.mark.os_agnostic
def test_a_store_of_exactly_the_read_limit_is_written_and_read_back(tmp_path: Path) -> None:
    """The largest file the writer may produce is one the reader takes."""
    store = tmp_path / "history.json"
    save_history(_padded(_model_filling(store, MAX_INPUT_BYTES)), store)

    assert store.stat().st_size == MAX_INPUT_BYTES, "the padding missed the boundary, so this asserted nothing"
    assert load_history(store, hostname=HOST).series[0].identity == "pad"


@pytest.mark.os_agnostic
def test_a_store_one_byte_past_the_read_limit_is_refused_and_the_old_one_kept(tmp_path: Path) -> None:
    """The writer refuses what the reader would refuse, loudly and before the rename."""
    store = tmp_path / "history.json"
    model = _model_filling(store, MAX_INPUT_BYTES + 1)
    save_history(_padded(""), store)
    before = store.read_bytes()

    with pytest.raises(OSError, match="larger than the history reader accepts"):
        save_history(_padded(model), store)

    assert store.read_bytes() == before, "the previous store was replaced by one nothing can read"
    assert not list(tmp_path.glob(".*.tmp")), "a temporary file was left behind"


@pytest.mark.os_agnostic
def test_record_fails_loudly_when_the_reading_would_outgrow_the_store(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
) -> None:
    """``record`` leaves a non-zero code and names the store, rather than 0.

    The store is seeded a few hundred bytes under the limit, so any real
    reading pushes it over. Before the writer checked, this run wrote a file
    every later run refused, and exited 0 each time.
    """
    store = tmp_path / "history.json"
    save_history(_padded(_model_filling(store, MAX_INPUT_BYTES - 512)), store)
    before = store.read_bytes()

    result = cli_runner.invoke(
        cli,
        ["--history-file", str(store), "record", "--replay", str(SNAPSHOT)],
        obj=production_factory,
    )

    assert result.exit_code == ExitCode.IO_ERROR, f"a store that could not be written left {result.exit_code}"
    assert str(store) in result.stderr, f"the refusal does not name the store: {result.stderr!r}"
    assert store.read_bytes() == before, "the previous store was replaced"


@pytest.mark.os_agnostic
def test_a_snapshot_its_own_replay_would_refuse_is_not_written(tmp_path: Path) -> None:
    """The snapshot writer holds the same rule, at the one serialiser both outputs share.

    Padded through a key the capture models do not name, which is kept rather
    than refused because a capture is meant to carry context for a bug report:
    every field a model DOES name is length-bounded, so no single one of them
    can carry the file past the limit.
    """
    context = "x" * MAX_INPUT_BYTES
    capture: dict[str, Any] = {
        "schema": 2,
        "platform": "linux",
        "hostname": HOST,
        "kernel": "6.1",
        "pci": {},
        "context": context,
    }
    destination = tmp_path / "capture.json"

    with pytest.raises(ConfigurationError, match="larger than --replay reads"):
        snapshot.save(capture, destination)

    assert not destination.exists(), "a snapshot nothing can replay was written"
