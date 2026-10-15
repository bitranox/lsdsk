"""Two edges of how ``config-deploy`` hands ``--set`` permission settings to the deploy.

``enabled`` is lsdsk's to settle: the command line's ``--permissions``/``--no-permissions``, else
the configured switch with a warning and a fallback when it is not a boolean. The settled answer
is handed to the deploy, and a ``--set ...enabled`` must never reach it beside that answer, or
the library judges a value lsdsk already replaced and refuses a deploy lsdsk announced.

And the deploy-anyway hint names ``--dir-mode``/``--file-mode``, which help only for a mode the
deploy read from a configuration FILE. A ``--set`` mode is validated even when both explicit
modes are given, so a refusal of one carries no such hint, and lsdsk must not add one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest

SECTION = "lib_layered_config.default_permissions"


def _run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run the real entry point with the production wiring; the code and stderr on one line."""
    get_config.cache_clear()
    try:
        exit_code = cli_mod.main(list(argv), services_factory=build_production)
    finally:
        get_config.cache_clear()
    return exit_code, " ".join(capsys.readouterr().err.split())


def test_a_set_enabled_that_is_not_a_boolean_falls_back_and_deploys(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fallback lsdsk announces is the one the deploy gets; the raw value never reaches it."""
    exit_code, said = _run(["--set", f"{SECTION}.enabled=maybe", "config-deploy", "--target", "user"], capsys)

    assert exit_code == 0, said
    assert f"ignoring {SECTION}.enabled=maybe" in said, said
    assert (user_config_dir / "config.toml").is_file()


def test_a_refused_set_mode_is_not_followed_by_the_deploy_anyway_hint(
    user_config_dir: Path,
    managed_traceback_state: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Giving --dir-mode and --file-mode would not deploy past a refused ``--set`` mode."""
    exit_code, said = _run(["--set", f"{SECTION}.user_file=0o777", "config-deploy", "--target", "user"], capsys)

    assert exit_code == ExitCode.INVALID_ARGUMENT, said
    assert f"{SECTION}.user_file" in said, said
    assert "to deploy anyway" not in said, said
    assert not (user_config_dir / "config.toml").exists(), "a refused deploy wrote the file"
