"""A shipped table given as a single value is reported by ``config``, whichever table it is.

``history = false`` was listed as ignored, while ``lib_log_rich = "x"`` and
``lib_layered_config = "x"`` showed ``ok: true, skipped: []``: the report kept its
own list of three tables. It now asks the registry of what the package ships as a
table, and this test walks that registry rather than a list of its own.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config, shipped_table_paths
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

TABLES = shipped_table_paths()


def _as_a_value(path: str) -> str:
    """A config file giving `path` as the integer 5 rather than as a table."""
    parent, _, leaf = path.rpartition(".")
    return f"[{parent}]\n{leaf} = 5\n" if parent else f"{leaf} = 5\n"


def _skipped_by_config(
    text: str, user_config_dir_under: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> tuple[bool, list[str]]:
    home = user_config_dir_under("ignored")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(["config", "--format", "json"], services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    envelope = json.loads(captured.out)
    return envelope["ok"], envelope["skipped"]


def test_the_registry_names_the_tables_the_report_used_to_miss() -> None:
    """Liveness: the walk below is only meaningful if the tables that were missed are in it."""
    assert {"history", "lib_log_rich", "lib_layered_config", "lib_layered_config.default_permissions"} <= set(TABLES)


@pytest.mark.parametrize("path", TABLES)
def test_each_shipped_table_given_as_a_value_is_listed_as_skipped(
    path: str,
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``ok`` is false and the skipped list names the table, for every table the package ships."""
    ok, skipped = _skipped_by_config(_as_a_value(path), user_config_dir_under, capsys)

    assert ok is False
    assert [entry for entry in skipped if entry.startswith(f"Ignored: {path}=")], skipped


def test_a_file_that_gives_every_table_as_a_table_skips_nothing(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: real tables, including the nested ones, keep ``ok`` true."""
    text = "[lib_log_rich]\nconsole_level = 'INFO'\n[lib_layered_config.default_permissions]\nenabled = true\n"

    ok, skipped = _skipped_by_config(text, user_config_dir_under, capsys)

    assert ok is True
    assert skipped == []
