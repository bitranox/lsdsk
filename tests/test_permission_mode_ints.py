"""An int permission mode that could be octal digits is refused; text and 8/9-bearing ints keep working.

lib_layered_config 7.1 reads an unquoted ``.env`` value such as ``755`` as the int 755, which is
ambiguous: octal digits or a numeric mode (755 decimal is 0o1363)? lsdsk never lets a configured
value stop a run, so "refused" means the shipped default stays in force and the refusal is recorded
and reported, naming the text spelling to use instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from lib_layered_config import Config

from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.permissions import PermissionReading, parse_mode, read_permission_defaults

if TYPE_CHECKING:
    from pathlib import Path

_HINT = "write it as text: 0755 or 0o755"
_KEY = "LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__APP_DIRECTORY"


def _reading_of(app_directory: object) -> PermissionReading:
    section = {"lib_layered_config": {"default_permissions": {"app_directory": app_directory}}}
    return read_permission_defaults(Config(section, {}))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("ambiguous", [755, 644, 420, 0])
def test_an_int_of_octal_digits_only_is_refused_with_the_text_spelling(ambiguous: int) -> None:
    reading = _reading_of(ambiguous)

    assert reading.defaults.app_directory == 0o755, "the shipped default stays in force"
    assert [(r.dotted, r.raw) for r in reading.rejected] == [
        ("lib_layered_config.default_permissions.app_directory", str(ambiguous))
    ]
    assert _HINT in reading.rejected[0].reason


@pytest.mark.os_agnostic
def test_an_int_with_an_eight_or_nine_stays_a_numeric_mode() -> None:
    reading = _reading_of(0o750)

    assert reading.defaults.app_directory == 0o750
    assert reading.rejected == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", ["0755", "755", "0o755"])
def test_text_is_octal(text: str) -> None:
    reading = _reading_of(text)

    assert reading.defaults.app_directory == 0o755
    assert reading.rejected == ()
    assert parse_mode(text, 0o700) == 0o755


@pytest.mark.os_agnostic
def test_a_bool_is_still_refused() -> None:
    reading = _reading_of(True)

    assert reading.defaults.app_directory == 0o755
    assert len(reading.rejected) == 1


@pytest.mark.os_agnostic
def test_parse_mode_does_not_take_an_ambiguous_int_for_a_mode() -> None:
    assert parse_mode(755, 0o700) == 0o700
    assert parse_mode(493, 0o700) == 0o755


@pytest.mark.os_agnostic
def test_the_shipped_default_config_loads_without_a_refusal(clear_config_cache: None) -> None:
    reading = read_permission_defaults(get_config())

    assert reading.rejected == ()
    assert reading.defaults.app_directory == 0o755
    assert reading.defaults.app_file == 0o644


@pytest.mark.os_agnostic
def test_a_dotenv_755_is_refused_and_0755_is_octal(tmp_path: Path, clear_config_cache: None) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{_KEY}=755\n", encoding="utf-8")
    refused = read_permission_defaults(get_config(dotenv_path=str(dotenv)))

    assert refused.defaults.app_directory == 0o755
    assert [r.raw for r in refused.rejected] == ["755"]
    assert _HINT in refused.rejected[0].reason

    get_config.cache_clear()
    dotenv.write_text(f"{_KEY}=0750\n", encoding="utf-8")
    accepted = read_permission_defaults(get_config(dotenv_path=str(dotenv)))

    assert accepted.defaults.app_directory == 0o750
    assert accepted.rejected == ()
