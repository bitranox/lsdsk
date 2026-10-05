"""Which ``--set`` sections are refused, and which are passed through.

COMMANDS.md once said exit `2` covers "a ``--set`` override naming a section or
key the tool does not have". The code never did that, and deliberately: a section
this tool does not own belongs to a library or to another consumer of the same
configuration files, so it is passed through untouched. What IS refused is a
section one typo away from an owned one - the key check says nothing about a
section it does not own, which is exactly what a misspelled owned section looks
like to it - and an unknown key inside an owned section. Both directions are held
here through the real command line, with the exit code a reader would see.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


def _findings(cli_runner: CliRunner, factory: Callable[[], Any], *overrides: str) -> Result:
    """Run ``findings`` over a committed capture with each override as a ``--set``."""
    sets = [part for override in overrides for part in ("--set", override)]
    return cli_runner.invoke(cli, [*sets, "--replay", str(CAPTURE), "--no-record", "findings"], obj=factory)


def _said(result: Result) -> str:
    """Standard error on one line, so a sentence wrapped inside the error panel can be searched."""
    return " ".join((result.stderr or "").replace("\N{BOX DRAWINGS LIGHT VERTICAL}", " ").split())


@pytest.mark.os_agnostic
def test_a_section_this_tool_does_not_own_is_passed_through(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """``bogus.key`` resembles nothing owned, so it is another consumer's and the run is unchanged."""
    control = _findings(cli_runner, production_factory)
    result = _findings(cli_runner, production_factory, "bogus.key=1")

    assert control.exit_code in {ExitCode.SUCCESS, ExitCode.GENERAL_ERROR}, f"the control left {control.exit_code}"
    assert result.exit_code == control.exit_code, f"left {result.exit_code}: {_said(result)[-300:]}"
    assert "bogus" not in _said(result), f"an unowned section was reported: {_said(result)}"


@pytest.mark.os_agnostic
def test_a_section_one_typo_from_an_owned_one_is_a_usage_error(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """``thresholdz`` is ``thresholds`` with one letter wrong, so it is refused and the owned one named."""
    result = _findings(cli_runner, production_factory, "thresholdz.x=1")

    said = _said(result)
    assert result.exit_code == ExitCode.USAGE_ERROR, f"left {result.exit_code}: {said[-300:]}"
    assert "there is no section [thresholdz]" in said, said
    assert "Did you mean [thresholds]?" in said, said


@pytest.mark.os_agnostic
def test_an_unknown_key_in_an_owned_section_is_a_usage_error(
    cli_runner: CliRunner, production_factory: Callable[[], Any]
) -> None:
    """The third case the sentence covers: the section is owned and the key is not one of its keys."""
    result = _findings(cli_runner, production_factory, "thresholds.no_such_key=1")

    said = _said(result)
    assert result.exit_code == ExitCode.USAGE_ERROR, f"left {result.exit_code}: {said[-300:]}"
    assert "[thresholds] has no key 'no_such_key'" in said, said
