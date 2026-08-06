"""The sampling policy, driven through the CLI rather than through its helpers.

What matters here is not that the recorder works but that it fires exactly when
it should: never for somebody else's machine, never from inside a pipeline, and
never twice for a reading that cannot say anything new.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.history.store import load_history
from lsdsk.domain.enums import CliCommand
from lsdsk.domain.history import TrendVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures" / "hw"
SNAPSHOT = FIXTURES / "linux-sas-hba.json"
LATER = FIXTURES / "linux-sas-hba-later.json"
# A machine whose drives are all healthy: recorded, but nothing to report.
HEALTHY = FIXTURES / "linux-minimal.json"


def run(
    runner: CliRunner,
    factory: Callable[[], Any],
    *args: str,
) -> Any:
    """Invoke the CLI."""
    return runner.invoke(cli, list(args), obj=factory)


# --------------------------------------------------------------------------
# When a sample is written, and when it must not be
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_replaying_someone_elses_snapshot_never_records(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The samples belong to whichever machine produced the snapshot."""
    store = tmp_path / "history.json"
    result = run(cli_runner, production_factory, "--history-file", str(store), "trend", "--replay", str(SNAPSHOT))
    # Without this the assertion below would pass just as happily if the command
    # had crashed before ever reaching the recorder.
    assert result.output.strip(), "the command produced no output, so it proved nothing"
    assert not store.exists()


@pytest.mark.os_agnostic
def test_a_reporting_command_asked_for_json_never_records(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """A REPORTING command in a pipeline must not mutate state on the side.

    Named for what it proves. `lsdsk record --format json` does record, because
    recording is that command's whole purpose; the rule belongs to the commands
    that report.
    """
    store = tmp_path / "history.json"
    result = run(
        cli_runner,
        production_factory,
        "--history-file",
        str(store),
        "trend",
        "--replay",
        str(SNAPSHOT),
        "--format",
        "json",
    )
    assert json.loads(result.output)["command"], "no envelope, so the run proved nothing"
    assert not store.exists()


@pytest.mark.os_agnostic
def test_record_folds_a_snapshot_into_history(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    store = tmp_path / "history.json"
    result = run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    assert result.exit_code == 0
    assert store.exists()
    history = load_history(store, hostname="linux-sas-hba")
    assert len(history.series) >= 18


@pytest.mark.os_agnostic
def test_record_prints_nothing(cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path) -> None:
    """It is meant for a timer, where output is noise in a log."""
    store = tmp_path / "history.json"
    result = run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    assert result.output.strip() == ""


@pytest.mark.os_agnostic
def test_recording_the_same_reading_twice_adds_nothing(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The rate limit, on the drives' own clock rather than the wall clock.

    Two runs inside the same power-on hour cannot produce a rate however far
    apart the wall clock says they were, so the second stores a row and no
    information.
    """
    store = tmp_path / "history.json"
    for _ in range(3):
        run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    history = load_history(store, hostname="linux-sas-hba")
    assert all(len(series.samples) == 1 for series in history.series)


@pytest.mark.os_agnostic
def test_a_later_reading_of_the_same_machine_is_recorded(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The drives' clocks advanced, so this one has something to say."""
    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(LATER))
    history = load_history(store, hostname="linux-sas-hba")
    assert any(len(series.samples) == 2 for series in history.series)


# --------------------------------------------------------------------------
# What the trend command shows
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_trend_explains_itself_when_nothing_is_recorded_yet(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """An empty view must not read as good news."""
    store = tmp_path / "history.json"
    result = run(cli_runner, production_factory, "--history-file", str(store), "trend", "--replay", str(SNAPSHOT))
    assert "No counter history recorded yet" in strip_ansi(result.output)


@pytest.mark.os_agnostic
def test_trend_reports_the_live_fault_and_the_dead_one_differently(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """The end-to-end proof, on two real captures of one machine."""
    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(LATER))

    result = run(cli_runner, production_factory, "--history-file", str(store), "trend", "--replay", str(LATER))
    output = strip_ansi(result.output)
    assert "rising" in output
    assert "no new" in output
    assert "per power-on hour" in output


@pytest.mark.os_agnostic
def test_trend_names_itself_in_the_envelope(cli_runner: CliRunner, production_factory: Callable[[], Any]) -> None:
    result = run(cli_runner, production_factory, "trend", "--replay", str(SNAPSHOT), "--format", "json")
    payload = json.loads(result.output)
    assert payload["command"] == CliCommand.TREND.value


@pytest.mark.os_agnostic
def test_no_record_suppresses_a_write_that_would_otherwise_happen(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """Driven through `record`, which is the path that writes on a replay.

    Pointing this at `trend --replay` proves nothing: that path never records
    anyway, so the assertion holds with the flag removed entirely. The control
    below is what makes the first assertion mean something.
    """
    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    before = store.read_text(encoding="utf-8")

    run(cli_runner, production_factory, "--history-file", str(store), "--no-record", "record", "--replay", str(LATER))
    assert store.read_text(encoding="utf-8") == before, "--no-record must suppress the write"

    # Control: the same command without the flag DOES write, so the assertion
    # above is about the flag and not about the command being a no-op.
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(LATER))
    assert store.read_text(encoding="utf-8") != before, "without the flag this run must record"


@pytest.mark.os_agnostic
def test_an_unreadable_store_warns_but_still_diagnoses_the_hardware(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """A malformed state file must not silence a report about a failing drive."""
    store = tmp_path / "history.json"
    store.write_text("{ broken", encoding="utf-8")
    result = run(cli_runner, production_factory, "--history-file", str(store), "trend", "--replay", str(SNAPSHOT))
    combined = strip_ansi(result.output)
    assert "ignoring counter history" in combined
    assert result.exit_code in (0, 1)


@pytest.mark.os_agnostic
def test_wear_rows_appear_only_where_they_say_something(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """Every healthy drive wears, so wear must not fill the view with shrugs.

    On this machine twelve drives sit at 1 or 2 percent with nothing measurable
    yet. Rendering a row each pushed the drive that is actually failing off the
    top of the screen. The one at 59 percent still earns its row, because that
    is a replacement worth planning.
    """
    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(LATER))
    output = strip_ansi(
        run(cli_runner, production_factory, "--history-file", str(store), "trend", "--replay", str(LATER)).output
    )

    wear_rows = [line for line in output.splitlines() if " wear " in line]
    assert len(wear_rows) == 1, f"expected only the worn drive to report wear, got {len(wear_rows)} rows"
    assert "59" in wear_rows[0]
    # And the row that matters is still there, which is the point of removing the rest.
    assert any("2196127" in line for line in output.splitlines())


@pytest.mark.os_agnostic
def test_captures_written_before_the_timestamp_existed_still_replay() -> None:
    """Schema 1 captures predate captured_at and must keep working.

    Every fixture in this repo is one, so a version bump that refused them would
    have broken the whole suite; that it did not is the evidence, and this makes
    the promise explicit rather than incidental.
    """
    from lsdsk.adapters.hw.snapshot import OLDEST_READABLE_SCHEMA, CaptureEnvelope, load

    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert payload["schema"] == OLDEST_READABLE_SCHEMA
    assert CaptureEnvelope.model_validate(payload).captured_at is None
    assert load(SNAPSHOT).hostname == "linux-sas-hba"


@pytest.mark.os_agnostic
def test_a_replayed_capture_is_stamped_with_when_it_was_taken(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """Folding a year-old capture in must not stamp it as today.

    No rule reads the stamp, but a display that says a reading is from today
    when it is from last March is the same fault as reporting an unmeasured
    value: it states something nobody measured.
    """
    stamped = tmp_path / "capture.json"
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    payload["schema"] = 2
    payload["captured_at"] = "2025-03-01T12:00:00+00:00"
    stamped.write_text(json.dumps(payload), encoding="utf-8")

    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(stamped))

    history = load_history(store, hostname="linux-sas-hba")
    assert history.series
    assert history.series[0].samples[0].captured_at == "2025-03-01T12:00:00+00:00"


# --------------------------------------------------------------------------
# The health table's trend markers
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_health_table_marks_a_climbing_count_and_not_a_stopped_one(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """The same distinction as `trend`, in the table people already read."""
    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(LATER))

    output = strip_ansi(
        run(cli_runner, production_factory, "--history-file", str(store), "health", "--replay", str(LATER)).output
    )
    rows = {line.split()[1]: line for line in output.splitlines() if "/dev/sd" in line}

    assert "2196127+" in rows["/dev/sdd"], "a climbing count must be marked"
    assert "462640+" not in rows["/dev/sdj"], "a count proved to have stopped must not be marked"
    assert "462640" in rows["/dev/sdj"], "and it must still be shown"
    assert "still climbing" in output, "the mark needs its legend"


@pytest.mark.os_agnostic
def test_without_history_the_health_table_is_unchanged(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """No history means no marks and no legend, exactly as before the feature."""
    output = strip_ansi(
        run(
            cli_runner,
            production_factory,
            "--history-file",
            str(tmp_path / "absent.json"),
            "health",
            "--replay",
            str(LATER),
        ).output
    )
    assert "+" not in output.replace("+-", ""), "no counter may be marked without history"
    assert "still climbing" not in output, "no legend without marks"
    assert "2196127" in output, "the counts are still there, so this is not a vacuous pass"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("verdict", "expect_mark", "expect_red"),
    [
        (TrendVerdict.RISING, True, True),
        (TrendVerdict.QUIET, False, False),
        (TrendVerdict.TOO_CLOSE, False, True),
        (TrendVerdict.FIRST_SAMPLE, False, True),
        (TrendVerdict.RESET, False, True),
    ],
)
def test_a_counter_cell_reflects_what_the_samples_support(
    verdict: TrendVerdict, expect_mark: bool, expect_red: bool
) -> None:
    """The style matters as much as the text, and strip_ansi cannot see it.

    A count proved to have stopped drops out of red, because a red number that
    never changes teaches the reader to ignore red. Asserting only on the
    rendered text cannot tell that apart from a count still screaming.
    """
    from lsdsk.adapters.render import theme
    from lsdsk.adapters.render.tables import RISING_MARK, counter_cell
    from lsdsk.domain.history import CounterKind, Trend

    trend = Trend(CounterKind.CRC_ERRORS, verdict, 462640, 0, 16, None, 235.0)
    text, style = counter_cell(462640, trend)

    assert text.endswith(RISING_MARK) is expect_mark
    assert (style == theme.STYLE_FAILING) is expect_red


@pytest.mark.os_agnostic
def test_a_counter_cell_without_history_is_unchanged() -> None:
    """The no-history path must be byte-identical to the old behaviour."""
    from lsdsk.adapters.render.tables import counter_cell

    assert counter_cell(462640) == counter_cell(462640, None)
    assert counter_cell(None)[0] == "-"
    assert counter_cell(0)[0] == "0"


@pytest.mark.os_agnostic
def test_a_healthy_machine_says_it_was_recorded_not_that_it_was_not(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path, strip_ansi: Callable[[str], str]
) -> None:
    """A drive with no errors must not read as a drive with no history.

    Found on three live hosts: every drive was recorded, every counter was zero,
    so no row was worth showing and the view fell through to "nothing has been
    recorded", which was false. Good news has to be stated as good news.
    """
    store = tmp_path / "history.json"
    run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(HEALTHY))
    assert store.exists(), "the fixture must actually record, or this proves nothing"

    output = strip_ansi(
        run(cli_runner, production_factory, "--history-file", str(store), "trend", "--replay", str(HEALTHY)).output
    )
    assert "Nothing has been recorded" not in output, "readings exist, so this claim is false"
    assert "no error counter" in output.lower() or "nothing is moving" in output.lower()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"not": "a snapshot"},
        {"schema": 2, "platform": "linux"},
        {"schema": 2, "platform": "linux", "hostname": "h"},
        {"hostname": "h", "kernel": "k", "pci": {}},
    ],
)
def test_a_file_that_is_not_a_capture_is_refused_not_described(payload: dict[str, Any], tmp_path: Path) -> None:
    """The one input a user supplies by hand must not be describable as a machine.

    Given defaults on every envelope field, an empty JSON object validated: any
    file at all replayed as a host called "unknown" with no disks and nothing
    wrong, exit 0, with the absent readings blamed on privilege. A tool whose
    first rule is never to report what it did not measure has to refuse the file
    rather than describe it.
    """
    from lsdsk.adapters.hw.snapshot import load
    from lsdsk.domain.errors import ConfigurationError

    path = tmp_path / "not-a-capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load(path)


@pytest.mark.os_agnostic
def test_every_committed_capture_still_loads() -> None:
    """The guard must reject junk without rejecting a real reading.

    Without this the previous test passes just as well with an envelope that
    refuses everything.
    """
    from lsdsk.adapters.hw.snapshot import load

    captures = sorted(FIXTURES.glob("*.json"))
    assert len(captures) >= 5, "the control is only meaningful with real captures present"
    for capture in captures:
        assert load(capture).hostname, f"{capture.name} no longer loads"


@pytest.mark.os_agnostic
def test_a_refused_capture_exits_with_the_config_error_code(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """Exit 78 says "could not run", which is what a caller has to be able to see."""
    path = tmp_path / "junk.json"
    path.write_text("{}", encoding="utf-8")
    result = run(cli_runner, production_factory, "health", "--replay", str(path), "--format", "json")
    assert result.exit_code == 78
    # Asserting on the absence of a brace tested the wrong thing: the validation
    # message itself contains one. What matters is that nothing PARSES as an
    # envelope, so a caller cannot mistake the failure for data.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)
    assert result.output.lstrip().startswith("Error:")


# --------------------------------------------------------------------------
# Where the snapshot comes from
# --------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_global_replay_reaches_record_exactly_like_the_subcommand_one(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """``--replay X record`` and ``record --replay X`` must store the same thing.

    ``record`` is the one command that WRITES, so a dropped flag here does not
    merely render the wrong machine: it files this machine's counters in the
    store under its own hostname while the caller asked for another's, and says
    it succeeded. Both forms are compared against each other rather than against
    a hand-written expectation, so the test cannot drift from what the recorder
    actually stores.
    """
    before = tmp_path / "global.json"
    after = tmp_path / "sub.json"

    run(cli_runner, production_factory, "--history-file", str(before), "--replay", str(SNAPSHOT), "record")
    run(cli_runner, production_factory, "--history-file", str(after), "record", "--replay", str(SNAPSHOT))

    def counters(store: Path) -> object:
        """Everything the recorder stored except when it stored it.

        A capture carrying no ``captured_at`` of its own falls back to the wall
        clock, which differs by microseconds between two runs and says nothing
        about which machine was read.
        """
        loaded = json.loads(store.read_text(encoding="utf-8"))
        for series in loaded["series"]:
            for reading in series["samples"]:
                reading.pop("captured_at", None)
        return loaded

    assert before.exists(), "the global --replay never reached the recorder"
    assert counters(before) == counters(after)
    # The control: the stores must not match merely because both are empty.
    assert json.loads(before.read_text(encoding="utf-8"))["series"], "nothing was recorded, so this proved nothing"


@pytest.mark.os_agnostic
def test_every_command_taking_replay_resolves_the_global_one() -> None:
    """The shape that let ``record`` drift, pinned so it cannot happen again.

    Two shapes, because the first version of this guard caught only one.

    A command that DECLARES ``--replay`` but reads its own parameter instead of
    calling ``effective_replay`` silently ignores the root group's flag. That is
    invisible to every per-command test, because each one passes the flag in the
    position that works.

    A command that declares NO ``--replay`` at all and reads the machine anyway
    has the same defect and slipped straight through, because the original guard
    skipped anything without the parameter - it was written around the shape of
    the bug it had just seen. ``snapshot`` was the one: it wrote the local
    machine into a file the caller believed held another host's capture, at exit
    0 and with no warning. Every command that reads a machine must resolve the
    global flag, whether to honour it or to refuse it.
    """
    import ast
    import inspect

    from lsdsk.adapters.cli.commands import history as history_mod
    from lsdsk.adapters.cli.commands import scan as scan_mod

    # Anything that turns a machine into an inventory, or reads the hardware
    # directly. A command calling one of these is a command --replay concerns.
    reads_a_machine = ("load_inventory(", "read_current_machine(", "collect(")

    offenders: list[str] = []
    checked = 0
    for module in (scan_mod, history_mod):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("cli_"):
                continue
            body = ast.unparse(node)
            declares = "replay" in {arg.arg for arg in node.args.args + node.args.kwonlyargs}
            reads = any(call in body for call in reads_a_machine)
            if not declares and not reads:
                continue
            checked += 1
            if "effective_replay(" not in body:
                offenders.append(f"{module.__name__}.{node.name}")

    assert checked >= 5, f"the guard inspected only {checked} commands, so it asserted almost nothing"
    assert not offenders, f"these read a machine without resolving the global --replay: {offenders}"


# --------------------------------------------------------------------------
# A store that cannot be read must survive the run that could not read it
# --------------------------------------------------------------------------


def _seed_foreign_store(path: Path) -> int:
    """Write a real store for a different machine, and return its size."""
    from lsdsk.adapters.history.store import save_history
    from lsdsk.domain.history import DiskSeries, History, Sample

    samples = tuple(
        Sample(power_on_hours=hour, captured_at=f"2020-01-{1 + hour % 27:02d}T00:00:00Z", crc_errors=hour * 3)
        for hour in range(1, 400)
    )
    history = History(
        hostname="OLD-NAME", series=(DiskSeries(identity="naa.e309336c", model="ACME X", samples=samples),)
    )
    save_history(history, path)
    return path.stat().st_size


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label", "corrupt"),
    [
        ("another machine's store", None),
        ("malformed json", '{"schema": 1, "hostname": "linux-sas-hba", "series": [ NOT JSON'),
        ("a truncated store", "{"),
    ],
)
def test_a_store_that_cannot_be_read_is_never_overwritten(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    tmp_path: Path,
    label: str,
    corrupt: str | None,
) -> None:
    """The one artefact that cannot be rebuilt from the hardware.

    Every refusal reason in ``load_history`` used to reach ``record_reading``
    through an empty ``History``, which reads exactly like "there was nothing
    here" and replaced an accumulated record with a single sample. The warning
    told the operator to point ``--history-file`` elsewhere, by which time the
    data it was protecting had already gone.
    """
    store = tmp_path / "history.json"
    if corrupt is None:
        _seed_foreign_store(store)
    else:
        store.write_text(corrupt, encoding="utf-8")
    before = store.read_bytes()
    assert before, f"{label}: nothing was seeded, so this test asserted nothing"

    result = run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))

    assert result.exit_code == 0, f"{label}: the hardware must still be diagnosed"
    assert store.read_bytes() == before, f"{label}: the unreadable store was replaced"


@pytest.mark.os_agnostic
def test_a_readable_store_is_still_written(
    cli_runner: CliRunner, production_factory: Callable[[], Any], tmp_path: Path
) -> None:
    """The control for the test above: refusing to write must not become the rule.

    Without this, deleting the whole recording path would pass the test above.
    """
    store = tmp_path / "history.json"
    result = run(cli_runner, production_factory, "--history-file", str(store), "record", "--replay", str(SNAPSHOT))
    assert result.exit_code == 0
    assert store.exists(), "a store that could be read was not written"
    assert load_history(store, hostname="linux-sas-hba").series
