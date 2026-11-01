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


def test_logdemo_warns_once_about_a_refused_variable(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's own start and the preview's start both met the variable; the reader hears it once."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    said = _said_about(["logdemo"], capfd)

    assert len(said) == 1, said


def test_an_ordinary_command_still_warns_once(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the command that starts the runtime once is not made quieter than before."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    said = _said_about(FINDINGS, capfd)

    assert len(said) == 1, said


def test_the_warning_names_the_environment_as_where_the_value_came_from(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader looking for the refused value must be sent to the environment, not to the section."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    said = _said_about(FINDINGS, capfd)

    assert "environment variable LOG_CONSOLE_LEVEL=bogus" in said[0], said


def test_two_runs_in_one_process_each_warn(capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Once per RUN, not once per process: a second invocation is a new run with a new reader."""
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "bogus")

    first = _said_about(FINDINGS, capfd)
    get_config.cache_clear()
    second = _said_about(FINDINGS, capfd)

    assert len(first) == 1, first
    assert len(second) == 1, second


def test_a_refused_key_and_a_refused_variable_each_quote_their_own_value(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With both sources refused, each warning gives its own source's reason, not the other's.

    The library reads ``LOG_CONSOLE_LEVEL`` ahead of the section, so the first refusal is the
    variable's; the key's warning used to quote that one ('LOUDER') as its own reason.
    """
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "LOUDER")

    main(["--set", "lib_log_rich.console_level=NOISY", *FINDINGS], services_factory=build_production)
    warnings = [line for line in capfd.readouterr().err.splitlines() if line.startswith("Warning: ignoring")]

    about_the_key = [line for line in warnings if "lib_log_rich.console_level=NOISY" in line]
    about_the_variable = [line for line in warnings if "LOG_CONSOLE_LEVEL=LOUDER" in line]
    assert len(about_the_key) == 1, warnings
    assert len(about_the_variable) == 1, warnings
    assert "'NOISY'" in about_the_key[0], about_the_key
    assert "LOUDER" not in about_the_key[0], about_the_key
    assert "'LOUDER'" in about_the_variable[0], about_the_variable
    assert "NOISY" not in about_the_variable[0], about_the_variable
