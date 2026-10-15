"""An all-digit ``history.path`` is the file name the operator wrote, not a refused value.

lib_layered_config reads an unquoted environment or ``.env`` value that spells an
integer losslessly as that int, so ``LSDSK___HISTORY__PATH=4711`` reaches the
``[history]`` section as the number 4711. The logging text settings already turn such
a number back into its text; ``history.path`` refused it as "not a path" and the store
went to the per-user state file instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lsdsk.adapters.cli import cli
from lsdsk.adapters.config.loader import get_config

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest
    from click.testing import CliRunner

PATH_VARIABLE = "LSDSK___HISTORY__PATH"
SNAPSHOT = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


def test_an_all_digit_history_path_writes_the_store_under_that_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
    clear_config_cache: None,
) -> None:
    """``LSDSK___HISTORY__PATH=4711`` records into ``./4711`` without a warning."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(PATH_VARIABLE, "4711")
    get_config.cache_clear()
    delivered = get_config().get("history.path")
    assert type(delivered) is int and delivered == 4711, (
        f"premise: the library no longer delivers an int: {delivered!r}"
    )

    result = cli_runner.invoke(cli, ["record", "--replay", str(SNAPSHOT)], obj=production_factory)

    assert result.exit_code == 0, (result.stderr or "")[-400:]
    assert "ignoring history.path" not in (result.stderr or ""), result.stderr
    assert (tmp_path / "4711").is_file(), sorted(path.name for path in tmp_path.iterdir())


def test_a_bool_history_path_is_still_refused(
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    production_factory: Callable[[], object],
    clear_config_cache: None,
) -> None:
    """The control: a bool is an int in Python, and is not a file name anybody wrote."""
    monkeypatch.setenv(PATH_VARIABLE, "true")
    get_config.cache_clear()
    assert get_config().get("history.path") is True, "premise: the library no longer delivers a bool"

    result = cli_runner.invoke(cli, ["--no-record", "info"], obj=production_factory)

    assert "ignoring history.path=true" in " ".join((result.stderr or "").split()), result.stderr
