"""The user ``config.toml`` can be a symlink, and the shortcut paths must not break on one.

A dotfile manager (home-manager, stow, chezmoi in symlink mode) leaves
``config.toml`` as a link into a repository whose file carries another name. The
user configuration path used to be picked out of a set of RESOLVED paths by its
name, so a link made the search come up empty: ``install-shortcut`` died with a
bare ``StopIteration`` (exit 70, no envelope) and the offer's ``y`` and ``d``
answers took the whole run down with it. These drive the real writer, the real
icon copier and the real command against a linked file.

Where no user path can be named at all - a platform the configuration library
reads no user layer on, or a relative ``XDG_CONFIG_HOME`` that would put the
file wherever the reader happened to start lsdsk - the answer is a typed
``OSError``, which the command's 13/74 envelope and the offer's warning already
handle.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, install_shortcut, offer_shortcut
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.offer import NoUserConfigPathError, disable_offer, user_config_path
from lsdsk.adapters.config.tunables import DisplaySettings
from lsdsk.adapters.uv_bootstrap import UvInstallResult
from lsdsk.domain.enums import OutputFormat, ShortcutChoice

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class _RecordingShortcuts:
    """A DesktopShortcuts double that records what it was told to create."""

    def __init__(self) -> None:
        self.created: tuple[tuple[str, ...], Path | None] | None = None

    def exists(self) -> bool:
        return False

    def has_desktop(self) -> bool:
        return True

    def create(self, *, launch: Sequence[str], icon: Path | None) -> Path:
        self.created = (tuple(launch), icon)
        return Path("/desktop/lsdsk.desktop")

    def no_desktop_reason(self) -> str:
        return f"no desktop folder at {Path('/desktop')}"


def _seams(shortcuts: _RecordingShortcuts) -> ShortcutSeams:
    return ShortcutSeams(
        shortcuts=shortcuts,
        uvx=lambda: Path("/usr/bin/uvx"),
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
    )


def _linked_config(user_config_dir: Path, tmp_path: Path, text: str) -> Path:
    """Put ``config.toml`` in place as a link into a dotfiles repository, and return the link's target."""
    target = tmp_path / "dotfiles" / "lsdsk.toml"
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")
    user_config_dir.mkdir(parents=True, exist_ok=True)
    (user_config_dir / "config.toml").symlink_to(target)
    return target


@pytest.mark.os_posix
def test_the_opt_out_edits_the_file_a_linked_config_leads_to_and_keeps_the_link(
    user_config_dir: Path, tmp_path: Path
) -> None:
    target = _linked_config(user_config_dir, tmp_path, "# mine\n[display]\npiped_width = 120\n")

    disable_offer()

    link = user_config_dir / "config.toml"
    assert link.is_symlink(), "the reader's link was replaced by a plain file"
    assert link.resolve() == target.resolve()
    text = target.read_text(encoding="utf-8")
    assert "# mine" in text
    parsed = tomllib.loads(text)
    assert parsed["display"]["offer_desktop_shortcut"] is False
    assert parsed["display"]["piped_width"] == 120


@pytest.mark.os_posix
def test_install_shortcut_with_a_linked_config_writes_the_icon_beside_the_link(
    user_config_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _linked_config(user_config_dir, tmp_path, "[display]\npiped_width = 120\n")
    recording = _RecordingShortcuts()

    install_shortcut(output_format=OutputFormat.JSON, seams=_seams(recording))

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert recording.created is not None
    assert recording.created[1] == user_config_dir / "lsdsk.png"
    assert (user_config_dir / "lsdsk.png").is_file()
    assert not (target.parent / "lsdsk.png").exists(), "the icon went into the dotfiles repository"


@pytest.mark.os_posix
def test_the_offer_creates_and_opts_out_through_a_linked_config(
    user_config_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both answers that write reach the file, and neither takes the run down."""
    target = _linked_config(user_config_dir, tmp_path, "[display]\n")
    recording = _RecordingShortcuts()

    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.CREATE, seams=_seams(recording))
    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.NEVER, seams=_seams(recording))

    assert recording.created is not None
    assert recording.created[1] == user_config_dir / "lsdsk.png"
    assert tomllib.loads(target.read_text(encoding="utf-8"))["display"]["offer_desktop_shortcut"] is False
    assert "will not offer the shortcut again" in capsys.readouterr().out


def test_the_user_config_path_is_the_one_the_loader_reads(user_config_dir: Path) -> None:
    assert user_config_path() == user_config_dir / "config.toml"


def test_a_platform_with_no_user_layer_names_no_user_config_path() -> None:
    """The library reads no user layer there, so a write would land somewhere nothing reads."""
    with pytest.raises(NoUserConfigPathError) as refused:
        user_config_path(platform="sunos5")

    assert isinstance(refused.value, OSError)
    assert "sunos5" in str(refused.value)


@pytest.mark.os_linux
def test_a_relative_xdg_config_home_names_no_user_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linux_config_home_set_to: Callable[[str], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A relative base would put the file wherever lsdsk was started, so the command refuses with 74."""
    monkeypatch.chdir(tmp_path)
    linux_config_home_set_to("relative/config")

    with pytest.raises(NoUserConfigPathError) as refused:
        user_config_path()
    assert "is not an absolute path" in str(refused.value)
    with pytest.raises(SystemExit) as leaving:
        install_shortcut(output_format=OutputFormat.JSON, seams=_seams(_RecordingShortcuts()))

    assert leaving.value.code == ExitCode.IO_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["type"] == "IO_ERROR"
    assert "is not an absolute path" in envelope["error"]["message"]
    assert not (tmp_path / "relative").exists(), "a file was written relative to the working directory"


@pytest.mark.os_linux
def test_the_offer_warns_rather_than_aborting_when_no_user_config_path_can_be_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linux_config_home_set_to: Callable[[str], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    linux_config_home_set_to("relative/config")

    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.NEVER, seams=_seams(_RecordingShortcuts()))

    assert "Could not save the do-not-offer setting" in capsys.readouterr().err
    assert not (tmp_path / "relative").exists()
