"""The pre-view offer is best-effort: a filesystem error never aborts the view.

``offer_shortcut`` is called bare, before the interactive view opens, so a
filesystem error from the icon copy, from ``create()`` or from the opt-out write
must not propagate out of it and take the whole view down with it. That error is
an ``OSError`` for a full or read-only directory, and a ``tomlkit`` parse error
for an opt-out write onto a reader-hand-edited, malformed ``config.toml``. These
drive the real seam with a shortcut double, an icon copier and a ``disable`` that
raise those errors and assert the offer degrades - an iconless shortcut, or one
warning and a return - while the explicit ``install-shortcut`` command still
fails on a create failure, with ``IO_ERROR`` and a line naming it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest
import tomlkit

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut, offer_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import ShortcutChoice

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from lsdsk.application.ports import DesktopShortcuts


class _ShortcutsThatFailToCreate:
    """A DesktopShortcuts double whose ``create`` raises ``OSError``."""

    def __init__(self) -> None:
        self.create_calls = 0

    def exists(self) -> bool:
        return False

    def has_desktop(self) -> bool:
        return True

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        self.create_calls += 1
        raise OSError("read-only config directory")

    def desktop_dir(self) -> Path:
        return Path("/desktop")


class _RecordingShortcuts:
    """A DesktopShortcuts double that records the ``create`` it was told to do."""

    def __init__(self) -> None:
        self.created: tuple[tuple[str, ...], Path | None] | None = None

    def exists(self) -> bool:
        return False

    def has_desktop(self) -> bool:
        return True

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)
        return Path("/desktop/lsdsk.desktop")

    def desktop_dir(self) -> Path:
        return Path("/desktop")


def _raise_oserror() -> NoReturn:
    """An icon copier that cannot write - a full or read-only config directory."""
    raise OSError("no space left on device")


def _raise_toml_parse_error() -> NoReturn:
    """Fail exactly as ``disable_offer`` does on a malformed existing ``config.toml``.

    ``disable_offer`` reads the reader's file through ``tomlkit.parse``; a
    hand-edited, malformed file makes that raise ``tomlkit.exceptions.ParseError``
    - a ``ValueError``, never an ``OSError`` - which the real parser reproduces here.
    """
    tomlkit.parse("this is = = not valid toml")
    msg = "tomlkit.parse should have raised on malformed input"
    raise AssertionError(msg)


def _seams(
    shortcuts: DesktopShortcuts,
    *,
    uvx: Path | None = None,
    copy_icon: Callable[[], Path] | None = None,
) -> ShortcutSeams:
    seams = ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: uvx,
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
    )
    # With no copier given, the dataclass's default - the real icon copier - stays in place.
    return seams if copy_icon is None else dataclasses.replace(seams, copy_icon=copy_icon)


@pytest.mark.os_agnostic
def test_the_offer_does_not_abort_the_view_when_create_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """A create that raises OSError degrades to one warning and a return, not a crash."""
    shortcuts = _ShortcutsThatFailToCreate()

    offer_shortcut(
        DisplaySettings(),
        ask_choice=lambda: ShortcutChoice.CREATE,
        ask_install=lambda: False,
        seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx")),
    )

    assert shortcuts.create_calls == 1, "the offer should have attempted the create"
    err = capsys.readouterr().err
    assert "could not create the desktop shortcut" in err.lower(), "the failure should be noted on stderr"


@pytest.mark.os_agnostic
def test_the_offer_does_not_abort_the_view_when_the_opt_out_write_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 'never again' whose opt-out write raises OSError warns once and returns."""

    def failing_disable() -> NoReturn:
        raise OSError("read-only config directory")

    offer_shortcut(
        DisplaySettings(),
        ask_choice=lambda: ShortcutChoice.NEVER,
        ask_install=lambda: False,
        seams=_seams(_RecordingShortcuts(), uvx=Path("/usr/bin/uvx")),
        disable=failing_disable,
    )

    err = capsys.readouterr().err
    assert "could not save" in err.lower(), "the failed opt-out write should be noted on stderr"


@pytest.mark.os_agnostic
def test_the_offer_does_not_abort_the_view_when_the_opt_out_write_hits_a_malformed_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 'never again' onto a malformed config.toml warns once and returns, not a crash.

    A reader-hand-edited ``config.toml`` makes ``disable_offer`` raise a
    ``tomlkit.exceptions.ParseError`` (a ``ValueError``, not an ``OSError``), which
    must degrade just as a write ``OSError`` does so the interactive view still opens.
    """
    offer_shortcut(
        DisplaySettings(),
        ask_choice=lambda: ShortcutChoice.NEVER,
        ask_install=lambda: False,
        seams=_seams(_RecordingShortcuts(), uvx=Path("/usr/bin/uvx")),
        disable=_raise_toml_parse_error,
    )

    err = capsys.readouterr().err
    assert "could not save" in err.lower(), "the malformed-config opt-out write should be noted on stderr"


@pytest.mark.os_agnostic
def test_an_unwritable_icon_degrades_the_offer_to_an_iconless_shortcut() -> None:
    """An icon-copy OSError does not stop creation: the shortcut is created iconless."""
    shortcuts = _RecordingShortcuts()

    offer_shortcut(
        DisplaySettings(),
        ask_choice=lambda: ShortcutChoice.CREATE,
        ask_install=lambda: False,
        seams=_seams(shortcuts, uvx=Path("/usr/bin/uvx"), copy_icon=_raise_oserror),
    )

    assert shortcuts.created is not None, "the shortcut should still have been created"
    assert shortcuts.created[1] is None, "an unwritable icon should degrade to iconless, not abort"


@pytest.mark.os_agnostic
def test_the_command_still_surfaces_a_create_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """Only the offer degrades: the explicit command fails on a create failure, naming it."""
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(seams=_seams(_ShortcutsThatFailToCreate(), uvx=Path("/usr/bin/uvx")))

    assert leaving.value.code == ExitCode.IO_ERROR
    assert "read-only config directory" in capsys.readouterr().err
