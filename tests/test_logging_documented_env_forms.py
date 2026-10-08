"""Every environment example the shipped logging comments quote must actually work.

``90-logging.toml`` documents, above each setting, the environment variable that
sets it (``LSDSK___LIB_LOG_RICH__X=...``) and the ``.env`` line that does
(``LIB_LOG_RICH__X=...``). Four of those examples were inert: the Graylog
endpoint ``graylog.example.com:12201``, the rate limit ``100:60``, the console
styles ``DEBUG=dim,ERROR=bold red`` and the scrub patterns ``password=.+,...`` are
text forms of a list or a table, and the logging library refused the text
("Input should be a valid tuple. Using []"). The comments are the only
documentation of those channels, so they are the test data.
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import TYPE_CHECKING, cast

import pytest
from lib_log_rich.runtime import RuntimeConfig

from lsdsk.adapters.cli.main import main
from lsdsk.adapters.config.loader import get_config
from lsdsk.adapters.logging.string_forms import with_documented_forms
from lsdsk.composition import build_production

SHIPPED = files("lsdsk.adapters.config").joinpath("defaultconfig.d", "90-logging.toml").read_text(encoding="utf-8")
ENVIRONMENT_EXAMPLES = re.findall(r"^# Environment Variable: (LSDSK___\S+?)=(.*)$", SHIPPED, re.MULTILINE)
DOTENV_EXAMPLES = re.findall(r"^# \.env: (LIB_LOG_RICH__\S+?)=(.*)$", SHIPPED, re.MULTILINE)
ARGV = ["--replay", "tests/fixtures/hw/linux-minimal.json", "--no-record", "findings"]

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> None:
    """The loader caches per process; drop it so each example reads its own environment."""
    get_config.cache_clear()


def _warnings_about_logging(capfd: pytest.CaptureFixture[str]) -> list[str]:
    """The lines on standard error saying a logging setting was not used."""
    return [line for line in capfd.readouterr().err.splitlines() if "ignoring lib_log_rich" in line]


@pytest.mark.os_agnostic
def test_the_comments_hold_enough_examples_to_mean_something() -> None:
    """The gate's own count: a regex that matches nothing would pass every test below."""
    assert len(ENVIRONMENT_EXAMPLES) >= 30, len(ENVIRONMENT_EXAMPLES)
    assert len(DOTENV_EXAMPLES) == len(ENVIRONMENT_EXAMPLES)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "value"), ENVIRONMENT_EXAMPLES, ids=[name for name, _ in ENVIRONMENT_EXAMPLES])
def test_a_documented_environment_variable_is_used_not_ignored(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """The documented value for the variable is accepted by the real loader and the real logging start."""
    monkeypatch.setenv(name, value)
    main(ARGV, services_factory=build_production)
    assert _warnings_about_logging(capfd) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "value"), DOTENV_EXAMPLES, ids=[name for name, _ in DOTENV_EXAMPLES])
def test_a_documented_dotenv_line_is_used_not_ignored(
    capfd: pytest.CaptureFixture[str], tmp_path: Path, name: str, value: str
) -> None:
    """The ``.env`` form is read with no prefix, and its documented value is accepted the same way."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{name}={value}\n", encoding="utf-8")
    main(["--env-file", str(env_file), *ARGV], services_factory=build_production)
    assert _warnings_about_logging(capfd) == []


@pytest.mark.os_agnostic
def test_the_documented_text_forms_reach_the_library_as_the_shapes_it_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loader's text becomes the real endpoint, limit, styles and patterns the runtime is started with."""
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__GRAYLOG_ENDPOINT", "graylog.example.com:12201")
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__RATE_LIMIT", "100:60")
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__CONSOLE_STYLES", "DEBUG=dim,ERROR=bold red")
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__SCRUB_PATTERNS", "api_key=.+")
    section = get_config().get("lib_log_rich")

    forms = with_documented_forms(section)
    config = RuntimeConfig(
        service="s",
        environment="e",
        graylog_endpoint=cast("tuple[str, int]", forms["graylog_endpoint"]),
        rate_limit=cast("tuple[int, float]", forms["rate_limit"]),
        console_styles=cast("dict[str, str]", forms["console_styles"]),
        scrub_patterns=cast("dict[str, str]", forms["scrub_patterns"]),
    )

    assert config.graylog_endpoint == ("graylog.example.com", 12201)
    assert config.rate_limit == (100, 60.0)
    assert config.console_styles == {"DEBUG": "dim", "ERROR": "bold red"}
    assert config.scrub_patterns == {"api_key": ".+"}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("value", "endpoint"),
    [("[::1]:12201", ("::1", 12201)), ("localhost:514", ("localhost", 514))],
)
def test_an_endpoint_may_be_a_bracketed_ipv6_address(value: str, endpoint: tuple[str, int]) -> None:
    """A URL-style bracketed address carries the colons of its own."""
    forms = with_documented_forms({"graylog_endpoint": value})
    config = RuntimeConfig(
        service="s", environment="e", graylog_endpoint=cast("tuple[str, int]", forms["graylog_endpoint"])
    )
    assert config.graylog_endpoint == endpoint


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", ["graylog.example.com", "host:port", "host:0", "host:70000", "::1:12201", ":12201"])
def test_a_malformed_endpoint_is_still_refused_with_the_usual_warning(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Text that is not ``host:port`` is left for the library to refuse, and the run goes on."""
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__GRAYLOG_ENDPOINT", value)
    main(ARGV, services_factory=build_production)
    warnings = _warnings_about_logging(capfd)
    assert len(warnings) == 1, warnings
    assert "lib_log_rich.graylog_endpoint=" in warnings[0], warnings
