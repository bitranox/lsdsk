"""The two ways logging start-up ends that no refused VALUE exercises.

A ``[lib_log_rich]`` section given as a single value has no keys to fall back
on, so the whole shipped section stands in for it. And when the library refuses
every attempt - the configured table, the table with the offender put back, the
variables set aside, the shipped table itself - the fault is this tool's, and
the library's own refusal is what escapes rather than a silent run with no
logging at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import lib_log_rich.runtime
import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config, shipped_section
from lsdsk.adapters.logging.setup import start_runtime

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

CAPTURE = "tests/fixtures/hw/linux-minimal.json"


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> None:
    """The loader caches per process; drop it so each test reads its own file."""
    get_config.cache_clear()


@pytest.mark.os_agnostic
def test_a_logging_section_that_is_not_a_table_is_replaced_by_the_shipped_one_with_a_warning(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    user_config_dir_under: Callable[[str], Path],
) -> None:
    """``lib_log_rich = "x"`` has no keys, so the shipped logging settings run the process and say so."""
    config = user_config_dir_under("cfg") / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('lib_log_rich = "x"\n', encoding="utf-8")

    result = cli_runner.invoke(
        cli, ["--replay", CAPTURE, "--no-record", "disks", "--format", "json"], obj=production_factory
    )

    said = " ".join(result.stderr.split())
    assert "Warning: ignoring lib_log_rich=x: not a table. Using the shipped logging settings." in said, said
    assert said.count("lib_log_rich") >= 1
    assert result.exit_code in (0, 1), said
    assert lib_log_rich.runtime.is_initialised(), "the replacement left the run with no logging at all"


@pytest.mark.os_agnostic
def test_settings_the_library_refuses_even_when_shipped_are_raised_not_swallowed() -> None:
    """When no attempt can start the runtime, the refusal reaches the caller instead of a run with no logging."""
    refused = {**shipped_section("lib_log_rich"), "console_level": "NOPE"}
    assert not lib_log_rich.runtime.is_initialised()

    with pytest.raises(ValueError, match="NOPE"):
        start_runtime(refused, refused)

    assert not lib_log_rich.runtime.is_initialised()


@pytest.mark.os_agnostic
def test_the_same_start_succeeds_once_the_shipped_settings_are_sound() -> None:
    """The control for the arm above: it is the shipped table being refused that raises, not the harness."""
    shipped = shipped_section("lib_log_rich")
    try:
        notes = start_runtime({**shipped, "console_level": "NOPE"}, shipped)
        assert len(notes) == 1
        assert "console_level=NOPE" in notes[0]
        assert lib_log_rich.runtime.is_initialised()
    finally:
        if lib_log_rich.runtime.is_initialised():
            lib_log_rich.runtime.shutdown()
