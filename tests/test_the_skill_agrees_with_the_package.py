"""The skill's upper-case names have to be names this package really has.

A reference document that teaches a constant is read as an import instruction,
so a name the branch has since deleted does not degrade into vagueness: the
reader types it and gets `ImportError`. That is what happened to three wear and
counter figures, which the CHANGELOG records removing while the skill went on
naming them as the values the rules fall back to.

The check is deliberately WIDER than the writer who made the mistake: it does
not read a list of names anybody maintains, it assembles what the package
actually exports and asks the document to be a subset of it. A guard that
searched only for names its own list held could never find the one nobody wrote
down.
"""

from __future__ import annotations

import enum
import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import lsdsk

SKILL = Path(__file__).resolve().parents[1] / "skills" / "lsdsk" / "SKILL.md"

#: A backticked bare identifier in SCREAMING_SNAKE, which is how this document
#: writes a Python constant. Three characters minimum, so `GB` and the like are
#: not swept in as names.
CONSTANT = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")

#: Written as an environment variable rather than as an importable name, and
#: resolved by the configuration layer instead of by an import.
ENVIRONMENT_PREFIX = "LSDSK_"

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hw"


def reachable_names() -> set[str]:
    """Collect every upper-case name this package publishes.

    Module-level exports and enum MEMBERS both count: the document names
    `USAGE_ERROR` and `FREE` as members rather than as module attributes, and a
    reader reaches them through the enum that owns them.

    Returns:
        Every name a reader could reach, upper-case ones only.
    """
    found: set[str] = set()
    for module in pkgutil.walk_packages(lsdsk.__path__, prefix="lsdsk."):
        try:
            loaded = importlib.import_module(module.name)
        except ImportError:  # pragma: no cover - a platform-only transport
            continue
        for name in getattr(loaded, "__all__", ()):
            if name.isupper():
                found.add(name)
            member = getattr(loaded, name, None)
            if isinstance(member, type) and issubclass(member, enum.Enum):
                found.update(each.name for each in member)
    return found


def printed_words() -> set[str]:
    """Collect the upper-case words the tool actually prints.

    The document also writes output LITERALS in backticks - `FREE` is a cell in
    the slots table, not a name anybody imports - so a guard keyed on the
    package alone would report a correct sentence as a dead import.

    Returns:
        Every upper-case word the sections print over the committed captures.
    """
    from click.testing import CliRunner

    from lsdsk.adapters.cli import cli
    from lsdsk.composition import build_production

    words: set[str] = set()
    runner = CliRunner()
    for capture in sorted(FIXTURES.glob("*.json")):
        for section in ("slots", "findings", "health", "disks", "controllers"):
            result = runner.invoke(cli, [section, "--replay", str(capture)], obj=build_production)
            words.update(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", result.output))
    return words


@pytest.mark.os_agnostic
def test_every_constant_the_skill_names_is_one_this_package_has() -> None:
    """No name the document writes as a constant is absent from the package.

    Two sources, because the document legitimately backticks both a Python name
    and a word the tool prints, and neither source alone can tell them apart.
    """
    named = set(CONSTANT.findall(SKILL.read_text(encoding="utf-8")))
    named = {one for one in named if not one.startswith(ENVIRONMENT_PREFIX)}
    assert named, "the pattern matched nothing, so this test asserted nothing"

    reachable = reachable_names()
    # The control: a name the package certainly has, so a run that found
    # nothing reachable fails here rather than passing the assertion below.
    assert "SCHEMA_VERSION" in reachable, "the package surface came back empty"

    printed = printed_words()
    assert "FREE" in printed, "no section printed anything, so the second source asserted nothing"

    missing = sorted(named - reachable - printed)
    assert not missing, f"the skill names constants that lsdsk neither exports nor prints: {missing}"


@pytest.mark.os_agnostic
def test_the_skill_explains_every_marker_a_view_can_draw() -> None:
    """A symbol a reader meets on the page has to be explained in the document.

    The markers are assembled from `theme` rather than from a list kept here,
    because a document check that searches only for the symbols its own list
    holds can never find the one nobody wrote down - which is how the ceiling
    marker shipped with the panel drawing it and no page saying what it meant.
    """
    from lsdsk.adapters.render import theme

    drawn = {name: getattr(theme, name) for name in ("NOT_READ", "LEGACY", "NOT_APPLICABLE", "AT_MOST")}
    text = SKILL.read_text(encoding="utf-8")

    unexplained = sorted(name for name, symbol in drawn.items() if f"`{symbol}" not in text)
    assert not unexplained, f"the skill draws no explanation for these markers: {unexplained}"


TREND_PARAGRAPH = re.compile(r"`(?:lsdsk )?trend --format json` carries.*?(?:\n\n)", re.DOTALL)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("document", [SKILL, SKILL.parents[2] / "COMMANDS.md"], ids=["skill", "commands"])
def test_the_trend_json_paragraph_names_every_counter_the_trend_watches(document: Path) -> None:
    """A reader filtering `data.trend` by counter must find every value it can carry.

    The list is read from `WATCHED`, the tuple the trend rows are built from,
    rather than kept here: a counter added there and missed in the prose is
    exactly the drift this exists to catch, and `error_log_entries` was that
    counter - every NVMe drive with history emits it while the paragraph named
    six.
    """
    from lsdsk.adapters.render.trend import WATCHED

    match = TREND_PARAGRAPH.search(document.read_text(encoding="utf-8"))
    assert match, f"{document.name} has no paragraph describing trend --format json"
    paragraph = match.group(0)
    assert "`crc_errors`" in paragraph, "the paragraph matched but names no counter at all"

    unnamed = [str(kind) for kind in WATCHED if f"`{kind}`" not in paragraph]
    assert not unnamed, f"{document.name}'s trend JSON paragraph never names: {unnamed}"


@pytest.mark.os_agnostic
def test_the_skill_names_every_refusal_a_caller_can_distinguish() -> None:
    """Every ConfigurationError subclass is named where callers are told what to catch.

    Catching the base class is correct and sufficient, so this is not about
    correctness: a caller who wants to tell "the file is absent" from "the file
    is there and wrong" cannot learn that the distinction exists unless the
    subclass is named.
    """
    import inspect

    from lsdsk.domain import errors

    subclasses = sorted(
        name
        for name, member in vars(errors).items()
        if inspect.isclass(member)
        and issubclass(member, errors.ConfigurationError)
        and member is not errors.ConfigurationError
    )
    assert subclasses, "no subclasses were found, so this test asserted nothing"

    text = SKILL.read_text(encoding="utf-8")
    unnamed = [name for name in subclasses if f"`{name}`" not in text]
    assert not unnamed, f"the skill's exception guidance never names: {unnamed}"


@pytest.mark.os_agnostic
def test_the_skill_names_every_rule_diagnostics_exports() -> None:
    """The paragraph saying every rule is callable on its own has to list every rule.

    It reads as the whole set, so a rule it leaves out is one a reader never
    learns they can run alone.
    """
    from lsdsk.domain import diagnostics

    rules = sorted(name for name in diagnostics.__all__ if name.startswith("diagnose_"))
    assert len(rules) > 1, "diagnostics exported no rules, so this test asserted nothing"

    text = SKILL.read_text(encoding="utf-8")
    unnamed = [name for name in rules if f"`{name}(" not in text]
    assert not unnamed, f"the skill's list of callable rules never names: {unnamed}"


@pytest.mark.os_agnostic
def test_the_skill_names_every_option_the_cli_offers() -> None:
    """The skill is read on its own, so an option only COMMANDS.md names is one its reader never meets."""
    from test_documented_surface import cli_surface, names_an_option

    _, options = cli_surface()
    assert options, "the control: the click tree yielded no options, so this asserted nothing"

    text = SKILL.read_text(encoding="utf-8")
    unnamed = sorted(option for option in options if not names_an_option(text, option))
    assert not unnamed, f"accepted by the CLI and never named in the skill: {unnamed}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["TREND", "DISKS"])
def test_the_skill_quotes_the_refused_history_sentence_each_command_gives(command: str) -> None:
    """Every command judged against the history says when it could not be read, not only trend.

    The consequence clause is taken from the function that writes the sentence,
    so the skill is held to what the envelope really says for that command.
    """
    from lsdsk.adapters.cli.commands.scan import history_refusals
    from lsdsk.adapters.history.store import HistoryRead
    from lsdsk.domain.enums import CliCommand
    from lsdsk.domain.history import History

    refused = HistoryRead(History(hostname="h"), writable=False, refusal="malformed")
    (sentence,) = history_refusals(refused, CliCommand[command])
    consequence = sentence.split("could not be read, ", 1)[1].rsplit(": malformed", 1)[0]
    assert consequence.startswith("so "), f"the sentence changed shape: {sentence!r}"

    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    quoted = f"could not be read, {consequence}:"
    assert quoted in text, f"the skill never quotes the {command} sentence: {consequence!r}"


@pytest.mark.os_agnostic
def test_the_skill_states_the_sas_port_count_the_sample_capture_gives() -> None:
    """The SAS port-count advice quotes the 9500-16i's figure, so it must be the one the tool prints."""
    from lsdsk.adapters.hw import snapshot

    inventory = snapshot.load(FIXTURES / "linux-sas-hba.json")
    (hba,) = [controller for controller in inventory.controllers if "9500-16i" in controller.name]
    assert hba.port_count is not None, "the capture gave the HBA no port count, so this asserted nothing"

    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    paragraph = re.search(r"\*\*A SAS port count is[^*]+\*\*.*?(?=\*\*)", text)
    assert paragraph is not None, "the SAS port-count paragraph is gone"
    assert re.search(rf"\b{hba.port_count} host phys\b", paragraph.group(0)), (
        f"the paragraph does not state the {hba.port_count} host phys the capture gives: {paragraph.group(0)[:300]}"
    )
