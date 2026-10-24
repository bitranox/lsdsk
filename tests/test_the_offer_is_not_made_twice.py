"""The desktop-shortcut offer is made by the person's own run, never by an elevated one.

A bare run at a terminal offers the shortcut and then asks for elevation; the
elevated copy it starts is a bare run at a terminal too. Offered there, the
question came a second time for one start, and an answer of "never" was written
by root into the person's own configuration directory: a root-owned 0600
``config.toml`` there makes every later unprivileged run refuse with exit 13.
An account already running as root or Administrator gets the same file.

So the offer is decided once, from the same answer the elevation question
reads: not in the relaunched copy (its marker), and not in a process that is
elevated already. The router is driven through :func:`run_default_view` with a
recording offer, and the elevation port answers RELAUNCHED so the run leaves
before any view is built.
"""

from __future__ import annotations

import io
import os
import sys
from typing import TYPE_CHECKING

import click
import pytest
from lib_layered_config import Config

from lsdsk.adapters.cli import cli
from lsdsk.adapters.cli.commands import scan
from lsdsk.adapters.cli.context import CLIContext, store_cli_context
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.composition import build_testing
from lsdsk.domain.elevation import Elevation, ElevationPrompt, ElevationState

if TYPE_CHECKING:
    from pathlib import Path


class _AtATerminal(io.StringIO):
    """A standard stream that says it is a terminal, which is all the router asks of it."""

    def isatty(self) -> bool:
        """Answer as a terminal does."""
        return True


def _at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stream the view needs is a terminal, replaced at the operating system's edge.

    Called from the test body, never from a fixture: pytest's capture puts its
    own ``sys.stdout`` back when the test's call phase begins.
    """
    monkeypatch.setattr(sys, "stdin", _AtATerminal())
    monkeypatch.setattr(sys, "stdout", _AtATerminal())
    monkeypatch.setattr(sys, "__stderr__", _AtATerminal())


def _relaunched(replay: Path | None, *, wanted: bool) -> Elevation:
    """The elevation port, answering as if the copy ran and left with 0."""
    del replay, wanted
    return Elevation(state=ElevationState.RELAUNCHED, prompt=ElevationPrompt.SUDO, exit_code=0)


def _the_display_a_run_resolves(*, elevated_relaunch: bool) -> DisplaySettings:
    """The layout values the root group resolves for a run with or without the relaunch marker."""
    ctx = click.Context(cli)
    store_cli_context(
        ctx,
        CLIContext(
            traceback=False,
            config=Config({"display": {"offer_desktop_shortcut": True}}, {}),
            services=build_testing(),
            elevated_relaunch=elevated_relaunch,
        ),
    )
    return scan.resolve_tunables(ctx).display


def _offers_made(display: DisplaySettings, *, already_elevated: bool) -> list[DisplaySettings]:
    """Run the router once and return every offer it made."""
    offered: list[DisplaySettings] = []
    seams = scan.the_real_collaborators(_relaunched)._replace(
        offer=offered.append, already_elevated=lambda: already_elevated
    )
    with pytest.raises(SystemExit) as left:
        scan.run_default_view(None, display=display, seams=seams)
    assert left.value.code == 0, "the run never reached the elevation question"
    return offered


@pytest.mark.parametrize(
    ("elevated_relaunch", "offers"),
    [pytest.param(True, 0, id="the relaunched copy"), pytest.param(False, 1, id="the person's own run")],
)
def test_the_relaunched_copy_does_not_offer_the_shortcut_again(
    monkeypatch: pytest.MonkeyPatch, elevated_relaunch: bool, offers: int
) -> None:
    _at_a_terminal(monkeypatch)
    display = _the_display_a_run_resolves(elevated_relaunch=elevated_relaunch)

    assert len(_offers_made(display, already_elevated=False)) == offers


@pytest.mark.parametrize(
    ("already_elevated", "offers"),
    [pytest.param(True, 0, id="already elevated"), pytest.param(False, 1, id="not elevated")],
)
def test_a_process_that_is_elevated_already_does_not_offer_the_shortcut(
    monkeypatch: pytest.MonkeyPatch, already_elevated: bool, offers: int
) -> None:
    _at_a_terminal(monkeypatch)
    assert len(_offers_made(DisplaySettings(), already_elevated=already_elevated)) == offers


@pytest.mark.os_posix
def test_a_root_run_does_not_offer_the_shortcut_through_the_real_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production router, with only the effective uid substituted: the real check is the one read."""
    _at_a_terminal(monkeypatch)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    offered: list[DisplaySettings] = []
    seams = scan.the_real_collaborators(_relaunched)._replace(offer=offered.append)

    with pytest.raises(SystemExit) as left:
        scan.run_default_view(None, display=DisplaySettings(), seams=seams)

    assert left.value.code == 0, "the run never reached the elevation question"
    assert offered == []
