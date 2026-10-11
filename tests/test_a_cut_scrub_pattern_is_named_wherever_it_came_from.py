"""A scrub pattern cut at its comma is named, from the environment and when the tail has an ``=``.

The text form is ``field=regex`` pairs split on every comma. The section's value was
already checked for a tail with no ``=``; the same text in ``LOG_SCRUB_PATTERNS``
was never looked at, and a tail that does contain ``=`` was read as a pair of its
own (``token=a{1,3=x}`` made an entry called ``3``) with nothing said.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.cli.main import main
from lsdsk.adapters.config.loader import get_config
from lsdsk.composition import build_production

ARGV = ["--replay", "tests/fixtures/hw/linux-minimal.json", "--no-record", "findings"]


@pytest.fixture(autouse=True)
def no_configuration_outlives_its_test() -> None:
    """The loader caches per process; drop it so each example reads its own environment."""
    get_config.cache_clear()


def _said_about_scrubbing(capfd: pytest.CaptureFixture[str]) -> list[str]:
    main(ARGV, services_factory=build_production)
    return [line for line in capfd.readouterr().err.splitlines() if "scrub_patterns" in line or "SCRUB" in line]


def test_a_cut_pattern_in_the_environment_variable_is_named(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""``LOG_SCRUB_PATTERNS=token=\d{1,3},api_key=.+`` was cut at ``{1,3}`` and said nothing."""
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", r"token=\d{1,3},api_key=.+")

    said = _said_about_scrubbing(capfd)

    assert len(said) == 1, said
    assert "environment variable LOG_SCRUB_PATTERNS" in said[0], said
    assert "ignoring" in said[0]
    assert "3}" in said[0]


def test_a_cut_tail_that_contains_an_equals_sign_is_named(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``token=a{1,3=x}`` cut at the comma leaves ``3=x}``, which parses as a pair and is not dropped."""
    monkeypatch.setenv("LSDSK___LIB_LOG_RICH__SCRUB_PATTERNS", "token=a{1,3=x}, key=.+")

    said = _said_about_scrubbing(capfd)

    assert len(said) == 1, said
    assert "lib_log_rich.scrub_patterns" in said[0], said
    assert "3=x}" in said[0], said
    assert "key" not in said[0].split("3=x}")[0].split("=")[-1], "the entry that parsed was named as dropped"


@pytest.mark.parametrize("text", ["token=a{2}, key=.+,", "token=[0-9]{2},key=(a|b),", "key=.+"])
def test_a_list_that_was_not_cut_says_nothing(
    text: str, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control, including a trailing comma: an empty piece is not a cut tail."""
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", text)

    assert _said_about_scrubbing(capfd) == []
