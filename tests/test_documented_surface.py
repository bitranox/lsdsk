"""Every command and option the CLI offers is named where a caller reads.

Exit codes have been guarded this way since 141 shipped undocumented: the
requirement is keyed on the enum, so a new code arrives carrying it rather than
waiting to be remembered. Commands and options had no such guard, and four had
drifted out of every document at once - `fail` and `logdemo` were offered by
`lsdsk --help` and named in nothing else, `-h` was an undocumented alias of
`--help`, and `snapshot --output` existed only as `-o`. COMMANDS.md meanwhile
opened by calling itself "every command, every global option".

Keying this on the click tree is the whole point. A list of names written out
here would be a second copy of the surface, and the copy that goes stale is
always the one nobody runs.

What this asks is deliberately weak: that the name appears SOMEWHERE a caller
reads, not that it is explained well. A presence check cannot judge prose, and
the failure it exists to catch is the one that happened - a surface documented
nowhere at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import click
import pytest

from lsdsk.adapters.cli import cli

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

REPO = Path(__file__).parent.parent

#: The documents a caller reads to find out what this tool takes.
#:
#: English only. The German twins are held against these by the translation
#: manifest, which requires the German half to be re-read whenever the English
#: half moves, so asking here as well would be a second gate on one fact.
DOCUMENTS = (
    "COMMANDS.md",
    "CONFIG.md",
    "README.md",
    "PAGES.md",
    "REPORT.md",
    "FINDINGS.md",
    "INSTALL.md",
    "skills/lsdsk/SKILL.md",
)


def _surface() -> tuple[set[str], set[str]]:
    """Every subcommand name and every option spelling the CLI really offers.

    Read from the live click tree, including the options click adds itself, so
    nothing here has to agree with a written-out list.

    Returns:
        The subcommand names, and every option spelling on any command.
    """
    commands: set[str] = set()
    options: set[str] = set()

    def walk(command: click.Command, name: str) -> None:
        # The command's OWN context settings, because `-h` lives in those rather
        # than on a parameter: built from a bare Context, click falls back to
        # `--help` alone and the alias never enters this set, so the guard would
        # have passed on exactly the spelling it was written to protect.
        ctx = click.Context(command, info_name=name, **command.context_settings)
        for param in command.get_params(ctx):
            if isinstance(param, click.Option):
                options.update(param.opts)
                options.update(param.secondary_opts)
        if isinstance(command, click.Group):
            for sub_name, sub in command.commands.items():
                commands.add(sub_name)
                walk(sub, sub_name)

    walk(cli, "lsdsk")
    return commands, options


def _prose() -> str:
    """Every caller-facing document, concatenated."""
    return "\n".join((REPO / name).read_text(encoding="utf-8") for name in DOCUMENTS)


def _names_a_command(text: str, command: str) -> bool:
    """Whether `text` invokes `command`, allowing the global options in between."""
    pattern = rf"(?<![\w-])lsdsk(?:\s+--[a-z-]+(?:[= ]\S+)?)*\s+{re.escape(command)}(?![\w-])"
    return re.search(pattern, text) is not None


def _names_an_option(text: str, option: str) -> bool:
    """Whether `text` names `option`, as a whole word rather than as a prefix."""
    return re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", text) is not None


def test_every_command_the_cli_offers_is_named_where_a_caller_reads() -> None:
    """A command only `--help` knows about is one nobody is told they have."""
    commands, _ = _surface()
    assert commands, "the control: the click tree yielded no commands, so this asserted nothing"

    text = _prose()
    undocumented = sorted(name for name in commands if not _names_a_command(text, name))
    assert not undocumented, f"offered by lsdsk --help and documented nowhere: {undocumented}"

    assert not _names_a_command(text, "nosuchcommand"), "the control: this check cannot report a command as absent"


def test_every_option_the_cli_offers_is_named_where_a_caller_reads() -> None:
    """Both spellings, because a reader meets whichever one they were shown."""
    _, options = _surface()
    assert options, "the control: the click tree yielded no options, so this asserted nothing"

    text = _prose()
    undocumented = sorted(option for option in options if not _names_an_option(text, option))
    assert not undocumented, f"accepted by the CLI and documented nowhere: {undocumented}"

    assert not _names_an_option(text, "--nosuchoption"), "the control: this check cannot report an option as absent"


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_document_this_guard_reads_exists(document: str) -> None:
    """A renamed document would silently shrink what the two guards above read."""
    assert (REPO / document).is_file(), f"{document} is gone, so the guards above read less than they claim"


#: The documents that quote a refusal sentence back to a reader, English and German.
#:
#: The German page quotes the ENGLISH sentence, because the tool prints one
#: language; so the twin is read here rather than left to the translation
#: manifest, which asks whether the German prose was re-read and not whether the
#: output block inside it is still what the tool says.
QUOTING_DOCUMENTS = ("CONFIG.md", "de/CONFIG.md", "COMMANDS.md", "de/COMMANDS.md", "skills/lsdsk/SKILL.md")

#: `Warning: ignoring <key>=<value>: <reason>. Using <default>.`
_REFUSAL = re.compile(
    r"^Warning: ignoring (?P<dotted>\S+?)=(?P<raw>.*?): (?P<reason>.+?)\. Using (?P<used>.+?)\.$", re.M
)


def _reasons_the_code_can_give() -> set[str]:
    """Every phrase a refused configuration value can be explained with."""
    from lsdsk.adapters.config.values import (
        REASON_FLAG,
        REASON_PATH,
        REASON_POSITIVE_FLOAT,
        REASON_POSITIVE_INT,
        reason_tree_density,
    )

    return {REASON_FLAG, REASON_PATH, REASON_POSITIVE_FLOAT, REASON_POSITIVE_INT, reason_tree_density()}


def test_every_refusal_a_document_quotes_is_one_the_code_can_give() -> None:
    """A quoted sentence is what a reader greps their own output for.

    CONFIG.md and its German twin both published `not a whole number above zero`
    for a whole release after the coercer started saying `not a whole number
    above zero, or too large to use`, so the search that sentence exists to serve
    returned nothing. Nothing caught it: the translation manifest asks whether
    the German prose was re-read, not whether the English output block inside it
    is still true, and a reason is not a command or an option so no surface guard
    covered it either.
    """
    real = _reasons_the_code_can_give()
    quoted: list[tuple[str, str]] = [
        (name, match.group("reason"))
        for name in QUOTING_DOCUMENTS
        for match in _REFUSAL.finditer((REPO / name).read_text(encoding="utf-8"))
    ]

    assert quoted, "the control: no refusal sentence was found, so this asserted nothing"

    stale = sorted({(name, reason) for name, reason in quoted if reason not in real})
    assert not stale, f"quoted but not what the code says: {stale}"

    assert "not a whole number above zero" not in real, (
        "the control: the superseded wording must not be in the real set, or this cannot fail"
    )


#: A quoted output block, and the argv that produces it.
#:
#: Keyed on the first line of the block so the test finds it wherever it moves in
#: the document, and rendered at an explicit width because the layout surrenders
#: the bandwidth figures on a narrow terminal - a sample showing both full device
#: names and bandwidths is one no default-width run produces.
SAMPLE_BLOCKS = (
    (
        "FINDINGS.md",
        "Micro-Star",
        ("--set", "display.piped_width=200", "slots", "--replay", "tests/fixtures/hw/linux-nvme-board.json"),
    ),
    (
        "de/FINDINGS.md",
        "Micro-Star",
        ("--set", "display.piped_width=200", "slots", "--replay", "tests/fixtures/hw/linux-nvme-board.json"),
    ),
)


@pytest.mark.parametrize(("document", "first_line", "argv"), SAMPLE_BLOCKS)
def test_a_quoted_sample_is_output_the_tool_really_produces(
    document: str,
    first_line: str,
    argv: tuple[str, ...],
    cli_runner: CliRunner,
    production_factory: Callable[[], object],
) -> None:
    """An invented sample reads exactly like a real one and matches nothing.

    This block named its occupants `Samsung 980 PRO 2TB` and `AMD Hawaii XT
    [Radeon R9 290X]`, and its board `MSI MEG Z690 ACE`. The occupant column is
    the PCI database's own vendor and device string and has never been any of
    those, so a reader comparing the sample against their own output found no row
    of it - while every figure beside the names was correct, which is what made
    it read as verified.

    Only the blocks listed above are held. Other quoted output in these documents
    is not, which is a narrower guard than the shape deserves.
    """
    result = cli_runner.invoke(cli, list(argv), obj=production_factory)
    assert result.exit_code in {0, 1}, f"the sample's own command failed: {result.exception or result.output}"

    produced = {line.rstrip() for line in result.output.splitlines() if line.strip()}
    assert produced, "the control: the command produced nothing, so this asserted nothing"

    text = (REPO / document).read_text(encoding="utf-8")
    block = re.search(rf"```\n({re.escape(first_line)}.*?)\n```", text, re.S)
    assert block is not None, f"{document}: no quoted block starting {first_line!r}"

    quoted = [line.rstrip() for line in block.group(1).splitlines() if line.strip()]
    invented = [line for line in quoted if line not in produced]
    assert not invented, f"{document} quotes lines the tool does not print: {invented}"
