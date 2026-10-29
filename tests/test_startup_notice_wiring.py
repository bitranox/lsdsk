"""Where the startup notice is paused, resumed and stopped on the way into the view.

The notice itself is tested in ``test_startup_notice.py``. This file holds the
choreography around it: it gives way to every prompt (the shortcut offer, the
elevation announcement), runs again while the machine is read, and is gone
before the view takes the screen; it is stopped as soon as click knows the run
is a subcommand other than ``tui``, and on every exit from the entry point.
The notice is substituted through the services container, like the elevation
port beside it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from lsdsk import entry
from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.commands import scan
from lsdsk.adapters.cli.commands.history import analyse as real_analyse
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.memory import StartupNoticeInMemory, init_logging_in_memory
from lsdsk.composition import build_production
from lsdsk.domain.elevation import Elevation, ElevationState
from lsdsk.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.adapters.cli.commands.scan import Analysis
    from lsdsk.adapters.config.history import HistorySettings
    from lsdsk.composition import AppServices
    from lsdsk.domain.enums import OutputFormat

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
NOT_ASKED = Elevation(state=ElevationState.NOT_ASKED)
NO_OFFER = ["--set", "display.offer_desktop_shortcut=false"]


def _services_writing_to(journal: list[str]) -> Callable[[], AppServices]:
    """Production services whose notice and elevation port write into one journal."""

    def elevate(replay: Path | None, *, wanted: bool) -> Elevation:
        del replay, wanted
        journal.append("elevate")
        return NOT_ASKED

    services = replace(build_production(), elevate=elevate, startup=StartupNoticeInMemory(journal))
    return lambda: services


def _at_a_terminal(monkeypatch: pytest.MonkeyPatch, *, sitting: bool = True) -> None:
    """Answer the router's terminal question where it asks it (CliRunner swaps the streams)."""
    monkeypatch.setattr(scan, "somebody_is_sitting_at_it", lambda: sitting)


def _view_writing_to(journal: list[str]) -> type:
    """An app class that records being built and being run, and opens nothing."""

    class JournalView:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            journal.append("view built")

        def run(self) -> None:
            journal.append("view runs")

    return JournalView


@pytest.mark.parametrize("command", [[], ["tui"]], ids=["bare run", "tui command"])
def test_the_notice_gives_way_to_the_question_runs_during_the_scan_and_is_gone_before_the_view(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    journal: list[str] = []
    _at_a_terminal(monkeypatch)
    monkeypatch.setattr("lsdsk.adapters.tui.LsdskApp", _view_writing_to(journal))

    result = cli_runner.invoke(
        cli, ["--no-record", *NO_OFFER, "--replay", str(FIXTURE), *command], obj=_services_writing_to(journal)
    )

    assert journal == ["pause", "elevate", "resume", "view built", "stop", "view runs"], result.output


def test_the_notice_gives_way_to_the_shortcut_offer_too() -> None:
    journal: list[str] = []

    def offer(display: DisplaySettings) -> None:
        del display
        journal.append("offer")

    def report(
        replay: Path | None,
        *,
        settings: HistorySettings | None = None,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
        display: DisplaySettings | None = None,
    ) -> None:
        del replay, settings, thresholds, display
        journal.append("report")

    def analyse(
        replay: Path | None,
        output_format: OutputFormat,
        settings: HistorySettings,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
    ) -> Analysis:
        journal.append("analyse")
        return real_analyse(replay, output_format, settings, thresholds)

    def elevate(replay: Path | None, *, wanted: bool) -> Elevation:
        del replay, wanted
        journal.append("elevate")
        return NOT_ASKED

    # The view binds onto the logging runtime the CLI's root group starts.
    init_logging_in_memory(Config({}, {}))
    seams = scan.Collaborators(
        report=report,
        open_view=_view_writing_to(journal),
        analyse=analyse,
        elevate=elevate,
        offer=offer,
        already_elevated=lambda: False,
        startup=StartupNoticeInMemory(journal),
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scan, "somebody_is_sitting_at_it", lambda: True)
        with pytest.raises(SystemExit) as departure:
            scan.run_default_view(FIXTURE, display=DisplaySettings(), seams=seams)

    # The run reached the view and left with the findings' code, not a crash on the way.
    assert isinstance(departure.value.code, int)

    assert journal == ["pause", "offer", "pause", "elevate", "resume", "analyse", "view built", "stop", "view runs"]


def test_a_subcommand_other_than_the_view_stops_the_notice_once_click_has_parsed_the_run(
    cli_runner: CliRunner,
) -> None:
    journal: list[str] = []

    cli_runner.invoke(cli, ["--no-record", "--replay", str(FIXTURE), "disks"], obj=_services_writing_to(journal))

    assert journal == ["stop"]


def test_a_bare_run_nobody_sits_at_stops_the_notice_before_the_page_is_printed(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal: list[str] = []
    _at_a_terminal(monkeypatch, sitting=False)

    cli_runner.invoke(cli, ["--no-record", *NO_OFFER, "--replay", str(FIXTURE)], obj=_services_writing_to(journal))

    assert journal == ["stop"]


def test_the_entry_point_starts_the_notice_and_stops_it_however_the_run_ends() -> None:
    journal: list[str] = []
    seen: list[list[str]] = []

    def start_notice(argv: list[str]) -> StartupNoticeInMemory:
        seen.append(list(argv))
        return StartupNoticeInMemory(journal)

    code = entry.main(["--version"], start_notice=start_notice)

    assert code == 0
    assert seen == [["--version"]]
    assert journal[0] == "start"
    assert journal[-1] == "stop"


def test_the_entry_point_hands_its_notice_to_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI stops the line the entry point started, as soon as click knows the run is ``info``.

    Without the hand-over the CLI would stop a notice of its own and the entry
    point's line would run on through the whole command, stopped only by the
    entry point's ``finally`` on the way out.
    """
    journal: list[str] = []

    code = entry.main(["info"], start_notice=lambda argv: StartupNoticeInMemory(journal))

    assert code == 0, capsys.readouterr().err
    assert journal == ["start", "stop", "stop"]


def test_an_interrupt_while_the_entry_point_decides_on_the_notice_leaves_130() -> None:
    def interrupted(argv: list[str]) -> StartupNoticeInMemory:
        del argv
        raise KeyboardInterrupt

    try:
        code = entry.main(["info"], start_notice=interrupted)
    except KeyboardInterrupt:
        pytest.fail("the interrupt escaped the entry point, which prints a traceback")

    assert code == 130
