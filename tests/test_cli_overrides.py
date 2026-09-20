"""CLI --set override integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result


@pytest.mark.os_agnostic
def test_when_set_override_is_passed_config_reflects_change(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify --set override is visible in config command output."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "console_level": "INFO",
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "lib_log_rich.console_level=DEBUG", "config", "--section", "lib_log_rich"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "DEBUG" in result.output


@pytest.mark.os_agnostic
def test_when_multiple_set_overrides_are_passed_all_apply(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify multiple --set options all apply."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "console_level": "INFO",
                "force_color": False,
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--set",
            "lib_log_rich.console_level=DEBUG",
            "--set",
            "lib_log_rich.force_color=true",
            "config",
            "--section",
            "lib_log_rich",
        ],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "DEBUG" in result.output


@pytest.mark.os_agnostic
def test_when_set_override_has_nested_key_it_works(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify nested key override (e.g., SECTION.SUB.KEY=VALUE) works."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "payload_limits": {
                    "message_max_chars": 4096,
                },
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "lib_log_rich.payload_limits.message_max_chars=8192", "config", "--format", "json"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "8192" in result.stdout


def _panel_prose(rendered: str) -> str:
    """The prose of a rich-click error panel, freed of the box that wraps it.

    The panel is drawn to the console width, and a phrase that does not fit is
    split across two of its lines - with a border glyph and a run of padding
    between the halves.

    Collapsing whitespace alone does not rejoin it, which is what this replaced:
    the border sits between the halves, so the normalised text reads
    ``must | contain '='``. The borders come out first.

    The suite deletes ``COLUMNS`` for isolation, so no ordinary test here can
    meet a narrow console. The one below sets it back for its own invocation and
    is the only place this helper is shown doing anything.
    """
    return " ".join(rendered.replace("\u2502", " ").split())


@pytest.mark.os_agnostic
def test_when_set_override_is_invalid_it_shows_usage_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify invalid --set format shows usage error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "invalid_no_equals", "config"],
        obj=production_factory,
    )

    assert result.exit_code != 0
    assert "must contain '='" in _panel_prose(result.stderr), result.output


@pytest.mark.os_agnostic
def test_the_refusal_is_still_readable_when_the_panel_has_to_wrap_it(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for _panel_prose: a width where the phrase really is split.

    Without this the helper is indistinguishable from the raw string it wraps,
    because the suite deletes COLUMNS and every other test here runs at the
    default width, where nothing wraps. The first assertion is what makes the
    second one mean something: it requires the panel to have broken the phrase.
    """
    monkeypatch.setenv("COLUMNS", "50")
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "invalid_no_equals", "config"],
        obj=production_factory,
    )

    assert "must contain '='" not in result.stderr, "the control: this width no longer wraps the phrase"
    assert "must contain '='" in _panel_prose(result.stderr), result.stderr


@pytest.mark.os_agnostic
def test_when_set_override_has_no_dot_it_shows_usage_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify --set without dot in key shows usage error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "nodot=value", "config"],
        obj=production_factory,
    )

    assert result.exit_code != 0


@pytest.mark.os_agnostic
def test_when_set_override_is_empty_string_it_shows_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify --set with empty string shows error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "", "config"],
        obj=production_factory,
    )

    assert result.exit_code != 0


@pytest.mark.os_agnostic
def test_when_no_set_overrides_config_is_unchanged(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify no --set leaves config unchanged."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "console_level": "WARNING",
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config", "--section", "lib_log_rich"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "WARNING" in result.output
