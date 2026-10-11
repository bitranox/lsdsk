"""Two refusals whose exact answer no test pinned, found by mutation.

* A refused counter store is named in ``skipped`` WITH its path. Dropping the path
  left every test green, though a reader with two stores needs to know which one.
* ``config-deploy`` refuses an invalid ``--profile`` before it logs that it is
  deploying. Removing that early refusal left every test green, because the later
  refusal gives the same code and the same sentence - but it logs a start and a
  second error first, which is what the early one exists to avoid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.composition import build_production

CAPTURE = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


@pytest.mark.os_agnostic
def test_a_refused_counter_store_is_named_in_skipped_with_its_path(
    tmp_path: Path, clear_config_cache: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The entry says which store could not be read, not only that one could not."""
    store = tmp_path / "history.json"
    store.write_text("this is not a history store", encoding="utf-8")

    cli_mod.main(
        ["--history-file", str(store), "--no-record", "--replay", str(CAPTURE), "disks", "--format", "json"],
        services_factory=build_production,
    )

    envelope = json.loads(capsys.readouterr().out)
    refusals = [entry for entry in envelope["skipped"] if entry.startswith("counter history")]
    assert len(refusals) == 1, envelope["skipped"]
    assert refusals[0].startswith(f"counter history at {store} could not be read"), refusals


@pytest.mark.os_agnostic
def test_a_deploy_with_an_invalid_profile_is_refused_before_it_logs_a_start(
    managed_traceback_state: None, clear_config_cache: None, capfd: pytest.CaptureFixture[str]
) -> None:
    """Standard error holds the refusal alone: no "Deploying configuration", no second error from the library."""
    exit_code = cli_mod.main(
        ["config-deploy", "--target", "user", "--profile", "../x"], services_factory=build_production
    )

    err = capfd.readouterr().err
    assert exit_code == ExitCode.INVALID_ARGUMENT, err
    assert "profile" in err
    assert "Deploying configuration" not in err, err
    assert "Deployment refused" not in err, err
