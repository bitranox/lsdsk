"""Every ``[lib_log_rich]`` setting the library refuses is put back with its OWN reason, and no other.

lib_log_rich stops at the first setting it cannot use, so one refusal names one
setting. Putting back only that one and then falling to the shipped table whole
discarded every valid setting the run had changed - ``console_theme = "classic"``
came back as "Using dark" - and every warning quoted the first refusal, so
``backend_level=NOPE`` was reported with ``console_level``'s reason. Each
refusal now puts back the settings it names, under its own words, until the
runtime starts.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import lib_log_rich.runtime
import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config, shipped_section
from lsdsk.adapters.logging.setup import start_runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from click.testing import CliRunner

    from lsdsk.composition import AppServices

CAPTURE = "tests/fixtures/hw/linux-minimal.json"


@pytest.fixture(autouse=True)
def no_logging_variable_reaches_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``LOG_*`` variable from the developer's shell would be read ahead of every setting here."""
    for name in [name for name in os.environ if name.startswith("LOG_")]:
        monkeypatch.delenv(name)
    get_config.cache_clear()


def _started_with(overrides: Mapping[str, object]) -> list[str]:
    shipped = shipped_section("lib_log_rich")
    notes = start_runtime({**shipped, **overrides}, shipped)
    assert lib_log_rich.runtime.is_initialised(), "the run was left with no logging at all"
    return notes


def _about(notes: list[str], key: str) -> list[str]:
    return [note for note in notes if f"lib_log_rich.{key}=" in note]


def test_a_valid_setting_survives_two_refused_ones() -> None:
    """Two refused levels are reported, each with its own value's reason; the valid theme stays."""
    notes = _started_with({"console_level": "LOUDER", "backend_level": "NOPE", "console_theme": "classic"})

    assert len(notes) == 2, notes
    assert not _about(notes, "console_theme"), notes
    (console,) = _about(notes, "console_level")
    (backend,) = _about(notes, "backend_level")
    assert "'LOUDER'" in console, console
    assert "NOPE" not in console.split(": ", 1)[1], console
    assert "backend_level: Unknown log level: 'NOPE'" in backend, backend
    assert "LOUDER" not in backend, backend


def test_three_refused_settings_each_carry_their_own_reason() -> None:
    """Three refusals in a row, three warnings, none quoting another setting's reason."""
    notes = _started_with({"console_level": "LOUDER", "backend_level": "NOPE", "console_theme": "nosuchtheme"})

    assert len(notes) == 3, notes
    (console,) = _about(notes, "console_level")
    (backend,) = _about(notes, "backend_level")
    (theme,) = _about(notes, "console_theme")
    assert "'LOUDER'" in console, console
    assert "'NOPE'" in backend, backend
    assert "'nosuchtheme'" in theme, theme
    for note, foreign in ((console, ("NOPE", "nosuchtheme")), (backend, ("LOUDER", "nosuchtheme"))):
        assert not any(word in note for word in foreign), note
    assert "LOUDER" not in theme, theme
    assert "Unknown log level: 'NOPE'" not in theme, theme


def test_a_valid_setting_survives_a_refused_one_behind_a_refused_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the variable set aside, the settings are put back one refusal at a time too.

    The library reads ``LOG_CONSOLE_LEVEL`` first, so the first refusals are the variable's;
    once it is set aside the refused settings come next, and the valid theme stays.
    """
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "SHOUT")

    notes = _started_with({"console_level": "LOUDER", "backend_level": "NOPE", "console_theme": "classic"})

    assert len(notes) == 3, notes
    assert not _about(notes, "console_theme"), notes
    (console,) = _about(notes, "console_level")
    (backend,) = _about(notes, "backend_level")
    (variable,) = [note for note in notes if "environment variable LOG_CONSOLE_LEVEL=SHOUT" in note]
    assert "'LOUDER'" in console, console
    assert "SHOUT" not in console, console
    assert "'NOPE'" in backend, backend
    assert "'SHOUT'" in variable, variable


def test_one_refused_setting_beside_a_valid_one_is_reported_alone() -> None:
    """The control: the single-refusal case was already right and stays so."""
    notes = _started_with({"backend_level": "NOPE", "console_theme": "classic"})

    assert len(notes) == 1, notes
    assert "backend_level: Unknown log level: 'NOPE'" in notes[0], notes


def test_a_refusal_naming_no_setting_still_falls_back_and_quotes_only_itself() -> None:
    """A value too large for a C call names no field; it falls back with the library's own words."""
    notes = _started_with({"ring_buffer_size": 10**27})

    assert len(notes) == 1, notes
    assert "lib_log_rich.ring_buffer_size=" in notes[0], notes


def test_the_command_line_keeps_the_valid_theme_through_a_config_file(
    cli_runner: CliRunner,
    production_factory: Callable[[], AppServices],
    user_config_dir_under: Callable[[str], Path],
) -> None:
    """The reported reproduction, through the real command and a real ``config.toml``."""
    config = user_config_dir_under("cfg") / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[lib_log_rich]\nconsole_level = "LOUDER"\nbackend_level = "NOPE"\nconsole_theme = "classic"\n',
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        cli, ["--replay", CAPTURE, "--no-record", "--no-elevate", "info"], obj=production_factory
    )

    warnings = [line for line in result.stderr.splitlines() if line.startswith("Warning: ignoring lib_log_rich.")]
    assert len(warnings) == 2, result.stderr
    assert not [line for line in warnings if "console_theme" in line], warnings
    assert [line for line in warnings if "backend_level=NOPE" in line and "'NOPE'" in line], warnings
