"""A refused atomic write names the file the reader asked for, never the temporary beside it.

Every file this tool replaces is written to ``.<name>.<random>.tmp`` in the
destination's directory and renamed over it, so the ``OSError`` a refusal
raises names that temporary: a path the reader never asked for, a different
one on every run, and gone by the time anybody looks. The refusal reached the
reader as it was - ``install-shortcut``'s error message, the skipped menu
copy, the snapshot and history writers - so the envelope changed from one run
to the next and a refused Desktop never named ``lsdsk.desktop``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.atomicfile import replace_atomically, replace_bytes_atomically
from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.shortcut.linux import LinuxDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Iterator


def test_a_text_write_that_cannot_start_names_its_destination(tmp_path: Path) -> None:
    """No temporary can be made in a missing directory; the refusal names the file, not the temporary."""
    destination = tmp_path / "missing" / "store.json"

    with pytest.raises(FileNotFoundError) as refused:
        replace_atomically(destination, "{}", mode=0o600)

    assert refused.value.filename == str(destination), refused.value
    assert refused.value.filename2 is None, refused.value
    assert ".tmp" not in str(refused.value), refused.value


def test_a_binary_write_that_cannot_start_names_its_destination(tmp_path: Path) -> None:
    """The icon copier's writer reports the same way as the text one."""
    destination = tmp_path / "missing" / "lsdsk.png"

    with pytest.raises(FileNotFoundError) as refused:
        replace_bytes_atomically(destination, b"\x89PNG", mode=0o644)

    assert refused.value.filename == str(destination), refused.value
    assert ".tmp" not in str(refused.value), refused.value


@pytest.fixture
def desktop_nobody_may_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A Desktop folder that exists but refuses a new file, as one owned by another account does.

    Root writes into a read-only folder regardless, and then there is no
    refusal to report, so the test skips rather than passing on nothing.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(desktop))
    desktop.chmod(0o555)
    try:
        if os.access(desktop, os.W_OK):
            pytest.skip("this account writes into a read-only folder, so the write is never refused")
        yield desktop
    finally:
        desktop.chmod(0o755)


@pytest.mark.os_linux
def test_a_refused_desktop_entry_is_reported_by_the_name_the_reader_knows(
    tmp_path: Path, desktop_nobody_may_write: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Desktop that refuses the write is reported as refusing ``lsdsk.desktop``, the same on every run."""
    seams = ShortcutSeams(
        shortcuts=LinuxDesktopShortcut(),
        uvx=lambda: Path("/usr/bin/uvx"),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
        copy_icon=lambda: tmp_path / "icon.png",
    )
    messages: list[str] = []
    for _ in range(2):
        with pytest.raises(SystemExit) as leaving:
            install_shortcut(output_format=OutputFormat.JSON, seams=seams)
        assert leaving.value.code == ExitCode.PERMISSION_DENIED
        messages.append(json.loads(capsys.readouterr().out)["error"]["message"])

    assert str(desktop_nobody_may_write / "lsdsk.desktop") in messages[0], messages[0]
    assert ".tmp" not in messages[0], messages[0]
    assert messages[0] == messages[1], "the same refusal should read the same on every run"


@pytest.mark.os_windows
def test_a_refused_rename_on_windows_keeps_the_windows_error_code(tmp_path: Path) -> None:
    """The refusal renamed to the destination still carries the Windows error code the rename failed with.

    Python's ``open`` shares no delete access on Windows, so a destination it
    holds open cannot be renamed over and the rename itself is refused. Rebuilt
    from the errno alone, that refusal would name the right file and carry no
    ``winerror`` - the code a Windows caller reads, and the one ``OSError``
    picks its subclass from there.
    """
    assert sys.platform == "win32"  # narrows OSError.winerror for pyright
    destination = tmp_path / "store.json"
    destination.write_text("{}", encoding="utf-8")

    with destination.open("rb"), pytest.raises(OSError) as refused:
        replace_atomically(destination, '{"new": 1}', mode=0o600)

    original = refused.value.__cause__
    assert isinstance(original, OSError), refused.value
    assert refused.value.filename == str(destination), refused.value
    assert refused.value.winerror is not None, refused.value
    assert refused.value.winerror == original.winerror
    assert destination.read_text(encoding="utf-8") == "{}"
