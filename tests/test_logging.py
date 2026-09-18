"""Tests for the logging configuration model.

LoggingConfigModel validation is tested here. The init_logging function
is tested via CLI integration tests in test_cli_core.py.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.logging.setup import LoggingConfigModel


@pytest.mark.os_agnostic
def test_logging_config_model_allows_extra_fields() -> None:
    """Extra fields pass through for lib_log_rich RuntimeConfig."""
    parsed = LoggingConfigModel.model_validate({"service": "test", "environment": "dev", "custom_field": "value"})

    assert parsed.service == "test"
    assert parsed.environment == "dev"
    extra = parsed.model_dump(exclude={"service", "environment"}, exclude_none=True)
    assert extra == {"custom_field": "value"}


@pytest.mark.os_agnostic
def test_logging_config_model_defaults() -> None:
    """Empty input produces sensible defaults."""
    parsed = LoggingConfigModel.model_validate({})

    assert parsed.service is None
    assert parsed.environment == "prod"


@pytest.mark.os_agnostic
def test_the_theme_enum_matches_what_the_library_publishes() -> None:
    """Verify the preview's choices are the library's, not a list that drifted.

    The theme names belong to lib_log_rich, so the enum mirrors them and click
    can refuse an unknown one with the list of choices rather than leaving the
    library to fail on a missing dict key. A theme added or renamed upstream
    fails here instead of silently becoming unreachable from the command line.
    """
    from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

    from lsdsk.adapters.cli.commands.logging import LogDemoTheme

    assert {theme.value for theme in LogDemoTheme} == set(CONSOLE_STYLE_THEMES)
