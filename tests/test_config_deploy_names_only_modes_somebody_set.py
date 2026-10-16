"""``config-deploy`` names an unused permission mode only when it would have decided something.

``config-deploy --target user`` writes a copy of the shipped ``40-layered-config.toml`` into the
user's directory, and that copy carries the shipped ``app_*`` and ``host_*`` modes. A user file
cannot set those, so every later deploy named all four as ignored - values nobody configured,
which the deploy was going to use anyway because they ARE the shipped ones. A mode is now named
only when the value ``lsdsk config`` reports as in force differs from the one the deploy reads, so
a mode somebody really wrote is still named, once. The warnings follow the deploy rather than
precede it: a refused deploy answers with its refusal alone.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

SECTION = "lib_layered_config.default_permissions"
IGNORED = f"Warning: ignoring {SECTION}."

#: Windows applies no mode, so its deploy reads none from any layer and has none to name.
NAMED_ONCE = 1 if os.name == "posix" else 0


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    get_config.cache_clear()
    try:
        code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    return code, capsys.readouterr().err


def _ignored(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith(IGNORED)]


def _deploy_once(user_config_dir: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """Deploy the user files, as the reader would before ever configuring anything."""
    user_config_dir.mkdir(parents=True, exist_ok=True)
    code, err = _run(["config-deploy", "--target", "user"], capsys)
    assert code == 0, err
    copy = user_config_dir / "config.d" / "40-layered-config.toml"
    assert 'app_file = "0o644"' in copy.read_text(encoding="utf-8"), "the premise: the copy carries app modes"
    return copy


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["config-deploy", "--target", "user"], id="user"),
        pytest.param(["config-deploy", "--target", "user", "--force"], id="user-force"),
        pytest.param(["config-deploy", "--target", "app"], id="app"),
        pytest.param(["config-deploy", "--target", "host", "--force"], id="host-force"),
    ],
)
def test_a_later_deploy_names_no_mode_the_first_deploy_copied(
    argv: list[str],
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _deploy_once(user_config_dir, capsys)

    code, err = _run(argv, capsys)

    assert code == 0, err
    assert _ignored(err) == [], err


@pytest.mark.parametrize("target", ["user", "app"])
def test_a_mode_somebody_wrote_into_the_user_copy_is_still_named_once(
    target: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    copy = _deploy_once(user_config_dir, capsys)
    text = copy.read_text(encoding="utf-8")
    copy.write_text(text.replace('app_file = "0o644"', 'app_file = "0o640"'), encoding="utf-8", newline="\n")

    code, err = _run(["config-deploy", "--target", target], capsys)

    assert code == 0, err
    named = _ignored(err)
    assert len(named) == NAMED_ONCE, err
    assert all(line.startswith(f"{IGNORED}app_file=0o640 from ") for line in named), err


def test_a_refused_deploy_names_no_mode_first(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refusal stops the deploy, so a sentence about how it would have read its modes is moot."""
    copy = _deploy_once(user_config_dir, capsys)
    text = copy.read_text(encoding="utf-8")
    copy.write_text(text.replace('app_file = "0o644"', 'app_file = "0o640"'), encoding="utf-8", newline="\n")

    code, err = _run([f"--set={SECTION}.user_file=0o666", "config-deploy", "--target", "app"], capsys)

    assert code == ExitCode.INVALID_ARGUMENT, err
    assert _ignored(err) == [], err
