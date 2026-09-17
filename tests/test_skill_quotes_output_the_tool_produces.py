"""The shipped skill quotes this tool's output, so the quotes have to be real.

`skills/lsdsk/SKILL.md` teaches a reader to interpret what `lsdsk` prints, and
several of its rules are keyed to a literal column value - "running equals
capable at the PCIe floor" names the value to look for. A reader searching their
own output for a string this tool no longer produces concludes the rule does not
apply to their machine, which is the quietest way a piece of documentation can
be wrong.

So every link figure the skill quotes is checked against the formatters that
produce one. This is the guard that catches the NEXT drift as well as this one,
which a fixed list of corrected lines would not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lsdsk.adapters.render import theme
from lsdsk.domain.models import pcie_bandwidth_gbps, pcie_generation

SKILL = Path(__file__).resolve().parents[1] / "skills" / "lsdsk" / "SKILL.md"

#: A PCIe link figure in either of this tool's two spellings, with or without the
#: blank that used to sit inside it: the marketing form (``Gen4x4``) and the
#: decimal one (``3.0x4``). The optional space is the whole point - a figure
#: written with one is exactly what this test exists to catch.
_FIGURE = re.compile(r"\b(?:Gen(?P<gen>\d+)|(?P<dec>\d+)\.0)\s?x(?P<width>\d+)\b")

#: Every generation this tool knows how to price, so a quoted figure can be
#: turned back into the string the formatter would produce for it.
_GTPS: dict[int, float] = {1: 2.5, 2: 5.0, 3: 8.0, 4: 16.0, 5: 32.0, 6: 64.0}


def _quoted_figures() -> list[tuple[str, int]]:
    """Every link figure in the skill, with the line it sits on."""
    found: list[tuple[str, int]] = []
    for number, line in enumerate(SKILL.read_text(encoding="utf-8").splitlines(), start=1):
        found.extend((match.group(0), number) for match in _FIGURE.finditer(line))
    return found


def test_the_skill_quotes_some_link_figures_at_all() -> None:
    """The control. Without it the two tests below pass on an empty list."""
    assert SKILL.is_file(), f"{SKILL} is missing, so nothing below checked anything"
    assert _quoted_figures(), "no link figure found in the skill, so the pattern has stopped matching"


def test_no_link_figure_in_the_skill_is_written_with_a_blank_inside_it() -> None:
    """The tool writes a figure closed, so a reader will never find a spaced one.

    Checked on the SPELLING rather than on a list of lines, because the lines
    move and the spelling is the thing that is either right or wrong.
    """
    spaced = [(text, line) for text, line in _quoted_figures() if " x" in text]
    assert not spaced, f"the skill quotes figures this tool cannot print: {spaced}"


@pytest.mark.os_agnostic
def test_every_link_figure_the_skill_quotes_is_one_a_formatter_produces() -> None:
    """Stronger than the spelling check: the exact string has to be reachable.

    Each quoted figure is fed back through the formatter that would draw it, and
    must come out unchanged. A figure the formatter spells differently is one no
    reader will find, whatever the reason.
    """
    for text, line in _quoted_figures():
        match = _FIGURE.fullmatch(text)
        assert match is not None, f"line {line}: {text!r} matched once and not again"
        generation = int(match.group("gen") or match.group("dec"))
        width = int(match.group("width"))
        gtps = _GTPS.get(generation)
        assert gtps is not None, f"line {line}: {text!r} names generation {generation}, which this tool cannot price"
        assert pcie_generation(gtps) == generation, f"line {line}: {text!r} is not a generation this tool knows"

        drawn = (
            theme.format_pcie_generation(gtps, width) if match.group("gen") else theme.format_pcie_decimal(gtps, width)
        )
        assert drawn == text, f"line {line}: the skill quotes {text!r}; the tool prints {drawn!r}"


@pytest.mark.os_agnostic
def test_a_bandwidth_the_skill_quotes_beside_a_figure_is_that_figure_s_own() -> None:
    """A wrong number beside a right figure is worse than no number at all.

    Where the skill writes a figure AND a throughput, the throughput must be the
    one this tool computes for that figure, so the example a reader compares
    their own output against agrees with it digit for digit.
    """
    pattern = re.compile(_FIGURE.pattern + r" \((?P<gbps>[0-9.]+) GB/s\)")
    checked = 0
    for number, line in enumerate(SKILL.read_text(encoding="utf-8").splitlines(), start=1):
        for match in pattern.finditer(line):
            generation = int(match.group("gen") or match.group("dec"))
            width = int(match.group("width"))
            expected = theme.format_bandwidth(pcie_bandwidth_gbps(_GTPS[generation], width))
            assert f"{match.group('gbps')} GB/s" == expected, (
                f"line {number}: {match.group(0)!r} should read {expected!r}"
            )
            checked += 1
    if not checked:
        pytest.skip("the skill quotes no figure with its throughput")
