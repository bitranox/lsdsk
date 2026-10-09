"""The logging section and the tool's own sections refuse a single value in the same words."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.values import REASON_SECTION

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

SETUP = Path(__file__).parent.parent / "src" / "lsdsk" / "adapters" / "logging" / "setup.py"


@pytest.mark.os_agnostic
def test_the_logging_setup_spells_no_second_copy_of_the_shared_reason() -> None:
    """The sentence was a second spelling of ``REASON_SECTION``, so rewording the constant left it behind."""
    assert REASON_SECTION == "not a table", "the control: the shared reason is the text this test looks for"
    assert f'"{REASON_SECTION}"' not in SETUP.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_a_logging_section_given_as_a_value_is_refused_with_the_shared_reason(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    user_config_dir_under: Callable[[str], Path],
) -> None:
    """The behaviour the source check protects: the warning carries the shared words."""
    home = user_config_dir_under("logging-scalar")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("lib_log_rich = 3\n", encoding="utf-8")
    get_config.cache_clear()
    try:
        result = cli_runner.invoke(cli, ["info"], obj=production_factory)
    finally:
        get_config.cache_clear()

    said = " ".join(result.stderr.split())
    assert f"ignoring lib_log_rich=3: {REASON_SECTION}." in said, said
