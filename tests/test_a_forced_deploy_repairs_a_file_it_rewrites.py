"""``config-deploy --force`` replaces a broken configuration file it rewrites, instead of refusing over it.

The deploy writes fresh copies of the shipped files, and lib_layered_config's own read of the
permission modes skips every file the deploy is about to write: a file never decides its own
replacement. lsdsk read the whole configuration first, though, so a file that no longer parsed
stopped the very command that would have replaced it with 78, in the unprofiled and the profile
forms alike, and the documented repair (``config-deploy --target user --force``) could not run.

With ``--force``, a parse error in a file this deploy rewrites no longer blocks it: that file is
skipped while reading, one warning names it, and the fresh copy replaces it. An unreadable file
and a broken file the deploy does NOT rewrite still refuse.
"""

from __future__ import annotations

import json
import os
import stat
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.deploy import paths_the_deploy_writes
from lsdsk.adapters.config.loader import get_config, get_default_config_path
from lsdsk.composition import build_production
from lsdsk.domain.enums import DeployTarget

if TYPE_CHECKING:
    from collections.abc import Sequence

PROFILE = "x"
BROKEN = "[display\n"

#: Asked with ``hasattr`` because Windows has no ``geteuid``. Root reads a mode-000 file.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

#: The forced user deploy, unprofiled and with the profile named in each place.
_FORCED_SPELLINGS = (
    pytest.param([], ["config-deploy", "--target", "user", "--force"], None, id="unprofiled"),
    pytest.param(["--profile", PROFILE], ["config-deploy", "--target", "user", "--force"], PROFILE, id="global"),
    pytest.param([], ["config-deploy", "--target", "user", "--force", "--profile", PROFILE], PROFILE, id="sub"),
)


def _directory(user_config_dir: Path, profile: str | None) -> Path:
    return user_config_dir if profile is None else user_config_dir / "profile" / profile


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _warnings_naming(path: Path, err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith("Warning:") and str(path) in line]


def _parses(path: Path) -> bool:
    try:
        tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return False
    return True


@pytest.mark.parametrize(("before", "after", "profile"), _FORCED_SPELLINGS)
def test_a_forced_deploy_replaces_a_broken_file_it_rewrites(
    before: list[str],
    after: list[str],
    profile: str | None,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = _write(_directory(user_config_dir, profile) / "config.toml", BROKEN)

    exit_code, _out, err = _run([*before, *after], capsys)

    assert exit_code == 0, err
    assert len(_warnings_naming(broken, err)) == 1, err
    assert broken.read_bytes() == get_default_config_path().read_bytes()


@pytest.mark.parametrize(("before", "after", "profile"), _FORCED_SPELLINGS)
def test_a_broken_companion_file_the_deploy_rewrites_is_replaced_too(
    before: list[str],
    after: list[str],
    profile: str | None,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = _write(_directory(user_config_dir, profile) / "config.d" / "50-history.toml", BROKEN)

    exit_code, _out, err = _run([*before, *after], capsys)

    assert exit_code == 0, err
    assert len(_warnings_naming(broken, err)) == 1, err
    assert _parses(broken)


@pytest.mark.parametrize(("before", "after", "profile"), _FORCED_SPELLINGS)
def test_without_force_a_broken_file_still_refuses(
    before: list[str],
    after: list[str],
    profile: str | None,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: a deploy that keeps existing files does not rewrite this one."""
    broken = _write(_directory(user_config_dir, profile) / "config.toml", BROKEN)

    exit_code, _out, err = _run([*before, *[word for word in after if word != "--force"]], capsys)

    assert exit_code == ExitCode.CONFIG_ERROR, err
    assert broken.read_text(encoding="utf-8") == BROKEN


@pytest.mark.parametrize(("before", "after", "profile"), _FORCED_SPELLINGS)
def test_a_broken_file_the_deploy_does_not_rewrite_still_refuses(
    before: list[str],
    after: list[str],
    profile: str | None,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A companion file the package does not ship is the reader's own, and no deploy replaces it."""
    own = _write(_directory(user_config_dir, profile) / "config.d" / "99-mine.toml", BROKEN)

    exit_code, _out, err = _run([*before, *after], capsys)

    assert exit_code == ExitCode.CONFIG_ERROR, err
    assert str(own) in err
    assert own.read_text(encoding="utf-8") == BROKEN


def test_a_second_broken_file_the_deploy_does_not_rewrite_still_refuses(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skipping the file the deploy replaces must not carry a second, unrelated fault past the read."""
    rewritten = _write(user_config_dir / "config.toml", BROKEN)
    own = _write(user_config_dir / "config.d" / "99-mine.toml", BROKEN)

    exit_code, _out, err = _run(["config-deploy", "--target", "user", "--force"], capsys)

    assert exit_code == ExitCode.CONFIG_ERROR, err
    assert str(own) in err
    assert rewritten.read_text(encoding="utf-8") == BROKEN, "a refused deploy rewrote a file"


@pytest.mark.os_posix
def test_an_unreadable_file_the_deploy_rewrites_still_refuses_as_permission_denied(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if RUNNING_AS_ROOT:
        pytest.skip("root reads a mode-000 file, so there is no refusal to answer")
    unreadable = _write(user_config_dir / "config.toml", "[display]\n")
    unreadable.chmod(0)
    try:
        exit_code, _out, err = _run(["config-deploy", "--target", "user", "--force"], capsys)
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert exit_code == ExitCode.PERMISSION_DENIED, err


@pytest.mark.parametrize("profile", [None, PROFILE])
def test_the_paths_a_deploy_writes_are_the_ones_it_reports_writing(
    profile: str | None,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What lsdsk computes as rewritten is what the library really writes, on this platform."""
    user_config_dir.mkdir(parents=True, exist_ok=True)
    named = [] if profile is None else ["--profile", profile]

    exit_code, out, err = _run(["config-deploy", "--target", "user", *named, "--format", "json"], capsys)

    assert exit_code == 0, err
    written = {Path(path).resolve() for path in json.loads(out)["data"]["deployed"]}
    assert written, "the deploy wrote nothing, so there is nothing to compare"
    assert paths_the_deploy_writes((DeployTarget.USER,), profile=profile) == frozenset(written)
