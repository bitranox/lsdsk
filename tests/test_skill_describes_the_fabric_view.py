"""The shipped skill describes the topology view the tool actually draws.

`skills/lsdsk/SKILL.md` teaches a reader to interpret `lsdsk topology`, and that
section stopped being a disk-to-controller tree: it is the PCI fabric now, it
draws the LEAST of it by default, and it puts SYMBOLS in the hop columns that
mean nothing without their legend. A skill that omits those three sends a reader
to the wrong conclusion rather than to no conclusion - a dash read as a dead link
is a fault report about working hardware, and a default that hides the graphics
card reads as a tool that cannot see it.

So every string checked here is taken from the PRODUCER that writes it, never
typed into this file: `tree.density_note`, `theme.hop_legend` and
`tree.device_header_line`. Reword any of them and this test fails until the skill
follows, which is the drift the sibling
`test_skill_quotes_output_the_tool_produces` catches for link figures.
"""

from __future__ import annotations

from pathlib import Path

from lsdsk.adapters.render import theme, tree
from lsdsk.domain.enums import TreeDensity

SKILL = Path(__file__).resolve().parents[1] / "skills" / "lsdsk" / "SKILL.md"

#: The note the SHIPPED default writes. The default is the least of the fabric,
#: so this line is the only thing standing between a reader and "lsdsk cannot
#: see my GPU" - which is why the skill has to carry the line itself rather than
#: a paraphrase of it.
DEFAULT_NOTE = tree.density_note(TreeDensity.STORAGE_ONLY)

#: Each hop symbol's legend, alone, because a capture draws whichever applies to
#: it and a reader meets one at a time. Asked of the formatter so a reworded
#: meaning cannot leave the skill quoting the old one.
LEGENDS = (theme.hop_legend([theme.NOT_READ]), theme.hop_legend([theme.LEGACY]))

#: The sentence that OPENS the global-option list. The list is checked inside
#: the paragraph this anchors, because that is the text a reader consults.
ANCHOR = "The global options are"


def _skill_text() -> str:
    """The shipped skill, as the reader gets it."""
    return SKILL.read_text(encoding="utf-8")


def test_the_producers_still_write_something_to_look_for() -> None:
    """The control. Without it every test below passes on an empty string.

    A formatter that returned "" would make each `in` check trivially true, so
    the suite would report the skill complete at the moment it stopped being
    checked at all.
    """
    assert SKILL.is_file(), f"{SKILL} is missing, so nothing below checked anything"
    assert DEFAULT_NOTE.strip(), "density_note returned nothing, so the note check asserts nothing"
    for legend in LEGENDS:
        assert legend.strip(), "hop_legend returned nothing, so the legend check asserts nothing"
    assert len(set(LEGENDS)) == len(LEGENDS), "the two hop symbols now share a legend, so one of them is unexplained"


def test_the_skill_carries_the_density_note_the_default_view_prints() -> None:
    """The default draws the LEAST of the fabric, and the skill must say so.

    Checked against the note itself rather than against the option name alone:
    a reader who finds `--tree-density` but not what the default OMITS still
    reads a missing device as a missing device.
    """
    assert DEFAULT_NOTE in _skill_text(), (
        f"the skill does not carry the line the default topology view prints: {DEFAULT_NOTE!r}. "
        "A reader whose GPU is absent from the tree concludes the tool cannot see it."
    )


def test_the_skill_explains_every_hop_symbol_the_tool_can_draw() -> None:
    """A symbol without its meaning is worse than a blank column.

    Both are checked, because the two are produced on different platforms - a
    Linux legacy device and a Windows bridge - so a skill that carries one still
    leaves half its readers with an unexplained column.
    """
    text = _skill_text()
    missing = [legend for legend in LEGENDS if legend not in text]
    assert not missing, (
        f"the skill draws no legend for hop symbols the tool prints: {missing}. "
        "A dash read as a dead link is a fault report about working hardware."
    )


def test_the_skill_shows_the_column_header_the_device_rows_are_drawn_under() -> None:
    """The tree grew a header, and the skill has to show the same one.

    Required IN ORDER on ONE line rather than as four words present somewhere,
    because `capable`, `running` and `name` are ordinary words this skill uses
    throughout: a membership test would pass on prose that never shows a reader
    what sits above their own rows. Taken from `DEVICE_COLUMNS`, so renaming or
    reordering a column fails this until the skill follows.
    """
    titles = [column.title for column in tree.DEVICE_COLUMNS]
    assert len(titles) > 1, "DEVICE_COLUMNS has fewer than two titles, so order asserts nothing"
    for line in _skill_text().splitlines():
        positions = [line.find(title) for title in titles]
        if all(at >= 0 for at in positions) and positions == sorted(positions):
            return
    message = " ".join(titles)
    raise AssertionError(f"no line of the skill shows the tree's device header, whose columns run: {message}")


def test_the_skill_names_every_global_option_the_cli_declares() -> None:
    """The option list is an ENUMERATION, so it goes stale silently.

    A reader who cannot find `--tree-density` in the list concludes it does not
    exist and lives with the default. Asked of the group's own parameters, so a
    new global option fails this until the skill names it.

    Scoped to the PARAGRAPH that makes the claim, not to the document: an option
    named anywhere else satisfies a whole-file search while the list a reader
    consults is still short one. That is how this test first passed against the
    very omission it was written for.
    """
    from lsdsk.adapters.cli import (
        cli,
    )

    declared = sorted({opt for param in cli.params for opt in getattr(param, "opts", ()) if opt.startswith("--")})
    assert declared, "the CLI group declares no options, so this test asserts nothing"
    paragraphs = [block for block in _skill_text().split("\n\n") if ANCHOR in block]
    assert len(paragraphs) == 1, f"expected exactly one paragraph containing {ANCHOR!r}, found {len(paragraphs)}"
    # --help is universal and documents itself; every other global is a fact
    # about THIS tool that a reader can only get from the list.
    missing = [opt for opt in declared if opt != "--help" and opt not in paragraphs[0]]
    assert not missing, f"the skill's global-option list omits options the CLI accepts: {missing}"
