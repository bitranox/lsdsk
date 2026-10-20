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
from lsdsk.adapters.config.offer import disable_offer
from lsdsk.adapters.config.tunables import get_display_settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


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
