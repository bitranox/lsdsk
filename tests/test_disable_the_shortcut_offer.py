"""Turning the desktop-shortcut offer off writes ``false`` into the user config.

The interactive offer's "never" answer calls ``disable_offer``, which must set
``display.offer_desktop_shortcut = false`` in the user's own ``config.toml`` and
leave everything else the reader put there untouched. These drive the real
writer against the directory this platform's loader reads, and read the result
back both as plain TOML and through the real configuration loader, so the key a
reader opts out with is the key the tool then obeys.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.config.offer import DisplayNotATableError, disable_offer
from lsdsk.adapters.config.tunables import get_display_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from lsdsk.domain.shortcut import ShortcutWritten


@pytest.mark.os_agnostic
def test_disable_offer_writes_false_into_a_freshly_created_user_config(user_config_dir: Path) -> None:
    """With no user file, the writer creates one carrying exactly the opt-out."""
    written = disable_offer()

    assert written == user_config_dir / "config.toml"
    assert written.is_file()
    parsed = tomllib.loads(written.read_text(encoding="utf-8"))
    assert parsed["display"]["offer_desktop_shortcut"] is False


@pytest.mark.os_agnostic
def test_the_disabled_offer_is_what_the_loader_then_reads(user_config_dir: Path) -> None:
    """The write is not enough on its own; the merged configuration must obey it."""
    assert user_config_dir  # the fixture seeds the directory the loader reads
    disable_offer()
    get_config.cache_clear()
    assert get_display_settings(get_config()).offer_desktop_shortcut is False


@pytest.mark.os_agnostic
def test_disable_offer_keeps_the_users_comments_and_other_keys(
    seed_user_config: Callable[[str], Path],
) -> None:
    """A reader's own file is edited in place, not rewritten: comments and keys survive."""
    seed_user_config("# my own note\n[display]\npiped_width = 200\noffer_desktop_shortcut = true\n")

    written = disable_offer()

    text = written.read_text(encoding="utf-8")
    assert "# my own note" in text
    parsed = tomllib.loads(text)
    assert parsed["display"]["piped_width"] == 200
    assert parsed["display"]["offer_desktop_shortcut"] is False


@pytest.mark.os_agnostic
def test_disable_offer_adds_the_key_when_no_display_table_exists(
    seed_user_config: Callable[[str], Path],
) -> None:
    """A file with no ``[display]`` table gains one; its other sections are untouched."""
    seed_user_config("[thresholds]\nwear_warning_percent = 70\n")

    written = disable_offer()

    parsed = tomllib.loads(written.read_text(encoding="utf-8"))
    assert parsed["thresholds"]["wear_warning_percent"] == 70
    assert parsed["display"]["offer_desktop_shortcut"] is False


@pytest.mark.parametrize(
    "text",
    ["[[display]]\npiped_width = 200\n", "display = 5\n", 'display = ["a"]\n'],
    ids=["array-of-tables", "integer", "array"],
)
def test_a_display_entry_that_is_not_a_table_is_refused_and_left_as_it_was(
    seed_user_config: Callable[[str], Path], text: str
) -> None:
    """Replacing it with a table would throw away what the reader put there, so nothing is written."""
    path = seed_user_config(text)
    before = path.read_bytes()

    with pytest.raises(DisplayNotATableError) as refused:
        disable_offer()

    assert path.read_bytes() == before
    assert "display" in str(refused.value)


def test_the_offer_reports_a_display_entry_that_is_not_a_table_as_a_warning(
    seed_user_config: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, offer_shortcut
    from lsdsk.adapters.config.tunables import DisplaySettings
    from lsdsk.adapters.uv_bootstrap import UvInstallResult
    from lsdsk.domain.enums import ShortcutChoice

    class _Desktop:
        def exists(self) -> bool:
            return False

        def has_desktop(self) -> bool:
            return True

        def create(self, *, launch: Sequence[str], icon: Path | None) -> ShortcutWritten:
            raise AssertionError("never asked to create")

        def no_desktop_reason(self) -> str:
            return f"no desktop folder at {Path('/desktop')}"

    path = seed_user_config("[[display]]\npiped_width = 200\n")
    before = path.read_bytes()
    seams = ShortcutSeams(
        shortcuts=_Desktop(),
        uvx=lambda: None,
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
    )

    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.NEVER, seams=seams)

    assert path.read_bytes() == before
    assert "Could not save the do-not-offer setting" in capsys.readouterr().err


@pytest.mark.os_posix
def test_the_opt_out_file_is_owner_only(user_config_dir: Path) -> None:
    """The reader's file may hold secrets in other keys, so the write leaves it at 0600."""
    assert user_config_dir
    written = disable_offer()

    assert written.stat().st_mode & 0o777 == 0o600


#: A file tomlkit edits but the standard library's TOML reader refuses: a trailing
#: comma in an inline table is TOML 1.1, which tomllib reads only from Python 3.15.
#: tomlkit keeps the text as it was, so the written file carries it too.
_TOMLKIT_ONLY = "[extra]\nx = { a = 1, }\n"


def _skip_where_tomllib_reads_toml_1_1() -> None:
    """The read-back guard has nothing to catch where the standard library reads the file."""
    try:
        tomllib.loads(_TOMLKIT_ONLY)
    except tomllib.TOMLDecodeError:
        return
    pytest.skip("this Python's tomllib reads TOML 1.1, so the written file reads back")


def test_a_written_file_that_does_not_read_back_as_toml_is_refused_at_the_write(
    seed_user_config: Callable[[str], Path],
) -> None:
    """The read-back is what turns a file the loader cannot read into a failure here.

    Without it the opt-out reported success into a file the configuration loader
    would refuse later, far from the write that made it.
    """
    _skip_where_tomllib_reads_toml_1_1()
    path = seed_user_config(_TOMLKIT_ONLY)

    with pytest.raises(tomllib.TOMLDecodeError) as refused:
        disable_offer()

    # The inline table's trailing comma, on the line the seed put it on.
    assert "(at line 2," in str(refused.value)
    # Raised by the read-back, after the write: tomlkit's own parse accepted the file.
    assert "offer_desktop_shortcut = false" in path.read_text(encoding="utf-8")


class _DesktopNeverAsked:
    """A desktop whose shortcut does not exist yet and is never created: the answer is "never"."""

    def exists(self) -> bool:
        return False

    def has_desktop(self) -> bool:
        return True

    def create(self, *, launch: Sequence[str], icon: Path | None) -> ShortcutWritten:
        raise AssertionError("never asked to create")

    def no_desktop_reason(self) -> str:
        return "a desktop is present"


def test_the_offer_warns_when_the_written_opt_out_does_not_read_back(
    seed_user_config: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The real writer's read-back failure degrades the offer to one warning, like any other failed opt-out."""
    from lsdsk.adapters.cli.commands.shortcut import ShortcutSeams, offer_shortcut
    from lsdsk.adapters.config.tunables import DisplaySettings
    from lsdsk.adapters.uv_bootstrap import UvInstallResult
    from lsdsk.domain.enums import ShortcutChoice

    _skip_where_tomllib_reads_toml_1_1()
    seed_user_config(_TOMLKIT_ONLY)
    seams = ShortcutSeams(
        shortcuts=_DesktopNeverAsked(),
        uvx=lambda: None,
        installed_lsdsk=lambda: None,
        install_uv=lambda: UvInstallResult(exit_code=0, uvx=None),
    )

    offer_shortcut(DisplaySettings(), ask_choice=lambda: ShortcutChoice.NEVER, seams=seams)

    assert "Could not save the do-not-offer setting" in capsys.readouterr().err
