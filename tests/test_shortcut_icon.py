"""The shortcut icon: a shipped asset copied to a stable per-user path.

A ``uvx lsdsk@latest`` launch reinstalls lsdsk into an ephemeral directory, so a
shortcut pointing at an icon inside the installed package would lose it on the
next upgrade. The icon is therefore copied beside the user ``config.toml`` - the
one place that outlives an upgrade - and refreshed on every create.

These drive the real copier against the config directory the autouse conftest
fixture redirects under each test's own tmp, so nothing reaches the developer's
machine, and the Linux end-to-end case builds the real ``.desktop`` through the
real adapter.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut
from lsdsk.adapters.config.offer import user_config_path
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.shortcut.macos import MacosDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: An ICO file starts with a zero reserved word and type 1.
_ICO_SIGNATURE = b"\x00\x00\x01\x00"


@pytest.mark.os_agnostic
def test_the_shipped_icon_is_resolvable_via_importlib_resources() -> None:
    """The package carries the PNG and it resolves without __file__ path math.

    importlib.resources is used rather than a path derived from __file__ because
    the package may be installed read-only or zipped.
    """
    data = (resources.files("lsdsk") / "assets" / "icon" / "lsdsk-256.png").read_bytes()
    assert data.startswith(_PNG_SIGNATURE), "the shipped icon is not a PNG"
    assert len(data) > 1000, "the shipped icon is implausibly small"


@pytest.mark.os_agnostic
def test_copy_shipped_icon_writes_the_png_into_the_user_config_dir() -> None:
    """The icon lands beside the user config.toml, resolved through the one resolver."""
    from lsdsk.adapters.shortcut.icon import copy_shipped_icon

    destination = copy_shipped_icon(platform="linux")

    assert destination == user_config_path().parent / "lsdsk.png"
    assert destination.is_file()
    assert destination.read_bytes().startswith(_PNG_SIGNATURE)
    assert list(destination.parent.glob(".lsdsk.png.*.tmp")) == [], "a temporary file was left behind"


@pytest.mark.os_agnostic
def test_windows_gets_the_ico_because_a_lnk_shows_no_png() -> None:
    """A ``.lnk`` takes its icon from an ICO (or an executable), never from a PNG."""
    from lsdsk.adapters.shortcut.icon import copy_shipped_icon

    destination = copy_shipped_icon(platform="win32")

    assert destination == user_config_path().parent / "lsdsk.ico"
    assert destination.read_bytes().startswith(_ICO_SIGNATURE), "the Windows icon is not an ICO"
    assert destination.read_bytes() == (resources.files("lsdsk") / "assets" / "icon" / "lsdsk.ico").read_bytes()


@pytest.mark.os_agnostic
def test_copy_shipped_icon_refreshes_on_every_call() -> None:
    """A second create re-copies, so an upgraded package icon propagates."""
    from lsdsk.adapters.shortcut.icon import copy_shipped_icon

    destination = copy_shipped_icon(platform="linux")
    shipped = destination.read_bytes()
    destination.write_bytes(b"stale")

    again = copy_shipped_icon(platform="linux")

    assert again == destination
    assert destination.read_bytes() == shipped, "the icon was not refreshed on re-create"


@pytest.mark.os_agnostic
def test_replace_bytes_atomically_writes_bytes_and_leaves_no_temporary(tmp_path: Path) -> None:
    """Binary bytes are written whole and the temporary file is renamed away."""
    from lsdsk.adapters.atomicfile import replace_bytes_atomically

    target = tmp_path / "x.bin"
    body = b"\x00\x01\x02\xff\xfe"

    replace_bytes_atomically(target, body, mode=0o644)

    assert target.read_bytes() == body
    assert list(tmp_path.glob(".x.bin.*.tmp")) == [], "a temporary file was left behind"


@pytest.mark.os_posix
def test_replace_bytes_atomically_narrows_to_the_mode_given(tmp_path: Path) -> None:
    """The file ends at exactly the mode asked for, whatever the umask."""
    from lsdsk.adapters.atomicfile import replace_bytes_atomically

    target = tmp_path / "y.bin"
    replace_bytes_atomically(target, b"\x00\x01", mode=0o644)

    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.os_linux
def test_the_linux_desktop_built_through_install_references_the_copied_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-shortcut copies the icon and the real .desktop names the copy."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    (tmp_path / "Desktop").mkdir()

    seams = ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(),
        uvx=lambda: Path("/usr/bin/uvx"),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
    )
    install_shortcut(seams=seams)

    expected_icon = user_config_path().parent / "lsdsk.png"
    text = (tmp_path / "Desktop" / "lsdsk.desktop").read_text(encoding="utf-8")
    assert f"Icon={expected_icon}" in text
    assert expected_icon.is_file()


@pytest.mark.os_posix
def test_a_macos_command_carries_no_icon_even_when_one_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The .command form has no icon, so a path handed to it is ignored."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    icon = tmp_path / "lsdsk.png"
    icon.write_bytes(_PNG_SIGNATURE)

    text = MacosDesktopShortcut().create(launch=("lsdsk",), icon=icon).path.read_text(encoding="utf-8")

    assert "lsdsk.png" not in text
    assert "Icon" not in text


@pytest.mark.os_posix
def test_the_copied_icon_is_readable_by_everyone() -> None:
    """A desktop environment reads it to draw the launcher, so unlike the config beside it it is 0644."""
    from lsdsk.adapters.shortcut.icon import copy_shipped_icon

    assert copy_shipped_icon().stat().st_mode & 0o777 == 0o644
