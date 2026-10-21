"""The interactive offer: the should_offer gate, the three-way answer, acting on it.

Driven through injected fakes - a recording shortcut double, scripted answers,
and recorders for the install and the opt-out - so every branch runs on any
platform and nothing writes to the developer's own machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, offer_shortcut
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.domain.enums import ShortcutChoice
from lsdsk.domain.shortcut import uvx_launch_args

if TYPE_CHECKING:
    from collections.abc import Callable


class _RecordingShortcuts:
    """A DesktopShortcuts double: a settable ``exists`` and a recorded ``create``."""

    def __init__(self, *, present: bool = False) -> None:
        self.present = present
        self.created: tuple[tuple[str, ...], Path | None] | None = None

    def exists(self) -> bool:
        return self.present

    def create(self, *, launch: object, icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)  # type: ignore[arg-type]
        return Path("/desktop/lsdsk.desktop")

    def desktop_dir(self) -> Path:
        return Path("/desktop")


def _seams(
    shortcuts: _RecordingShortcuts,
    *,
    uvx: Path | None = None,
    lsdsk: Path | None = None,
    installed: Path | None = None,
) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: uvx,
        installed_lsdsk=lambda: lsdsk,
        install_uv=lambda: installed,
    )


def _answers(
    choice: ShortcutChoice, *, install: bool = False
) -> tuple[Callable[[], ShortcutChoice], Callable[[], bool]]:
    return (lambda: choice), (lambda: install)


class _DisableRecorder:
    def __init__(self) -> None:
        self.called = False

    def __call__(self) -> Path:
        self.called = True
        return Path("/config/config.toml")


@pytest.mark.os_agnostic
def test_a_disabled_key_means_no_offer_is_even_asked() -> None:
    shortcuts = _RecordingShortcuts()
    disable = _DisableRecorder()
    asked = False

    def ask_choice() -> ShortcutChoice:
        nonlocal asked
        asked = True
        return ShortcutChoice.CREATE

    offer_shortcut(
        DisplaySettings(offer_desktop_shortcut=False),
        ask_choice=ask_choice,
        ask_install=lambda: False,
        seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx")),
        disable=disable,
    )
    assert asked is False
    assert shortcuts.created is None
    assert disable.called is False


@pytest.mark.os_agnostic
def test_an_existing_shortcut_means_no_offer() -> None:
    shortcuts = _RecordingShortcuts(present=True)
    asked = False

    def ask_choice() -> ShortcutChoice:
        nonlocal asked
        asked = True
        return ShortcutChoice.CREATE

    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=lambda: False,
        seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx")),
        disable=_DisableRecorder(),
    )
    assert asked is False
    assert shortcuts.created is None


@pytest.mark.os_agnostic
def test_create_writes_the_uvx_launch_when_uvx_is_present() -> None:
    shortcuts = _RecordingShortcuts()
    ask_choice, ask_install = _answers(ShortcutChoice.CREATE)
    uvx = Path("/usr/bin/uvx")
    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=uvx),
        disable=_DisableRecorder(),
    )
    assert shortcuts.created is not None
    # The launch carries the path as this platform renders it: on Windows the same Path reads \usr\bin\uvx.
    assert shortcuts.created[0] == uvx_launch_args(str(uvx))


@pytest.mark.os_agnostic
def test_not_now_creates_nothing_and_does_not_disable() -> None:
    shortcuts = _RecordingShortcuts()
    disable = _DisableRecorder()
    ask_choice, ask_install = _answers(ShortcutChoice.NOT_NOW)
    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx")),
        disable=disable,
    )
    assert shortcuts.created is None
    assert disable.called is False


@pytest.mark.os_agnostic
def test_never_writes_the_opt_out_and_creates_nothing() -> None:
    shortcuts = _RecordingShortcuts()
    disable = _DisableRecorder()
    ask_choice, ask_install = _answers(ShortcutChoice.NEVER)
    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx")),
        disable=disable,
    )
    assert disable.called is True
    assert shortcuts.created is None


@pytest.mark.os_agnostic
def test_create_with_no_uvx_installs_on_consent_then_creates() -> None:
    shortcuts = _RecordingShortcuts()
    ask_choice, ask_install = _answers(ShortcutChoice.CREATE, install=True)
    installed = Path("/root/.local/bin/uvx")
    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=None, lsdsk=None, installed=installed),
        disable=_DisableRecorder(),
    )
    assert shortcuts.created is not None
    assert shortcuts.created[0] == uvx_launch_args(str(installed))


@pytest.mark.os_agnostic
def test_create_with_no_uvx_and_declined_install_creates_nothing() -> None:
    shortcuts = _RecordingShortcuts()
    ask_choice, ask_install = _answers(ShortcutChoice.CREATE, install=False)
    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=None, lsdsk=None, installed=None),
        disable=_DisableRecorder(),
    )
    assert shortcuts.created is None
