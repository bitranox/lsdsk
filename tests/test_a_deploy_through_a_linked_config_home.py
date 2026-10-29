"""A deploy's warnings hold when the configuration directory is reached through a symlink.

A home directory on another disk, a dotfile manager or a container bind leaves the user's
configuration directory behind a link, so the paths the loader records as a value's origin and
the paths the deploy reports as written name one file two ways. Both warnings compare the two
sets after resolving each side; with either side left unresolved, the override of a value the
reader wrote and the mode a kept file sets were dropped in silence while the deploy reported
success.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

#: The base every test reaches its configuration through, and the directory it leads to.
LINK = "cfglink"
REAL = "real"
SECTION = "lib_layered_config.default_permissions"


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    get_config.cache_clear()
    try:
        code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    return code, capsys.readouterr().err


def _linked_config_dir(tmp_path: Path, user_config_dir_under: Callable[[str], Path]) -> Path:
    """The user configuration directory, under a base that is a link to another directory.

    Returns:
        The directory as the loader reaches it, through the link.
    """
    (tmp_path / REAL).mkdir()
    try:
        (tmp_path / LINK).symlink_to(tmp_path / REAL, target_is_directory=True)
    except OSError:
        # Windows refuses a symbolic link without SeCreateSymbolicLinkPrivilege or Developer
        # Mode; the elevated CI runner has the privilege, an ordinary desktop session may not.
        if sys.platform != "win32":
            raise
        pytest.skip("creating a symbolic link needs SeCreateSymbolicLinkPrivilege or Developer Mode")
    directory = user_config_dir_under(LINK)
    directory.mkdir(parents=True, exist_ok=True)
    assert directory.resolve() != directory, "the premise: the directory is reached through the link"
    return directory


def test_a_value_a_new_companion_overrides_is_named_through_a_linked_config_home(
    tmp_path: Path,
    user_config_dir_under: Callable[[str], Path],
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = _linked_config_dir(tmp_path, user_config_dir_under)
    (directory / "config.toml").write_text("[display]\nwwn_width = 40\n", encoding="utf-8", newline="\n")

    code, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert (directory / "config.d" / "70-display.toml").is_file(), "the premise: the deploy created the companion"
    named = [line for line in err.splitlines() if line.startswith("Warning: display.wwn_width=40 in ")]
    assert len(named) == 1, err
    assert " is now overridden by " in named[0], named
    assert "70-display.toml, which this deploy created and which sets 24." in named[0], named


@pytest.mark.os_posix
def test_a_mode_in_a_kept_file_is_named_through_a_linked_config_home(
    tmp_path: Path,
    user_config_dir_under: Callable[[str], Path],
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """POSIX only: Windows applies no mode, so its deploy reads none and has none to name."""
    directory = _linked_config_dir(tmp_path, user_config_dir_under)
    own = directory / "config.toml"
    own.write_text(f'[{SECTION}]\nuser_file = "0o640"\n', encoding="utf-8", newline="\n")

    code, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert own.read_text(encoding="utf-8") == f'[{SECTION}]\nuser_file = "0o640"\n', "the premise: the file is kept"
    named = [line for line in err.splitlines() if line.startswith(f"Warning: ignoring {SECTION}.user_file=0o640 ")]
    assert len(named) == 1, err
    assert ": config-deploy keeps that file, because it exists and --force was not given" in named[0], named
