"""``--dir-mode`` and ``--file-mode`` read their text by the rule ``--set`` and the files use.

The options parsed with ``int(value, 8)``, which is a second decider beside lib_layered_config's
``parse_mode_text``: it takes a ``_`` separator, surrounding whitespace, a sign, ``0O`` and
any Unicode digit, so ``--dir-mode 7_50`` deployed with 0o750 while
``--set ...app_directory=7_50`` and the same text in a file were refused. Now the option is
read by the library's own parser and a text it refuses is a usage error, exit 2, before
anything is deployed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters import cli as cli_mod
from lsdsk.composition import build_production

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from lsdsk.composition import AppServices
    from lsdsk.domain.deployment import DeployRequest


def _capturing(requests: list[DeployRequest], deployed: Path) -> Callable[[], AppServices]:
    """The production services with a deploy that records each request instead of writing."""

    def _deploy(request: DeployRequest) -> list[Path]:
        requests.append(request)
        return [deployed]

    services = replace(build_production(), deploy_configuration=_deploy)
    return lambda: services


#: Texts ``int(text, 8)`` takes and ``parse_mode_text`` refuses, each for its own reason.
_REFUSED = (
    pytest.param("7_50", id="underscore"),
    pytest.param(" 750", id="leading-space"),
    pytest.param("750\n", id="trailing-newline"),
    pytest.param("+750", id="sign"),
    pytest.param("-1", id="negative"),
    pytest.param("0O750", id="upper-case-prefix"),
    pytest.param("0o7_50", id="prefixed-underscore"),
    pytest.param("\u0667\u0665\u0660", id="arabic-indic-digits"),
    pytest.param("77777", id="above-0o7777"),
)


@pytest.mark.parametrize("option", ["--dir-mode", "--file-mode"])
@pytest.mark.parametrize("text", _REFUSED)
def test_a_mode_text_the_library_refuses_is_a_usage_error(
    option: str, text: str, tmp_path: Path, cli_runner: CliRunner
) -> None:
    requests: list[DeployRequest] = []

    result = cli_runner.invoke(
        cli_mod.cli,
        ["config-deploy", "--target", "user", option, text],
        obj=_capturing(requests, tmp_path / "config.toml"),
    )

    assert result.exit_code == 2, result.output
    assert option in result.output, result.output
    assert "Invalid octal mode" in result.output, result.output
    assert requests == [], "a refused mode reached the deploy"


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        pytest.param("750", 0o750, id="bare"),
        pytest.param("0o750", 0o750, id="prefixed"),
        pytest.param("0750", 0o750, id="leading-zero"),
        pytest.param("0000750", 0o750, id="many-leading-zeros"),
    ],
)
@pytest.mark.parametrize("option", ["--dir-mode", "--file-mode"])
def test_a_mode_text_the_library_takes_reaches_the_deploy_as_that_mode(
    option: str, text: str, mode: int, tmp_path: Path, cli_runner: CliRunner
) -> None:
    """The control: every spelling the files and ``--set`` take, the option takes too."""
    requests: list[DeployRequest] = []

    result = cli_runner.invoke(
        cli_mod.cli,
        ["config-deploy", "--target", "user", option, text],
        obj=_capturing(requests, tmp_path / "config.toml"),
    )

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    given = requests[0].dir_mode if option == "--dir-mode" else requests[0].file_mode
    assert given == mode
