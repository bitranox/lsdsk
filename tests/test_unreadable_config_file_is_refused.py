"""A user config file the OS refuses to read is refused with an envelope, not a bare exception."""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _root_reads_anything() -> bool:
    """Whether this process ignores file modes, so a mode-000 file refuses nobody."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.os_posix
def test_an_unreadable_user_config_is_refused_with_a_json_envelope(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mode 000 on ``config.toml`` used to leave a bare ``PermissionError`` and empty stdout.

    ``_load_or_refuse`` caught only the layered library's ``ConfigError``, so the
    ``OSError`` the read raised escaped to the last-resort handler: no envelope
    under ``--format json`` and a one-line ``PermissionError: ...``.
    """
    if _root_reads_anything():
        pytest.skip("root reads a mode-000 file, so there is no refusal to answer")
    home = user_config_dir_under("unreadable")
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    config.write_text("[display]\n", encoding="utf-8")
    config.chmod(0)
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(["disks", "--format", "json"], services_factory=build_production)
    finally:
        config.chmod(stat.S_IRUSR | stat.S_IWUSR)
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == ExitCode.PERMISSION_DENIED, captured.err
    envelope = json.loads(captured.out)
    assert envelope["ok"] is False
    assert envelope["command"] == "disks"
    assert envelope["error"]["type"] == "PERMISSION_DENIED"
    assert str(config) in envelope["error"]["message"]
    assert captured.err.startswith("Error:"), captured.err
