"""A refused value is never quoted as live terminal control."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.values import REASON_FLAG, RejectedValue

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

ESCAPE_SEQUENCE = "\x1b]0;pwned\x07"


@pytest.mark.os_agnostic
def test_a_refused_value_with_terminal_controls_is_shown_as_escapes() -> None:
    r"""The warning quoted an ESC/OSC sequence raw, so a configured value could retitle the terminal."""
    sentence = RejectedValue(dotted="display.x", raw=ESCAPE_SEQUENCE, reason=REASON_FLAG, used="true").as_sentence()

    assert "\x1b" not in sentence and "\x07" not in sentence, repr(sentence)
    assert r"\x1b]0;pwned\x07" in sentence


@pytest.mark.os_agnostic
def test_a_refused_set_value_with_terminal_controls_never_reaches_standard_error(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """The same, through the command line that carries it."""
    result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", f"display.expand_virtual={ESCAPE_SEQUENCE}", "config", "--format", "json"],
        obj=production_factory,
    )

    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.stderr, repr(result.stderr)
    assert r"\x1b]0;pwned" in result.stderr
