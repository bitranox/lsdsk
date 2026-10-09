"""A configuration read that fails for a reason other than permission is a CONFIG error (78)."""

from __future__ import annotations

import errno
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.adapters.cli.exit_codes import ExitCode
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from lib_layered_config import Config

    from lsdsk.composition import AppServices


def _services_whose_read_fails() -> AppServices:
    """The production services with a configuration reader that hits an I/O error."""

    def _get_config(*, profile: str | None = None, dotenv_path: str | None = None) -> Config:
        raise OSError(errno.EIO, "Input/output error", "/etc/xdg/lsdsk/config.toml")

    return replace(build_production(), get_config=_get_config)


@pytest.mark.os_agnostic
def test_an_io_error_reading_the_configuration_is_refused_as_78_with_an_envelope(
    managed_traceback_state: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``OSError`` arm of ``_load_or_refuse`` was reached by no test: a mutant of it survived."""
    exit_code = cli_mod.main(["disks", "--format", "json"], services_factory=_services_whose_read_fails)

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CONFIG_ERROR, captured.err
    envelope = json.loads(captured.out)
    assert envelope["ok"] is False
    assert envelope["command"] == "disks"
    assert envelope["error"]["type"] == "CONFIG_ERROR"
    assert "Input/output error" in envelope["error"]["message"]
    assert captured.err.startswith("Error:"), captured.err


@pytest.mark.os_agnostic
def test_a_permission_error_is_still_told_apart_from_an_io_error(
    managed_traceback_state: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: the arm above must not swallow the permission arm that precedes it."""

    def _get_config(*, profile: str | None = None, dotenv_path: str | None = None) -> Config:
        raise PermissionError(errno.EACCES, "Permission denied", "/etc/xdg/lsdsk/config.toml")

    exit_code = cli_mod.main(
        ["disks", "--format", "json"], services_factory=lambda: replace(build_production(), get_config=_get_config)
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.PERMISSION_DENIED, captured.err
    assert json.loads(captured.out)["error"]["type"] == "PERMISSION_DENIED"
