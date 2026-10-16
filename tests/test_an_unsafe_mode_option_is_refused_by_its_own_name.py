"""An unsafe ``--dir-mode`` or ``--file-mode`` is refused under the option's own name.

The deploy's refusal of an unsafe explicit mode came through as the library's
own line, ``dir_mode: unsafe directory mode 0o777: ...``, which names a
parameter a reader of ``config-deploy`` has no way to pass. The refusal now
names ``--dir-mode`` / ``--file-mode``. It still leaves with exit 22: the text
parsed and the value was refused as unsafe, which is ``INVALID_ARGUMENT``,
while text that does not parse stays a usage error, exit 2 (user decision on
backlog [560], 2026-10-16: reword, keep exit 22).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("option", "text", "library_name", "kind"),
    [
        pytest.param("--dir-mode", "0o777", "dir_mode", "directory", id="dir-mode"),
        pytest.param("--file-mode", "0o666", "file_mode", "file", id="file-mode"),
    ],
)
def test_an_unsafe_mode_option_is_refused_by_its_own_name(
    option: str,
    text: str,
    library_name: str,
    kind: str,
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli_mod.main(["config-deploy", "--target", "user", option, text], services_factory=build_production)

    err = capsys.readouterr().err
    assert code == ExitCode.INVALID_ARGUMENT, err
    assert f"{option}: unsafe {kind} mode" in err, err
    assert f"{library_name}:" not in err, err
    assert not (user_config_dir / "config.toml").exists(), "a refused mode deployed anyway"


def test_both_unsafe_mode_options_are_each_refused_by_their_own_name(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The library checks both before refusing, one line each; both lines carry the option."""
    argv = ["config-deploy", "--target", "user", "--dir-mode", "0o777", "--file-mode", "0o666"]
    code = cli_mod.main(argv, services_factory=build_production)

    err = capsys.readouterr().err
    assert code == ExitCode.INVALID_ARGUMENT, err
    assert "--dir-mode: unsafe directory mode" in err, err
    assert "--file-mode: unsafe file mode" in err, err
    assert "dir_mode:" not in err, err
    assert "file_mode:" not in err, err


def test_a_safe_mode_option_still_deploys(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: the same command with a safe mode deploys, so the refusal above is the mode's."""
    code = cli_mod.main(["config-deploy", "--target", "user", "--dir-mode", "0o700"], services_factory=build_production)

    assert code == 0, capsys.readouterr().err
    assert (user_config_dir / "config.toml").exists()
