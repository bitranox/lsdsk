"""A pytest run leaves every coverage database it did not create alone.

Several sessions run on one machine at once - a developer's, an agent's, a
gate's - and a coverage database in the system temp directory can belong to any
of them. So this runs a real pytest session with a database planted there, and
requires it to survive the run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent

#: The database a coverage run leaves in the temp directory, and the rollback
#: journal SQLite keeps beside it while a write is in flight.
PLANTED = (".coverage.lsdsk", ".coverage.lsdsk-journal")


@pytest.mark.os_agnostic
def test_a_pytest_run_leaves_another_sessions_coverage_database_alone(tmp_path: Path) -> None:
    """A session that did not create a coverage database must not delete it."""
    for name in PLANTED:
        (tmp_path / name).write_bytes(b"another session's coverage data")
    # The whole environment is kept, because a replaced one loses SystemRoot and
    # the child dies on Windows. Only COVERAGE_FILE goes, so the run takes the
    # default path a developer's own run takes, and every temp variable points
    # at this test's directory so nothing outside it is touched.
    environment = {key: value for key, value in os.environ.items() if key != "COVERAGE_FILE"}
    environment.update(TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))

    finished = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_enums.py"],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )

    assert finished.returncode == 0, f"the inner run failed: {finished.stdout[-1500:]}{finished.stderr[-1500:]}"
    survivors = sorted(path.name for path in tmp_path.iterdir() if path.name in PLANTED)
    assert survivors == sorted(PLANTED), f"the run deleted a coverage database it did not create; left: {survivors}"
