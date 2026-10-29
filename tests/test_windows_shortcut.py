"""The Windows desktop shortcut: a run-as-administrator ``.lnk`` written through pwshpy.

The adapter reaches pwshpy by name and takes the loader as a keyword, so these
inject a fake module at that seam: it resolves the known folders to directories
under each test's own tmp and records every shortcut it is asked to write,
leaving an empty file where the real ``.lnk`` would be. That makes the whole
adapter - folder lookup, the target/argument split, the run-as flag, the
Start-menu copy, the refusals - testable on every runner, while the shell's own
``.lnk`` writing is pwshpy's and is verified on a real Windows machine.
"""

from __future__ import annotations

import errno
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.shortcut.windows import ShortcutWriteError, WindowsDesktopShortcut
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import OutputFormat

_LAUNCH = ("C:/Users/me/.local/bin/uvx.exe", "--exclude-newer", "7 days", "lsdsk@latest")


class _FakeError(Exception):
    """Stands in for pwshpy's ``PwshPyError``."""


class _FakeAccessDeniedError(_FakeError, PermissionError):
    """Stands in for pwshpy's ``NativeAccessDeniedError``: a ``PwshPyError`` AND a ``PermissionError``."""


class _KnownFolder:
    DESKTOP = "Desktop"
    PROGRAMS = "Programs"


@dataclass
class _FakePs:
    """Resolves known folders from a mapping and records each shortcut written."""

    folders: dict[str, Path | None]
    written: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    refuse_with: Exception | None = None
    lookup_fails: bool = False
    #: Refuse only a shortcut written into this folder, as a Start menu that will not take one does.
    refuse_only_in: Path | None = None

    def get_known_folder(self, folder: object) -> Path | None:
        if self.lookup_fails:
            raise _FakeError("SHGetKnownFolderPath failed: 0x80070002")
        return self.folders.get(str(folder))

    def new_shortcut(self, path: object, **fields: object) -> object:
        refused_here = self.refuse_only_in is None or Path(str(path)).parent == self.refuse_only_in
        if self.refuse_with is not None and refused_here:
            raise self.refuse_with
        Path(str(path)).write_bytes(b"")
        self.written.append({"path": Path(str(path)), **fields})
        return object()


@dataclass
class _FakePwshpy:
    ps: _FakePs
    KnownFolder: _KnownFolder = field(default_factory=_KnownFolder)
    PwshPyError: type[Exception] = _FakeError


@pytest.fixture
def desktop(tmp_path: Path) -> Path:
    folder = tmp_path / "Desktop"
    folder.mkdir()
    return folder


@pytest.fixture
def programs(tmp_path: Path) -> Path:
    folder = tmp_path / "Start Menu" / "Programs"
    folder.mkdir(parents=True)
    return folder


def _adapter(ps: _FakePs) -> WindowsDesktopShortcut:
    return WindowsDesktopShortcut(load=lambda: _FakePwshpy(ps=ps))


@pytest.mark.os_agnostic
def test_create_writes_a_run_as_administrator_lnk_on_the_desktop(desktop: Path, tmp_path: Path) -> None:
    ps = _FakePs(folders={"Desktop": desktop})
    icon = tmp_path / "lsdsk.ico"

    written = _adapter(ps).create(launch=_LAUNCH, icon=icon).path

    assert written == desktop / "lsdsk.lnk"
    assert ps.written[0]["path"] == desktop / "lsdsk.lnk"
    assert ps.written[0]["target"] == _LAUNCH[0]
    assert ps.written[0]["arguments"] == _LAUNCH[1:], "the argv is handed over whole, never pre-joined"
    assert ps.written[0]["icon"] == str(icon)
    assert ps.written[0]["run_as_administrator"] is True, "the shortcut must start lsdsk elevated: one window"
    assert ps.written[0]["force"] is True, "a second create refreshes the shortcut"
    assert ps.written[0]["working_directory"] == str(Path.home())
    assert ps.written[0]["description"]


@pytest.mark.os_agnostic
def test_create_also_puts_the_shortcut_in_the_start_menu(desktop: Path, programs: Path) -> None:
    ps = _FakePs(folders={"Desktop": desktop, "Programs": programs})

    _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert [entry["path"] for entry in ps.written] == [desktop / "lsdsk.lnk", programs / "lsdsk.lnk"]
    desktop_fields = {k: v for k, v in ps.written[0].items() if k != "path"}
    menu_fields = {k: v for k, v in ps.written[1].items() if k != "path"}
    assert menu_fields == desktop_fields, "the Start-menu entry must launch exactly what the Desktop one does"


@pytest.mark.os_agnostic
def test_a_start_menu_windows_does_not_name_is_skipped(desktop: Path) -> None:
    ps = _FakePs(folders={"Desktop": desktop, "Programs": None})

    _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert [entry["path"] for entry in ps.written] == [desktop / "lsdsk.lnk"]


@pytest.mark.os_agnostic
def test_no_icon_is_passed_as_none(desktop: Path) -> None:
    ps = _FakePs(folders={"Desktop": desktop})

    _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert ps.written[0]["icon"] is None


@pytest.mark.os_agnostic
def test_exists_turns_true_once_created(desktop: Path) -> None:
    shortcut = _adapter(_FakePs(folders={"Desktop": desktop}))
    assert shortcut.exists() is False

    shortcut.create(launch=_LAUNCH, icon=None)

    assert shortcut.exists() is True


@pytest.mark.os_agnostic
def test_a_desktop_folder_windows_names_is_a_desktop(desktop: Path) -> None:
    assert _adapter(_FakePs(folders={"Desktop": desktop})).has_desktop() is True


@pytest.mark.os_agnostic
def test_an_account_windows_names_no_desktop_for_has_none() -> None:
    shortcut = _adapter(_FakePs(folders={"Desktop": None}))

    assert shortcut.has_desktop() is False
    assert shortcut.exists() is False
    assert shortcut.no_desktop_reason() == "Windows names no Desktop folder for this account"


@pytest.mark.os_agnostic
def test_a_named_desktop_folder_that_is_missing_is_named_in_the_reason(tmp_path: Path) -> None:
    missing = tmp_path / "Desktop"
    shortcut = _adapter(_FakePs(folders={"Desktop": missing}))

    assert shortcut.has_desktop() is False
    assert shortcut.no_desktop_reason() == f"no desktop folder at {missing}"


@pytest.mark.os_agnostic
def test_a_failed_folder_lookup_means_no_desktop_and_says_why() -> None:
    shortcut = _adapter(_FakePs(folders={}, lookup_fails=True))

    assert shortcut.has_desktop() is False
    assert "0x80070002" in shortcut.no_desktop_reason()


@pytest.mark.os_agnostic
def test_create_on_an_account_with_no_desktop_writes_nothing() -> None:
    ps = _FakePs(folders={"Desktop": None})

    with pytest.raises(FileNotFoundError, match="names no Desktop folder"):
        _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert ps.written == []


@pytest.mark.os_agnostic
def test_a_refused_write_is_an_oserror_carrying_pwshpys_message(desktop: Path) -> None:
    ps = _FakePs(folders={"Desktop": desktop}, refuse_with=_FakeError("IPersistFile::Save failed: 0x80004005"))

    with pytest.raises(ShortcutWriteError) as refused:
        _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert isinstance(refused.value, OSError), "the command and the offer both catch OSError for a failed write"
    assert not isinstance(refused.value, PermissionError), "only a refusal for lack of rights is a permission error"
    assert "0x80004005" in str(refused.value)


@pytest.mark.os_agnostic
def test_an_access_denied_write_is_a_permission_error_carrying_pwshpys_message(desktop: Path) -> None:
    denied = _FakeAccessDeniedError("IPersistFile::Save failed: 0x80070005")
    ps = _FakePs(folders={"Desktop": desktop}, refuse_with=denied)

    with pytest.raises(ShortcutWriteError) as refused:
        _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert isinstance(refused.value, PermissionError), "the command exits 13 for a PermissionError"
    assert refused.value.errno == errno.EACCES
    assert "0x80070005" in str(refused.value)
    assert refused.value.__cause__ is denied


@pytest.mark.os_agnostic
def test_a_launch_with_no_executable_is_refused(desktop: Path) -> None:
    ps = _FakePs(folders={"Desktop": desktop})

    with pytest.raises(ShortcutWriteError, match="the launch is empty"):
        _adapter(ps).create(launch=(), icon=None)

    assert ps.written == []


def _seams(shortcuts: WindowsDesktopShortcut, icon: Path) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: Path(_LAUNCH[0]),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=1, uvx=None),
        copy_icon=lambda: icon,
    )


@pytest.mark.os_agnostic
def test_install_shortcut_reports_the_desktop_lnk_through_the_real_adapter(
    desktop: Path, programs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ps = _FakePs(folders={"Desktop": desktop, "Programs": programs})
    icon = tmp_path / "lsdsk.ico"

    install_shortcut(output_format=OutputFormat.JSON, seams=_seams(_adapter(ps), icon))

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["data"]["shortcut"] == str(desktop / "lsdsk.lnk")
    assert envelope["data"]["launch"][0] == str(Path(_LAUNCH[0]))
    assert ps.written[0]["target"] == str(Path(_LAUNCH[0]))
    assert ps.written[0]["icon"] == str(icon)


@pytest.mark.os_agnostic
def test_install_shortcut_turns_a_refused_lnk_into_io_error(
    desktop: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ps = _FakePs(folders={"Desktop": desktop}, refuse_with=_FakeError("IPersistFile::Save failed: 0x80004005"))

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, seams=_seams(_adapter(ps), tmp_path / "lsdsk.ico"))

    assert leaving.value.code == ExitCode.IO_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert "0x80004005" in envelope["error"]["message"]


@pytest.mark.os_agnostic
def test_install_shortcut_turns_an_access_denied_lnk_into_permission_denied(
    desktop: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    denied = _FakeAccessDeniedError("IPersistFile::Save failed: 0x80070005")
    ps = _FakePs(folders={"Desktop": desktop}, refuse_with=denied)

    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, seams=_seams(_adapter(ps), tmp_path / "lsdsk.ico"))

    assert leaving.value.code == ExitCode.PERMISSION_DENIED, (
        "an icon copy refused for rights exits 13; so does the .lnk"
    )
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["type"] == "PERMISSION_DENIED"
    assert "0x80070005" in envelope["error"]["message"]


@pytest.mark.parametrize(
    "refusal",
    [
        _FakeError("IPersistFile::Save failed: 0x80004005"),
        _FakeAccessDeniedError("IPersistFile::Save failed: 0x80070005"),
    ],
    ids=["refused", "access-denied"],
)
def test_a_start_menu_copy_that_fails_after_the_desktop_one_is_a_warning_not_a_failure(
    desktop: Path, programs: Path, refusal: Exception
) -> None:
    """The Desktop ``.lnk`` is the shortcut and it is written; a refused Start-menu copy must not say nothing was."""
    ps = _FakePs(folders={"Desktop": desktop, "Programs": programs}, refuse_with=refusal, refuse_only_in=programs)

    written = WindowsDesktopShortcut(load=lambda: _FakePwshpy(ps=ps)).create(launch=_LAUNCH, icon=None)

    assert written.path == desktop / "lsdsk.lnk"
    assert [entry["path"] for entry in ps.written] == [desktop / "lsdsk.lnk"]
    assert len(written.skipped) == 1, written.skipped
    assert "on the Desktop" in written.skipped[0]
    assert str(programs / "lsdsk.lnk") in written.skipped[0]
    assert str(refusal) in written.skipped[0]


def test_install_shortcut_names_the_start_menu_copy_it_could_not_write_in_skipped(
    desktop: Path, programs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Desktop ``.lnk`` is reported, and the refused Start-menu copy is named in ``skipped``, ``ok`` false."""
    refusal = _FakeError("IPersistFile::Save failed: 0x80004005")
    ps = _FakePs(folders={"Desktop": desktop, "Programs": programs}, refuse_with=refusal, refuse_only_in=programs)
    shortcuts = WindowsDesktopShortcut(load=lambda: _FakePwshpy(ps=ps))

    install_shortcut(output_format=OutputFormat.JSON, seams=_seams(shortcuts, tmp_path / "lsdsk.ico"))

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["shortcut"] == str(desktop / "lsdsk.lnk")
    assert envelope["ok"] is False
    assert len(envelope["skipped"]) == 1, envelope["skipped"]
    assert "could not be added to the Start menu" in envelope["skipped"][0]
    assert str(refusal) in envelope["skipped"][0]


def test_a_refused_write_carries_eio(desktop: Path) -> None:
    """``EIO``, not ``EACCES``: only a refusal for rights is a permission error, and the errno says which it was."""
    ps = _FakePs(folders={"Desktop": desktop}, refuse_with=_FakeError("IPersistFile::Save failed: 0x80004005"))

    with pytest.raises(ShortcutWriteError) as refused:
        _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert refused.value.errno == errno.EIO


def test_an_empty_launch_carries_einval(desktop: Path) -> None:
    """An empty argv is a bad argument to the adapter, not a failed write."""
    with pytest.raises(ShortcutWriteError) as refused:
        _adapter(_FakePs(folders={"Desktop": desktop})).create(launch=(), icon=None)

    assert refused.value.errno == errno.EINVAL


def test_create_on_a_named_desktop_folder_that_is_missing_writes_nothing_and_says_why(tmp_path: Path) -> None:
    """Windows can name a Desktop that is not there (a redirect to a removed share); the folder is never created."""
    missing = tmp_path / "Desktop"
    ps = _FakePs(folders={"Desktop": missing})

    with pytest.raises(FileNotFoundError, match="no desktop folder at") as refused:
        _adapter(ps).create(launch=_LAUNCH, icon=None)

    assert refused.value.filename == str(missing)
    assert ps.written == []
    assert not missing.exists()


def test_a_start_menu_windows_names_but_that_is_missing_is_skipped_quietly(desktop: Path, tmp_path: Path) -> None:
    """A Programs folder that is not there is no Start menu to add to: skipped, as one Windows names none of is."""
    ps = _FakePs(folders={"Desktop": desktop, "Programs": tmp_path / "Start Menu" / "Programs"})

    written = WindowsDesktopShortcut(load=lambda: _FakePwshpy(ps=ps)).create(launch=_LAUNCH, icon=None)

    assert [entry["path"] for entry in ps.written] == [desktop / "lsdsk.lnk"]
    assert written.skipped == ()


def test_a_folder_named_like_the_shortcut_is_not_the_shortcut(desktop: Path) -> None:
    """Only a FILE called lsdsk.lnk is the shortcut; a directory of that name must not stop the offer."""
    (desktop / "lsdsk.lnk").mkdir()

    assert _adapter(_FakePs(folders={"Desktop": desktop})).exists() is False
