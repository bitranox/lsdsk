"""The testing composition's logging runtime, and the per-test reset of logging state.

Every command that reports a job binds context onto the process-global lib_log_rich
runtime, so the in-memory logging adapter must leave a live runtime behind or
``build_testing()`` cannot run those commands at all. That runtime, and the root logger
that production ``init_logging`` changes, are process-global, so the suite puts both back
after every test; the ``no_logging_runtime_outlives_its_test`` fixture hands a test the
same restore step, so the reset is proved inside one test instead of by test order.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lib_log_rich.runtime
import pytest
from lib_layered_config import Config

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.logging.setup import init_logging
from lsdsk.composition import build_testing

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.composition import AppServices


#: Commands that bind job context onto the runtime, as one CLI argument list each.
_MINIMAL = str(Path(__file__).parent / "fixtures" / "hw" / "linux-minimal.json")
_BINDING_COMMANDS = [["info"], ["config"], ["--replay", _MINIMAL, "trend"]]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", _BINDING_COMMANDS)
def test_a_command_that_binds_runs_under_build_testing(cli_runner: CliRunner, command: list[str]) -> None:
    """Each of these calls ``lib_log_rich.runtime.bind(...)``, which raises unless a runtime is live."""
    result = cli_runner.invoke(cli_mod.cli, command, obj=build_testing)

    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code == 0, result.output


@pytest.mark.os_agnostic
def test_the_reset_shuts_down_the_runtime_a_command_started(
    cli_runner: CliRunner, no_logging_runtime_outlives_its_test: Callable[[], None]
) -> None:
    result = cli_runner.invoke(cli_mod.cli, ["info"], obj=build_testing)
    assert result.exit_code == 0, result.output
    assert lib_log_rich.runtime.is_initialised() is True

    no_logging_runtime_outlives_its_test()

    assert lib_log_rich.runtime.is_initialised() is False


@pytest.mark.os_agnostic
def test_the_reset_restores_the_root_logger_a_production_init_changed(
    no_logging_runtime_outlives_its_test: Callable[[], None],
) -> None:
    """Production ``init_logging`` changes the root logger, and the reset puts it back.

    It attaches a stdlib handler and raises the level; ``runtime.shutdown()`` undoes
    neither, so without the reset a later test's stdlib records go to a handler whose
    runtime is gone, depending on which test ran first.
    """
    root = logging.getLogger()
    before = (list(root.handlers), root.level, root.propagate)

    init_logging(Config({}, {}))
    assert (list(root.handlers), root.level, root.propagate) != before

    no_logging_runtime_outlives_its_test()

    assert (list(root.handlers), root.level, root.propagate) == before


def _deploy_nothing(*_args: Any, **_kwargs: Any) -> list[Path]:
    return []


#: Each services-building fixture, with how to get one AppServices out of it.
_SERVICE_FIXTURES: dict[str, Callable[[Any], AppServices]] = {
    "inject_config": lambda make: make({})(),
    "inject_config_with_profile_capture": lambda make: make(Config({}, {}), [])(),
    "inject_deploy_with_profile_capture": lambda make: make(Path("config.toml"), [])(),
    "inject_generate_examples": lambda make: make(_deploy_nothing)(),
    "inject_deploy_configuration": lambda make: make(_deploy_nothing)(),
    "config_cli_context": lambda make: make({})(),
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fixture_name", _SERVICE_FIXTURES)
def test_the_service_fixtures_log_through_the_quiet_runtime(request: pytest.FixtureRequest, fixture_name: str) -> None:
    """The fixtures that only swap a configuration or deploy seam use the testing runtime.

    Production ``init_logging`` queues INFO lines onto standard error from a worker
    thread, so a test asserting on what a command printed would pass or fail by timing.
    """
    services = _SERVICE_FIXTURES[fixture_name](request.getfixturevalue(fixture_name))

    assert services.init_logging is build_testing().init_logging
