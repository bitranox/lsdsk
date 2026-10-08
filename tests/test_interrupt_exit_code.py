"""Ctrl-C during a run leaves 130, the code for an interrupt, through the real ``main``."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.composition import AppServices, build_production

if TYPE_CHECKING:
    from collections.abc import Callable


def _services_interrupted_while_starting_logging() -> AppServices:
    """The production services whose logging start meets a Ctrl-C."""

    def _interrupted(config: object) -> None:
        raise KeyboardInterrupt

    return replace(build_production(), init_logging=_interrupted)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("traceback_flag", [False, True])
def test_a_keyboard_interrupt_mid_run_leaves_130_not_the_software_error_code(
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
    strip_ansi: Callable[[str], str],
    traceback_flag: bool,
) -> None:
    """Click turns the interrupt into ``Abort`` under ``standalone_mode=False``.

    ``code_for_an_unhandled_exception`` mapped only the raw ``KeyboardInterrupt``,
    so the ``Abort`` click substitutes left 70, the code for a bug in this tool.
    The interrupt is raised at the ``init_logging`` port so that it travels
    through ``cli.main`` and click's own conversion, which a direct call with
    ``KeyboardInterrupt()`` never reaches.
    """
    args = ["disks", "--format", "json"]
    if traceback_flag:
        args = ["--traceback", *args]

    exit_code = cli_mod.main(args, services_factory=_services_interrupted_while_starting_logging)

    plain_err = strip_ansi(capsys.readouterr().err)
    assert exit_code == 130, plain_err
    assert "Traceback (most recent call last)" not in plain_err, plain_err
    assert "Abort" not in plain_err, plain_err
