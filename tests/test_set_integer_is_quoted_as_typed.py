"""A refused ``--set`` integer is quoted with the digits that were typed."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.overrides import coerce_value

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner


@pytest.mark.os_agnostic
def test_an_integer_too_big_for_a_float_to_hold_is_kept_as_typed() -> None:
    """``orjson`` turned 99999999999999999999999 into the float 1e+23, which no reader typed."""
    value = coerce_value("99999999999999999999999")

    assert value == 99999999999999999999999
    assert isinstance(value, int)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("typed", "expected"), [("1e3", 1000.0), ("2.5", 2.5), ("42", 42), ("-7", -7)])
def test_the_numbers_orjson_already_reads_faithfully_are_unchanged(typed: str, expected: float) -> None:
    """The control: only an integer the float path distorts is read another way."""
    assert coerce_value(typed) == expected


@pytest.mark.os_agnostic
def test_the_warning_for_a_huge_set_integer_quotes_the_digits_that_were_typed(
    clear_config_cache: None,
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """``--set display.wwn_width=99999999999999999999999`` was quoted back as ``1e+23``."""
    result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "display.wwn_width=99999999999999999999999", "config", "--format", "json"],
        obj=production_factory,
    )

    assert result.exit_code == 0, result.output
    assert "display.wwn_width=99999999999999999999999:" in result.stderr, result.stderr
    assert "1e+23" not in result.stderr
    assert json.loads(result.stdout)["ok"] is True
