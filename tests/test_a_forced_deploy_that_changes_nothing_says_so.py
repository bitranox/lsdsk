"""A forced deploy that writes nothing does not tell the reader to use ``--force``.

The library skips a file that already holds exactly the content it would write, with or without
``--force``. So ``config-deploy --force`` over an unchanged deployment wrote nothing and then said
"Use --force to overwrite existing configuration files" to somebody who had just used it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest

USE_FORCE = "Use --force to overwrite existing configuration files."


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    get_config.cache_clear()
    try:
        code = cli_mod.main(["--no-record", *argv], services_factory=build_production)
    finally:
        get_config.cache_clear()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_forced_deploy_over_identical_files_says_they_already_match(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_config_dir.mkdir(parents=True, exist_ok=True)
    first, _out, err = _run(["config-deploy", "--target", "user"], capsys)
    assert first == 0, err

    code, out, err = _run(["config-deploy", "--target", "user", "--force"], capsys)

    assert code == 0, err
    assert USE_FORCE not in out, out
    assert "already hold" in out, out


def test_an_unforced_deploy_over_existing_files_still_offers_force(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The control: without --force the advice is the way to overwrite, so it stays."""
    user_config_dir.mkdir(parents=True, exist_ok=True)
    first, _out, err = _run(["config-deploy", "--target", "user"], capsys)
    assert first == 0, err

    code, out, err = _run(["config-deploy", "--target", "user"], capsys)

    assert code == 0, err
    assert USE_FORCE in out, out
