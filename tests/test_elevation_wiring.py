"""Both ways into the interactive view ask for Administrator first, and honour every way to say no.

The question itself is decided in ``adapters/hw/elevation.py`` and tested there.
This file holds the wiring around it: that a bare run and ``lsdsk tui`` both ask
before they read anything, that an elevated copy's exit code becomes this run's
without a view being drawn, that a refusal reaches the view, and that
``--no-elevate``, ``display.auto_elevate = false`` and the relaunched copy's own
marker each turn the asking off. The port is substituted through the services
container, the seam every command resolves its collaborators through.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.tunables import DisplaySettings, get_display_settings
from lsdsk.composition import build_production
from lsdsk.domain.elevation import Elevation, ElevationState

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

FIXTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
RELAUNCHED = Elevation(state=ElevationState.RELAUNCHED, exit_code=5)
DECLINED = Elevation(state=ElevationState.DECLINED)


class AskRecorder:
    """The elevate port, answering as told and remembering what it was asked."""

    def __init__(self, outcome: Elevation) -> None:
        """Answer every question with ``outcome``."""
        self.outcome = outcome
        self.asked: list[tuple[Path | None, bool]] = []

    def __call__(self, replay: Path | None, *, wanted: bool) -> Elevation:
        """Record the question and answer it."""
        self.asked.append((replay, wanted))
        return self.outcome


def _services_with(elevate: AskRecorder) -> Callable[[], AppServices]:
    services = replace(build_production(), elevate=elevate)
    return lambda: services


def _at_a_terminal(monkeypatch: pytest.MonkeyPatch, *, sitting: bool = True) -> None:
    """Answer the router's terminal question, the one external edge it names for a test.

    Patched where the router asks it rather than on ``sys.stdout``: CliRunner
    swaps the streams during ``invoke``, so a patch on them is gone by the time
    the question is asked.
    """
    from lsdsk.adapters.cli.commands import scan

    monkeypatch.setattr(scan, "somebody_is_sitting_at_it", lambda: sitting)


class ViewRecorder:
    """Stands in for the app class: records the elevation it was built with, opens nothing."""

    built: list[Elevation | None] = []  # noqa: RUF012 - one list per test, reset by the fixture

    def __init__(self, *_args: Any, elevation: Elevation | None = None, **_kwargs: Any) -> None:
        """Remember what the view would have been told."""
        ViewRecorder.built.append(elevation)

    def run(self) -> None:
        """The real one blocks on the terminal."""


@pytest.fixture
def view(monkeypatch: pytest.MonkeyPatch) -> type[ViewRecorder]:
    """The app class replaced by the recorder for the length of one test."""
    ViewRecorder.built = []
    monkeypatch.setattr("lsdsk.adapters.tui.LsdskApp", ViewRecorder)
    return ViewRecorder


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", [[], ["tui"]], ids=["bare run", "tui command"])
def test_an_accepted_prompt_hands_back_the_elevated_copys_code_and_draws_nothing(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, view: type[ViewRecorder], command: list[str]
) -> None:
    _at_a_terminal(monkeypatch)
    asked = AskRecorder(RELAUNCHED)

    result = cli_runner.invoke(cli, ["--no-record", "--replay", str(FIXTURE), *command], obj=_services_with(asked))

    assert result.exit_code == 5, result.output
    assert asked.asked, "the run never asked"
    assert view.built == [], "a view was drawn although the elevated copy already showed it"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", [[], ["tui"]], ids=["bare run", "tui command"])
def test_a_declined_prompt_opens_the_view_and_tells_it_so(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, view: type[ViewRecorder], command: list[str]
) -> None:
    _at_a_terminal(monkeypatch)

    cli_runner.invoke(
        cli, ["--no-record", "--replay", str(FIXTURE), *command], obj=_services_with(AskRecorder(DECLINED))
    )

    assert view.built == [DECLINED]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("options", "wanted"),
    [
        pytest.param([], True, id="asks by default"),
        pytest.param(["--no-elevate"], False, id="--no-elevate"),
        pytest.param(["--elevated-relaunch"], False, id="the relaunched copy never asks again"),
        pytest.param(["--set", "display.auto_elevate=false"], False, id="display.auto_elevate"),
    ],
)
def test_every_way_to_say_no_reaches_the_question(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, view: type[ViewRecorder], options: list[str], wanted: bool
) -> None:
    del view
    _at_a_terminal(monkeypatch)
    asked = AskRecorder(Elevation(state=ElevationState.NOT_ASKED))

    cli_runner.invoke(cli, ["--no-record", "--replay", str(FIXTURE), *options], obj=_services_with(asked))

    assert asked.asked == [(FIXTURE, wanted)]


@pytest.mark.os_agnostic
def test_nothing_is_asked_where_nobody_can_answer(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, view: type[ViewRecorder]
) -> None:
    """Off a terminal the bare run prints the page; a prompt there has nobody to read it."""
    del view
    _at_a_terminal(monkeypatch, sitting=False)
    asked = AskRecorder(RELAUNCHED)

    result = cli_runner.invoke(cli, ["--no-record", "--replay", str(FIXTURE)], obj=_services_with(asked))

    assert asked.asked == []
    assert result.exit_code in (0, 1), result.output


@pytest.mark.os_agnostic
def test_the_relaunch_marker_is_not_offered_in_help(cli_runner: CliRunner) -> None:
    shown = cli_runner.invoke(cli, ["--help"], obj=build_production).output

    assert "--no-elevate" in shown
    assert "--elevated-relaunch" not in shown


@pytest.mark.os_agnostic
def test_the_shipped_configuration_asks() -> None:
    import tomllib

    shipped = (
        Path(__file__).parents[1] / "src" / "lsdsk" / "adapters" / "config" / "defaultconfig.d" / "70-display.toml"
    )

    assert tomllib.loads(shipped.read_text(encoding="utf-8"))["display"]["auto_elevate"] is True
    assert DisplaySettings().auto_elevate is True
    assert get_display_settings(Config({"display": {"auto_elevate": False}}, {})).auto_elevate is False
