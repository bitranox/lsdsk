"""``logdemo`` starts its own runtime, and a refused ``LOG_*`` variable must not end it."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import lib_log_rich.runtime
import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from click.testing import CliRunner


@pytest.fixture(autouse=True)
def no_runtime_outlives_its_test() -> Iterator[None]:
    """Leave neither a cached configuration nor a running logging runtime behind."""
    get_config.cache_clear()
    yield
    get_config.cache_clear()
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.shutdown()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("LOG_CONSOLE_LEVEL", "bogus"),
        ("LOG_CONSOLE_FORMAT_PRESET", "zzz"),
        ("LOG_CONSOLE_STREAM", "bogus"),
        ("LOG_BACKEND_LEVEL", "bogus"),
        ("LOG_CONSOLE_STYLES", "ERRORx=bold"),
        ("LOG_RING_BUFFER_SIZE", "0"),
    ],
)
def test_a_refused_log_variable_is_set_aside_by_logdemo_too(
    variable: str,
    value: str,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LOG_CONSOLE_LEVEL=bogus lsdsk logdemo`` left 70: the demo bypassed the fallback every run has."""
    monkeypatch.setenv(variable, value)

    result = cli_runner.invoke(cli, ["logdemo"], obj=production_factory)

    said = " ".join((result.stderr or "").split())
    assert result.exit_code == 0, f"left {result.exit_code}: {result.exception!r} {said[-300:]}"
    assert "Log demo completed" in result.stdout
    assert f"ignoring {variable}={value}" in said, said
    assert os.environ.get(variable) == value, "the variable was not put back"
