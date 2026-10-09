"""A non-boolean ``default_permissions.enabled`` falls back to true and says so."""

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

ENABLED_VARIABLE = "LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__ENABLED"


@pytest.mark.os_agnostic
def test_a_non_boolean_enabled_from_the_environment_does_not_end_config_deploy(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``enabled=maybe`` reached pydantic's bool parser: exit 70 and an empty stdout."""
    user_config_dir_under("enabled")
    monkeypatch.setenv(ENABLED_VARIABLE, "maybe")
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(
            ["config-deploy", "--target", "user", "--format", "json"], services_factory=build_production
        )
    finally:
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert json.loads(captured.out)["ok"] is True
    said = " ".join(captured.err.split())
    assert "ignoring lib_layered_config.default_permissions.enabled=maybe" in said, said
    assert "Using true" in said, said
