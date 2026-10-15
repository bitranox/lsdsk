"""An int mode that could be octal digits stops the run for EVERY mode key, from every source.

The startup refusal was pinned for ``app_directory`` from a ``.env`` file alone, so a loop that
checked only the first key, or dropped the ``host_*`` keys, passed the suite. The keys are
written out here rather than taken from ``permissions.MODE_KEYS``: a list derived from the code
under test agrees with it by construction, which is exactly how five of the six went unpinned.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    #: Puts the ambiguous value for one key into one source; returns the leading arguments.
    Seed = Callable[[str, Path, Path, pytest.MonkeyPatch], list[str]]

#: Every mode key the section has, three layers by two kinds, spelt out by hand.
MODE_KEYS_WRITTEN_OUT = (
    "app_directory",
    "app_file",
    "host_directory",
    "host_file",
    "user_directory",
    "user_file",
)

AMBIGUOUS = "755"


def _from_a_config_file(key: str, user_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    del tmp_path, monkeypatch
    user_config_dir.mkdir(parents=True, exist_ok=True)
    body = f"[lib_layered_config.default_permissions]\n{key} = {AMBIGUOUS}\n"
    (user_config_dir / "config.toml").write_text(body, encoding="utf-8", newline="\n")
    return []


def _from_a_dotenv_file(key: str, user_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    del user_config_dir, monkeypatch
    dotenv = tmp_path / "modes.env"
    dotenv.write_text(f"LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__{key.upper()}={AMBIGUOUS}\n", encoding="utf-8")
    return ["--env-file", str(dotenv)]


def _from_the_environment(
    key: str, user_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    del user_config_dir, tmp_path
    monkeypatch.setenv(f"LSDSK___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__{key.upper()}", AMBIGUOUS)
    return []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_from_a_config_file, id="config-file"),
        pytest.param(_from_a_dotenv_file, id="dotenv"),
        pytest.param(_from_the_environment, id="environment"),
    ],
)
@pytest.mark.parametrize("key", MODE_KEYS_WRITTEN_OUT)
def test_an_int_mode_of_octal_digits_stops_the_run_with_78(
    key: str,
    source: Seed,
    user_config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    leading = source(key, user_config_dir, tmp_path, monkeypatch)
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main([*leading, "config", "--format", "json"], services_factory=build_production)
    finally:
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CONFIG_ERROR, captured.err
    envelope = json.loads(captured.out)
    assert envelope["error"]["type"] == "CONFIG_ERROR"
    assert f"lib_layered_config.default_permissions.{key}" in envelope["error"]["message"], envelope
