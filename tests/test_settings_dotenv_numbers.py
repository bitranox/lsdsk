"""Digit-only .env values reach every text setting as the text the operator wrote.

lib_layered_config's dotenv and environment layers turn an unquoted value into an int whenever that
number reads back as the same text (``4711`` stays ``4711``). A text setting must therefore accept
such a number and keep its digits; otherwise a perfectly ordinary ``.env`` line stops logging
setup with ``Input should be a valid string``.

These tests load a real ``.env`` through the real loader, so a change in the library's coercion
shows up here rather than on a user's machine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError

from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.logging.setup import LoggingConfigModel

if TYPE_CHECKING:
    from pathlib import Path

    from lib_layered_config import Config


def _load_dotenv(tmp_path: Path, *lines: str) -> Config:
    dotenv = tmp_path / ".env"
    dotenv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return get_config(dotenv_path=str(dotenv))


def _logging_section(config: Config) -> dict[str, Any]:
    return cast("dict[str, Any]", config.get("lib_log_rich", default={}))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("env_key", "attr"),
    [("LIB_LOG_RICH__SERVICE", "service"), ("LIB_LOG_RICH__ENVIRONMENT", "environment")],
    ids=["service", "environment"],
)
def test_digit_only_dotenv_value_reaches_a_logging_text_setting_as_its_text(
    tmp_path: Path, clear_config_cache: None, env_key: str, attr: str
) -> None:
    loaded = _logging_section(_load_dotenv(tmp_path, f"{env_key}=4711"))
    # Premise: the library hands the value over as a number. Without it the test proves nothing.
    assert isinstance(loaded[attr], int)

    assert getattr(LoggingConfigModel.model_validate(loaded), attr) == "4711"


@pytest.mark.os_agnostic
def test_a_numeric_setting_passes_through_as_a_number(tmp_path: Path, clear_config_cache: None) -> None:
    loaded = _logging_section(_load_dotenv(tmp_path, "LIB_LOG_RICH__QUEUE_MAXSIZE=512"))

    assert LoggingConfigModel.model_validate(loaded).model_dump()["queue_maxsize"] == 512


@pytest.mark.os_agnostic
def test_a_value_that_is_not_text_is_still_refused(clear_config_cache: None) -> None:
    with pytest.raises(ValidationError, match="service"):
        LoggingConfigModel.model_validate({"service": ["not", "text"]})
