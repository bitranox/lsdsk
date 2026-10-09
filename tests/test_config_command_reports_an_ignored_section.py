"""``lsdsk config`` says so when a section it displays is one the tool ignores."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _write_user_config(user_config_dir_under: Callable[[str], Path], text: str) -> None:
    home = user_config_dir_under("ignored")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    get_config.cache_clear()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("line", ["history = false", 'thresholds = "x"', "display = 3"])
def test_the_json_envelope_lists_an_ignored_section_as_skipped(
    line: str,
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``history = false`` was shown as the user layer under ``ok: true, skipped: []``."""
    _write_user_config(user_config_dir_under, f"{line}\n")
    try:
        exit_code = cli_mod.main(["config", "--format", "json"], services_factory=build_production)
    finally:
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    envelope = json.loads(captured.out)
    section = line.split(" =", maxsplit=1)[0]
    assert envelope["ok"] is False
    assert [entry for entry in envelope["skipped"] if entry.startswith(f"Ignored: {section}=")], envelope["skipped"]
    assert "not a table" in " ".join(envelope["skipped"])


@pytest.mark.os_agnostic
def test_the_human_view_marks_the_ignored_section(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The page that shows ``history = false`` as the user layer also says it decides nothing."""
    _write_user_config(user_config_dir_under, "history = false\n")
    try:
        exit_code = cli_mod.main(["config", "--section", "history"], services_factory=build_production)
    finally:
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "Ignored: history=false: not a table. Using the shipped [history] section." in captured.out, captured.out


@pytest.mark.os_agnostic
def test_a_real_table_is_neither_marked_nor_skipped(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: only a non-table is ignored, so a real section keeps ``ok`` true."""
    _write_user_config(user_config_dir_under, "[history]\nenabled = true\n")
    try:
        exit_code = cli_mod.main(["config", "--format", "json"], services_factory=build_production)
    finally:
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    envelope = json.loads(captured.out)
    assert envelope["ok"] is True
    assert envelope["skipped"] == []
