"""A directory standing where a configuration file belongs is refused, not read as "already there"."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable


def _generated_layout(scratch: Path, capsys: pytest.CaptureFixture[str]) -> list[Path]:
    """The relative paths ``config-generate-examples`` writes on this platform, learned by running it."""
    code = cli_mod.main(
        ["config-generate-examples", "--destination", str(scratch), "--format", "json"],
        services_factory=build_production,
    )
    assert code == 0, capsys.readouterr().err
    written = json.loads(capsys.readouterr().out)["data"]["generated"]
    return [Path(each).relative_to(scratch) for each in written]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("force", [False, True])
def test_a_directory_at_a_generated_example_path_is_refused_before_anything_is_written(
    force: bool,
    managed_traceback_state: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without ``--force`` the example was omitted under "all already exist"; with it, it raised late."""
    layout = _generated_layout(tmp_path / "learn", capsys)
    destination = tmp_path / "real"
    (destination / layout[0]).mkdir(parents=True)

    argv = ["config-generate-examples", "--destination", str(destination), "--format", "json"]
    exit_code = cli_mod.main([*argv, *(["--force"] if force else [])], services_factory=build_production)

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CONFIG_ERROR, captured.err
    envelope = json.loads(captured.out)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CONFIG_ERROR"
    assert str(destination / layout[0]) in envelope["error"]["message"]
    assert "directory" in envelope["error"]["message"]
    written = [p for p in destination.rglob("*") if p.is_file()]
    assert written == [], f"a refusal that had already written: {written}"


@pytest.mark.os_agnostic
def test_a_directory_at_the_config_deploy_target_is_refused(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``config-deploy`` reported ``written`` for the ``config.d`` files and passed over the directory."""
    home = user_config_dir_under("deploy-target")
    (home / "config.toml").mkdir(parents=True)
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(
            ["config-deploy", "--target", "user", "--format", "json"], services_factory=build_production
        )
    finally:
        get_config.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CONFIG_ERROR, captured.err
    envelope = json.loads(captured.out)
    assert envelope["ok"] is False
    assert str(home / "config.toml") in envelope["error"]["message"]
    assert "directory" in envelope["error"]["message"]


@pytest.mark.os_agnostic
def test_a_regular_existing_file_is_still_just_already_present(
    managed_traceback_state: None,
    user_config_dir_under: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: only a non-file is refused; a file already there keeps its quiet skip."""
    home = user_config_dir_under("deploy-file")
    home.mkdir(parents=True)
    (home / "config.toml").write_text("[display]\n", encoding="utf-8")
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
