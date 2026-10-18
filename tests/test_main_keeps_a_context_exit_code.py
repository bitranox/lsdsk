"""An exit through click's context keeps its code all the way out of ``main()``.

Under ``standalone_mode=False`` click catches the ``Exit`` that ``ctx.exit(N)`` raises
inside a command and RETURNS ``N`` from ``cli.main()``; it never reaches the caller as an
exception. ``_run_cli`` discarded that return value and answered 0, so a command leaving
through its context with a refusal reported success. The service is replaced at the
composition seam, so the real ``main()`` and the real command tree run.
"""

from __future__ import annotations

from dataclasses import replace

import click
import pytest

from lsdsk.adapters.cli.main import main
from lsdsk.composition import AppServices, build_testing


def _services_whose_info_leaves_with(code: int) -> AppServices:
    def leave() -> None:
        click.get_current_context().exit(code)

    return replace(build_testing(), print_info=leave)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("code", [5, 78])
def test_a_command_leaving_through_its_context_keeps_the_code(code: int) -> None:
    assert main(["info"], services_factory=lambda: _services_whose_info_leaves_with(code)) == code


@pytest.mark.os_agnostic
def test_a_command_that_returns_normally_still_exits_zero() -> None:
    """The control: the code is read from click, not invented, so a plain run stays 0."""
    assert main(["info"], services_factory=build_testing) == 0
