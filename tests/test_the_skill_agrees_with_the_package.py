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
