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


@pytest.mark.parametrize("value", ["nonsense", "0o640"])
def test_a_mode_in_a_file_the_deploy_rewrites_is_named_once(
    value: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A user-file ``user_file`` cannot decide the mode of its own replacement, so it is named.

    The deploy skips every file it writes when it reads its modes, which is right - a file is
    about to be replaced by the shipped copy - and it means a ``user_file`` written into the very
    copy ``--target user`` rewrites decides nothing. ``lsdsk config`` still reports it as in force,
    so the deploy says it went unused, valid or not: an invalid one is the case that matters,
    because nothing else ever mentions it.
    """
    copy = _deploy_once(user_config_dir, capsys)
    text = copy.read_text(encoding="utf-8")
    assert 'user_file = "0o600"' in text, "the premise: the copy carries the shipped user_file"
    copy.write_text(text.replace('user_file = "0o600"', f'user_file = "{value}"'), encoding="utf-8", newline="\n")

    code, err = _run(["config-deploy", "--target", "user", "--force"], capsys)

    assert code == 0, err
    named = _ignored(err)
    assert len(named) == NAMED_ONCE, err
    assert all(line.startswith(f"{IGNORED}user_file={value} from ") for line in named), err
    assert all("rewrites" in line for line in named), err


@pytest.mark.os_posix
def test_an_invalid_mode_in_a_file_the_deploy_reads_is_refused_by_name(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: where the user file is NOT rewritten, the deploy reads its value and refuses it.

    POSIX only: Windows applies no mode, so its deploy reads none and has nothing to refuse.
    """
    copy = _deploy_once(user_config_dir, capsys)
    text = copy.read_text(encoding="utf-8")
    copy.write_text(text.replace('user_file = "0o600"', 'user_file = "nonsense"'), encoding="utf-8", newline="\n")

    code, err = _run(["config-deploy", "--target", "app"], capsys)

    assert code == ExitCode.INVALID_ARGUMENT, err
    assert "user_file" in err, err
    assert _ignored(err) == [], err


def test_a_mode_in_a_kept_file_is_named_without_claiming_the_file_is_rewritten(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file a deploy keeps still decides no mode, and the warning says it was KEPT.

    A deploy without ``--force`` leaves an existing ``config.toml`` alone, yet its read of the
    modes still skips that file, so a ``user_file`` written there decides nothing about the
    companions it creates. The warning used to give the rewritten file's reason, "config-deploy
    rewrites that file", for a file the same run reported as already existing and left untouched.
    """
    user_config_dir.mkdir(parents=True, exist_ok=True)
    own = user_config_dir / "config.toml"
    own.write_text(f'[{SECTION}]\nuser_file = "0o640"\n', encoding="utf-8", newline="\n")

    code, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert own.read_text(encoding="utf-8") == f'[{SECTION}]\nuser_file = "0o640"\n', "the premise: the file is kept"
    named = _ignored(err)
    assert len(named) == NAMED_ONCE, err
    assert all(line.startswith(f"{IGNORED}user_file=0o640 from ") for line in named), err
    assert all("rewrites" not in line and "keeps that file" in line for line in named), err
