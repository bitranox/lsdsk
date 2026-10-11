"""``--history-file ""`` is not given, as an empty ``history.path`` is not.

CONFIG.md says an empty ``history.path`` means the per-user default. The option
read the empty string as the current directory and refused it as not a regular file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from lsdsk.adapters.cli import cli
from lsdsk.composition import build_production

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


@pytest.mark.os_agnostic
def test_an_empty_history_file_is_the_default_not_a_usage_error(clear_config_cache: None) -> None:
    """The run proceeds exactly as it does with no ``--history-file`` at all."""
    runner = CliRunner()
    given = runner.invoke(
        cli, ["--history-file", "", "--no-record", "--replay", str(CAPTURE), "disks"], obj=build_production
    )
    absent = runner.invoke(cli, ["--no-record", "--replay", str(CAPTURE), "disks"], obj=build_production)

    assert "not a regular file" not in given.stderr, given.stderr
    assert given.exit_code == absent.exit_code
    assert given.stderr == absent.stderr


@pytest.mark.os_agnostic
def test_a_history_file_that_is_a_directory_is_still_refused(clear_config_cache: None, tmp_path: Path) -> None:
    """The control: only the empty string changed meaning; a real directory is still refused."""
    result = CliRunner().invoke(
        cli, ["--history-file", str(tmp_path), "--no-record", "--replay", str(CAPTURE), "disks"], obj=build_production
    )

    assert result.exit_code == 2
    assert "--history-file" in result.output
