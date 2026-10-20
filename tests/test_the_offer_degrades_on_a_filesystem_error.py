"""The pre-view offer is best-effort: a filesystem error never aborts the view.

``offer_shortcut`` is called bare, before the interactive view opens, so an
``OSError`` from the icon copy, from ``create()`` or from the opt-out write must
not propagate out of it and take the whole view down with it. These drive the
real seam with a shortcut double and an icon copier that raise ``OSError`` and
assert the offer degrades - an iconless shortcut, or one warning and a return -
while the explicit ``install-shortcut`` command still surfaces a create failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut, offer_shortcut
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.domain.enums import ShortcutChoice

if TYPE_CHECKING:
    from collections.abc import Callable


class _ShortcutsThatFailToCreate:
    """A DesktopShortcuts double whose ``create`` raises ``OSError``."""

    def __init__(self) -> None:
        self.create_calls = 0

    def exists(self) -> bool:
        return False

    def create(self, *, launch: object, icon: Path | None) -> Path:
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

    def create(self, *, launch: object, icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)  # type: ignore[arg-type]
        return Path("/desktop/lsdsk.desktop")

    def desktop_dir(self) -> Path:
        return Path("/desktop")


def _raise_oserror() -> NoReturn:
    """An icon copier that cannot write - a full or read-only config directory."""
    raise OSError("no space left on device")


def _seams(
    shortcuts: object,
    *,
    uvx: Path | None = None,
    copy_icon: Callable[[], Path] | None = None,
) -> ShortcutSeams:
    kwargs: dict[str, object] = {
        "shortcuts": shortcuts,
        "uvx": lambda: uvx,
        "installed_lsdsk": lambda: None,
        "install_uv": lambda: None,
    }
    if copy_icon is not None:
        kwargs["copy_icon"] = copy_icon
    return ShortcutSeams(**kwargs)  # type: ignore[arg-type]


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
def test_the_command_still_surfaces_a_create_failure() -> None:
    """Only the offer degrades: the explicit command still lets a create failure out."""
    with pytest.raises(OSError, match="read-only config directory"):
        install_shortcut(seams=_seams(_ShortcutsThatFailToCreate(), uvx=Path("/usr/bin/uvx")))
