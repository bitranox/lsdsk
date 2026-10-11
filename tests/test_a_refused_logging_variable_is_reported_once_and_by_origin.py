"""A refused ``LOG_*`` value warns once per run, and the warning says it came from the environment.

``lsdsk logdemo`` starts the logging runtime twice - the run's own, then the
preview's - and both starts read the same refused variable, so the one warning
was printed twice. The sentence also read "Using the [lib_log_rich] settings"
without ever saying the refused value came from the environment rather than
from that section, which is the one place a reader would look for it.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.cli.main import main
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

FINDINGS = ["--replay", "tests/fixtures/hw/linux-minimal.json", "--no-record", "findings"]


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> None:
    """The loader caches per process; drop it so each example reads its own environment."""
    get_config.cache_clear()


def _said_about(argv: list[str], capfd: pytest.CaptureFixture[str]) -> list[str]:
    main(argv, services_factory=build_production)
    return [line for line in capfd.readouterr().err.splitlines() if "ignoring" in line and "LOG_CONSOLE_LEVEL" in line]


@pytest.mark.os_agnostic
def test_logdemo_warns_once_about_a_refused_variable(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's own start and the preview's start both met the variable; the reader hears it once."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    said = _said_about(["logdemo"], capfd)

    assert len(said) == 1, said


@pytest.mark.os_agnostic
def test_an_ordinary_command_still_warns_once(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the command that starts the runtime once is not made quieter than before."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    said = _said_about(FINDINGS, capfd)

    assert len(said) == 1, said


@pytest.mark.os_agnostic
def test_the_warning_names_the_environment_as_where_the_value_came_from(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader looking for the refused value must be sent to the environment, not to the section."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    said = _said_about(FINDINGS, capfd)

    assert "environment variable LOG_CONSOLE_LEVEL=bogus" in said[0], said


@pytest.mark.os_agnostic
def test_two_runs_in_one_process_each_warn(capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Once per RUN, not once per process: a second invocation is a new run with a new reader."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    first = _said_about(FINDINGS, capfd)
    get_config.cache_clear()
    second = _said_about(FINDINGS, capfd)

    assert len(first) == 1, first
    assert len(second) == 1, second
