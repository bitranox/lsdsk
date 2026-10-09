"""``lsdsk.get_config`` names every exception a caller must handle, and raises exactly those."""

from __future__ import annotations

import inspect
import os
import stat
from typing import TYPE_CHECKING

import pytest
from lib_layered_config import LayerLoadError

import lsdsk
from lsdsk.adapters.config.loader import get_config as loader_get_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DOCUMENTED = ("ValueError", "LayerLoadError", "PermissionError", "OSError")


def _raises_section() -> str:
    doc = inspect.getdoc(lsdsk.get_config) or ""
    assert "Raises:" in doc, f"help(lsdsk.get_config) has no Raises section: {doc!r}"
    return doc.split("Raises:", 1)[1]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", DOCUMENTED)
def test_the_public_get_config_documents_each_exception_it_can_raise(name: str) -> None:
    """``help(lsdsk.get_config)`` listed none, though four kinds of failure reach a caller."""
    assert name in _raises_section()


@pytest.mark.os_agnostic
def test_the_call_signature_documents_them_too() -> None:
    """The method a reader opens from the class carries the same section."""
    doc = inspect.getdoc(type(lsdsk.get_config).__call__) or ""
    assert all(name in doc.split("Raises:", 1)[-1] for name in DOCUMENTED), doc


@pytest.mark.os_agnostic
def test_a_malformed_config_file_raises_the_documented_layer_load_error(
    user_config_dir_under: Callable[[str], Path],
) -> None:
    """The documented type is the one raised, not a lookalike."""
    home = user_config_dir_under("malformed")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("[display\n", encoding="utf-8")
    loader_get_config.cache_clear()
    try:
        with pytest.raises(LayerLoadError, match="not valid TOML"):
            lsdsk.get_config()
    finally:
        loader_get_config.cache_clear()


@pytest.mark.os_posix
def test_an_unreadable_config_file_raises_the_documented_permission_error(
    user_config_dir_under: Callable[[str], Path],
) -> None:
    """The permission case of the same contract."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so there is no refusal to raise")
    home = user_config_dir_under("unreadable")
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    config.write_text("[display]\n", encoding="utf-8")
    config.chmod(0)
    loader_get_config.cache_clear()
    try:
        with pytest.raises(PermissionError, match=r"config\.toml"):
            lsdsk.get_config()
    finally:
        config.chmod(stat.S_IRUSR | stat.S_IWUSR)
        loader_get_config.cache_clear()
