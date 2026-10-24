"""The interactive offer: the should_offer gate, the three-way answer, acting on it.

Driven through injected fakes - a recording shortcut double, scripted answers,
and recorders for the install and the opt-out - so every branch runs on any
platform and nothing writes to the developer's own machine.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, offer_shortcut
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import ShortcutChoice
from lsdsk.domain.shortcut import manual_install_line, uvx_launch_args

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from lsdsk.application.ports import DesktopShortcuts


class _RecordingShortcuts:
    """A DesktopShortcuts double: settable ``exists`` and ``has_desktop``, a recorded ``create``."""

    def __init__(self, *, present: bool = False, desktop: bool = True) -> None:
        self.present = present
        self.desktop = desktop
        self.created: tuple[tuple[str, ...], Path | None] | None = None

    def exists(self) -> bool:
        return self.present

    def has_desktop(self) -> bool:
        return self.desktop

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)
        return Path("/desktop/lsdsk.desktop")

    def no_desktop_reason(self) -> str:
        return f"no desktop folder at {Path('/desktop')}"


def _seams(
    shortcuts: DesktopShortcuts,
    *,
    uvx: Path | None = None,
    lsdsk: Path | None = None,
    installed: Path | None = None,
    installer_exit: int = 0,
) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: uvx,
        installed_lsdsk=lambda: lsdsk,
        install_uv=lambda: UvInstallResult(exit_code=installer_exit, uvx=installed),
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
def test_an_account_with_no_desktop_is_never_asked() -> None:
    """A headless server has no desktop to put a shortcut on, so the question is not put."""
    shortcuts = _RecordingShortcuts(desktop=False)
    disable = _DisableRecorder()
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
        disable=disable,
    )
    assert asked is False
    assert shortcuts.created is None
    assert disable.called is False


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


@pytest.mark.os_agnostic
def test_the_real_question_shows_the_letter_each_answer_is_typed_as(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rich reads ``[y]`` as a markup tag; unescaped, the letters vanish and the reader is left guessing."""
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    shortcuts = _RecordingShortcuts()

    offer_shortcut(DisplaySettings(), seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx")), disable=_DisableRecorder())

    shown = " ".join(capsys.readouterr().out.split())
    assert "[y] yes" in shown
    assert "[n] not now" in shown
    assert "[d] do not ask again" in shown
    assert shortcuts.created is None, "the scripted 'n' must have been read as not now"


@pytest.mark.os_agnostic
def test_each_step_of_the_offer_is_set_off_by_a_blank_line(capsys: pytest.CaptureFixture[str]) -> None:
    """The consent text, the install question and the result each start a paragraph of their own."""
    shortcuts = _RecordingShortcuts()
    asked_install_after: list[str] = []

    def ask_install() -> bool:
        asked_install_after.append(capsys.readouterr().out)
        return True

    offer_shortcut(
        DisplaySettings(),
        ask_choice=lambda: ShortcutChoice.CREATE,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=None, lsdsk=None, installed=Path("/root/.local/bin/uvx")),
        disable=_DisableRecorder(),
    )

    before_install = asked_install_after[0]
    assert before_install.startswith("\nlsdsk runs through uv")
    assert before_install.endswith("\n\n"), "the install question needs a blank line above it"
    assert capsys.readouterr().out.startswith("\nShortcut created at")


def test_a_failed_installer_in_the_offer_names_its_exit_code_and_creates_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    shortcuts = _RecordingShortcuts()
    ask_choice, ask_install = _answers(ShortcutChoice.CREATE, install=True)
    offer_shortcut(
        DisplaySettings(),
        ask_choice=ask_choice,
        ask_install=ask_install,
        seams=_seams(shortcuts, uvx=None, lsdsk=None, installed=Path("/root/.local/bin/uvx"), installer_exit=7),
        disable=_DisableRecorder(),
    )

    assert shortcuts.created is None
    said = capsys.readouterr()
    assert "the uv installer failed (exit 7)" in said.err
    assert manual_install_line(sys.platform) in said.err
    assert "uv was installed" not in said.out + said.err
