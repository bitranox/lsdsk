"""A ``ValueError`` from deployment is logged for what it says, not as a bad profile name."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from pathlib import Path

    from lsdsk.composition import AppServices
    from lsdsk.domain.deployment import DeployRequest


def _refusing_modes(request: DeployRequest) -> list[Path]:
    """A deployment the library refuses for its MODES, which has nothing to do with a profile."""
    raise ValueError("dir_mode 0o7777 is not a permission mode")


def _services() -> AppServices:
    return replace(build_production(), deploy_configuration=_refusing_modes)


@pytest.mark.os_agnostic
def test_a_value_error_that_is_not_about_the_profile_is_not_logged_as_one(
    managed_traceback_state: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every ``ValueError`` was logged "Rejected profile name", whatever it was about."""
    with caplog.at_level(logging.ERROR):
        exit_code = cli_mod.main(["config-deploy", "--target", "user"], services_factory=_services)

    assert exit_code == ExitCode.INVALID_ARGUMENT, capsys.readouterr().err
    messages = [record.getMessage() for record in caplog.records if record.name.endswith("commands.config")]
    assert messages, "the refusal was not logged at all"
    assert "Rejected profile name" not in messages, messages
    assert "Deployment refused" in messages, messages
    record = next(r for r in caplog.records if r.getMessage() == "Deployment refused")
    assert "dir_mode 0o7777" in str(getattr(record, "error", "")), record.__dict__
    assert getattr(record, "error_type", None) == "ValueError"
