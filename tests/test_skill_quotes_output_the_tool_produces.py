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

import ast
import re
from pathlib import Path

import pytest

from lsdsk.adapters.render import theme
from lsdsk.domain.enums import CliCommand
from lsdsk.domain.models import pcie_bandwidth_gbps, pcie_generation

SKILL = Path(__file__).resolve().parents[1] / "skills" / "lsdsk" / "SKILL.md"
REPORT = Path(__file__).resolve().parents[1] / "src" / "lsdsk" / "adapters" / "render" / "report.py"

#: A PCIe link figure as the tool writes it (``Gen4x4``) AND in the two forms it
#: does not: the decimal spelling (``3.0x4``) and either of them opened up with a
#: blank. Matching what the tool cannot print is the whole point - a figure the
#: skill quotes in a form no reader will find is what this test exists to catch,
#: and a pattern that matched only the right form would call it absent instead.
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

        # One formatter, whichever spelling the skill used: a quoted figure in
        # the decimal form is exactly the drift this asserts against, so it must
        # be measured against what the tool prints rather than against the
        # formatter that would have produced it.
        drawn = theme.format_pcie_generation(gtps, width)
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


#: The sentence that enumerates a disk's fields, and the end of it. Anchored on
#: the sentence rather than on a heading because the surrounding prose is free to
#: move; what must not drift is this list against the payload itself.
_DISK_FIELDS_OPENS = "A disk, inside `data.disks`, carries "
_SENTENCE_ENDS = ".**"


def _enumerated_disk_fields() -> list[str]:
    """The field names the skill's own sentence promises a disk carries."""
    text = SKILL.read_text(encoding="utf-8")
    start = text.index(_DISK_FIELDS_OPENS)
    sentence = text[start : text.index(_SENTENCE_ENDS, start)]
    return re.findall(r"`([a-z_]+)`", sentence)


def test_the_skill_enumerates_the_fields_a_disk_really_carries() -> None:
    """A field added to the payload and not to this sentence is missing from the docs.

    The failure this guards is not a wrong word: a caller reading an enumerated
    list takes it as the whole payload, so a field the tool emits and the list
    omits is one nobody knows to read. The same shape cost the TUI's disk page its
    serial and firmware columns for a whole minor series, and that list had no
    guard either.

    Compared against a real envelope rather than against ``Disk.model_fields``,
    because the promise is about what a caller RECEIVES, and a field could be
    excluded on the way out without the model saying so.
    """
    from lsdsk.adapters.cli.commands.scan import build_envelope
    from lsdsk.adapters.hw import snapshot as snapshot_adapter

    capture = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"
    inventory = snapshot_adapter.load(capture)
    envelope = build_envelope(inventory, (), CliCommand.DISKS)
    emitted = set(envelope.model_dump(mode="json")["data"]["disks"][0])

    promised = _enumerated_disk_fields()
    assert promised, "the enumerating sentence was not found, so this test checked nothing"
    assert set(promised) == emitted, (
        f"the skill and the payload disagree: only in the skill {sorted(set(promised) - emitted)}, "
        f"only in the payload {sorted(emitted - set(promised))}"
    )


def _verdict_of(returned: ast.expr, calls: list[str]) -> str | None:
    """The verdict text one ``return`` hands back, or None when it hands on.

    A verdict is returned as ``(text, style)``, so the text is the first element
    of the tuple. A computed one is normalised to the form a table can carry: the
    documented row for ``f"spare {spare:.2f} GB/s"`` is ``spare N GB/s``.
    """
    if isinstance(returned, ast.Tuple) and returned.elts:
        return _verdict_of(returned.elts[0], calls)
    if isinstance(returned, ast.Constant) and isinstance(returned.value, str):
        return returned.value
    if isinstance(returned, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "N"
            for part in returned.values
        )
    if isinstance(returned, ast.Call) and isinstance(returned.func, ast.Name):
        calls.append(returned.func.id)
    return None


def _verdicts_the_code_can_produce() -> set[str]:
    """Every verdict ``slot_verdict`` can hand back, read off its own source.

    Read from the source rather than by driving the function over a list of
    slots, because a list I write can only reach the branches I thought of and
    the branch this exists to catch is the one somebody adds later. Returns that
    hand on to another function in the module are followed, so moving a branch
    into a helper does not quietly empty the set.
    """
    module = ast.parse(REPORT.read_text(encoding="utf-8"))
    bodies = {node.name: node for node in ast.walk(module) if isinstance(node, ast.FunctionDef)}
    verdicts: set[str] = set()
    pending = ["slot_verdict"]
    walked: set[str] = set()
    while pending:
        name = pending.pop()
        if name in walked or name not in bodies:
            continue
        walked.add(name)
        for node in ast.walk(bodies[name]):
            if isinstance(node, ast.Return) and node.value is not None:
                verdict = _verdict_of(node.value, pending)
                if verdict is not None:
                    verdicts.add(verdict)
    return verdicts


def _documented_verdicts() -> set[str]:
    """The verdicts the skill's own table lists, keyed on its heading."""
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("| Verdict ")
    table = text[start : text.index("\n\n", start)]
    return {match.group(1) for line in table.splitlines()[2:] if (match := re.match(r"\| `([^`]+)`", line))}


@pytest.mark.os_agnostic
def test_the_skill_lists_every_verdict_the_slots_table_can_print() -> None:
    """The verdict vocabulary is CLOSED, so an unlisted one reads as an anomaly.

    An agent handed a table of the values a column takes treats anything else as
    something to escalate, and two of these differ by one parenthetical: ``in
    use`` and ``in use (graphics)``, so the likely failure is reporting a
    graphics card where there is a balloon device. It is not an edge case on
    Windows, which is the platform that publishes no link registers for a
    bridge: the two occupied ports of the windows-ahci capture both read ``in
    use`` and not one of the 44 ports in the four Linux captures does, so
    whoever reads the table is exactly whoever runs it on the platform it omits.
    """
    produced = _verdicts_the_code_can_produce()
    documented = _documented_verdicts()

    assert len(produced) >= 7, f"the source walk found {sorted(produced)}, so it is not reading the branches"
    assert len(documented) >= 7, f"the table read as {sorted(documented)}, so the heading moved"
    assert produced == documented, (
        f"printed but not documented: {sorted(produced - documented)}; "
        f"documented but not printed: {sorted(documented - produced)}"
    )
