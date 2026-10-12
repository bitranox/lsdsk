r"""A scrub pattern is called cut only when a bracket that MEANS something was left open.

The text form is ``field=regex`` pairs split on every comma. A regex that holds a
comma is cut, and the tail is told from a deliberate list by the previous piece
having opened a group it never closed. Counting every bracket character
overreached: a bracket inside a character class or behind a backslash is a
literal, so ``a=[(],b=c`` and ``a=\(,b=c`` warned that the valid ``b=c`` was
dropped, and a piece with no ``=`` at all was blamed on a cut that never happened.
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
    return [" ".join(line.split()) for line in capfd.readouterr().err.splitlines() if "LOG_SCRUB" in line]


@pytest.mark.parametrize(
    "text",
    ["a=[(],b=c", r"a=\(,b=c", r"a=[\]],b=c", "a=[]x],b=c", "a=[^]],b=c", r"a=\[,b=c", r"a=\{1,b=c", "a=[{(],b=c"],
)
def test_a_bracket_that_is_a_literal_does_not_make_the_next_entry_a_cut_tail(
    text: str, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The valid ``b=c`` follows a piece whose bracket is escaped or inside a class."""
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", text)

    assert _said_about_scrubbing(capfd) == []


def test_a_piece_with_no_equals_sign_says_so_rather_than_blaming_a_cut(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``a=x,junk`` was cut by nothing: ``junk`` simply is not a ``field=regex`` pair."""
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", "a=x,junk")

    said = _said_about_scrubbing(capfd)

    assert len(said) == 1, said
    assert "has no '='" in said[0], said
    assert "continues a cut regex" not in said[0], said


@pytest.mark.parametrize("text", [r"token=\d{1,3},api_key=.+", "token=(?:a|b){1,2},key=.+", "token=[{]{1,3},key=.+"])
def test_a_tail_after_a_group_left_open_still_says_it_continues_a_cut_regex(
    text: str, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: a real cut keeps its own reason, so the two are not collapsed into one."""
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", text)

    said = _said_about_scrubbing(capfd)

    assert len(said) == 1, said
    assert "continues a cut regex" in said[0], said
    assert "has no '='" not in said[0], said


@pytest.mark.parametrize(
    "text",
    [
        "token=(a,b),key=.+",
        "token=(?:a,b),key=.+",
        "token=[a,b],key=.+",
        "token=[^a,b],key=.+",
        "token=x(a,b=c),key=.+",
    ],
    ids=["group", "non-capturing-group", "class", "negated-class", "group-tail-with-equals"],
)
def test_each_kind_of_bracket_left_open_makes_the_next_piece_a_cut_tail(
    text: str, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parenthesis or a square bracket left open is a cut regex just as a brace is, not only ``{``."""
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", text)

    # The library also refuses the half-open regex itself and says so on a line of
    # its own; only the warning about the dropped piece is this test's subject.
    said = [line for line in _said_about_scrubbing(capfd) if "split on every comma" in line]

    assert len(said) == 1, said
    assert "continues a cut regex" in said[0], said
    assert "has no '='" not in said[0], said
