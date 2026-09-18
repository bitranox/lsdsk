"""The German pages must exist, resolve, switch language, and not be stale.

A translated page is a second copy of a claim, edited in a different file from
the one it describes, so it drifts in silence: nothing about a German sentence
looks wrong when the English one beside it has moved on. Every test here exists
because that failure has no other symptom.

The switcher rows get their own guard for the same reason. They are hand-written
into every page, and the repository already shows what happens to hand-written
cross-references: seven of its pages carried no way back to the README at all,
for as long as anyone had been reading them.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
GERMAN = ROOT / "de"

#: Documents declared for translation that do not have a German page yet. Empty
#: now that every declared document is translated, and it stays as the bucket a
#: NEW English document goes through: declare it, land the German page, remove
#: it from here. While a name sits in this tuple the guards below exempt it, so
#: leaving one behind is the way to have an unguarded German page.
PENDING: Final[tuple[str, ...]] = ()


def _manifest() -> ModuleType:
    """Load the manifest script by path.

    It lives in ``scripts/``, which is not a package, and importing it as one
    works under ``python -m pytest`` and dies under a bare ``pytest`` in CI.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("translation_manifest", ROOT / "scripts" / "translation_manifest.py")
    assert spec is not None and spec.loader is not None, "the manifest script is not where the tests expect it"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST: Final = _manifest()

#: Documents with a German page. Everything else is excluded or still pending.
TRANSLATED: Final[tuple[str, ...]] = tuple(name for name in MANIFEST.TRANSLATED if name not in PENDING)


def _relative_links(text: str) -> list[str]:
    """Every markdown link target in one document that is not an absolute URL."""
    return re.findall(r"\]\((?!https?://|mailto:)([^)\s]+)\)", text)


def _slug(heading: str) -> str:
    """Render a heading the way GitHub renders it into an anchor."""
    lowered = heading.strip().lower()
    stripped = re.sub(r"[^\w\s-]", "", lowered, flags=re.UNICODE)
    return re.sub(r"[\s]+", "-", stripped)


def _headings(path: Path) -> set[str]:
    """Every anchor one document offers."""
    text = path.read_text(encoding="utf-8")
    return {_slug(line.lstrip("#").strip()) for line in text.splitlines() if line.startswith("#")}


@pytest.mark.os_agnostic
def test_every_tracked_root_document_is_translated_excluded_or_pending() -> None:
    """A new English document cannot quietly go untranslated.

    Without this, adding a page means adding one that no reader of German ever
    learns about, and nothing anywhere records the decision not to translate it.
    """
    on_disk = {path.name for path in ROOT.glob("*.md")} - set(MANIFEST.LOCAL_ONLY)
    placed = set(MANIFEST.TRANSLATED) | set(MANIFEST.EXCLUDED)
    unplaced = sorted(on_disk - placed)

    assert not unplaced, f"neither translated nor excluded in scripts/translation_manifest.py: {unplaced}"
    assert on_disk, "the control: no root documents were found, so this asserted nothing"


@pytest.mark.os_agnostic
def test_every_translated_document_has_a_german_page_and_the_reverse() -> None:
    """One declaration, both directions, so neither half can drift out of the set."""
    declared = set(TRANSLATED)
    present = {path.name for path in GERMAN.glob("*.md")}

    assert declared, "the control: nothing is declared translated, so this asserted nothing"
    assert declared - present == set(), f"declared translated but missing from de/: {sorted(declared - present)}"
    assert present - declared == set(), f"in de/ but not declared translated: {sorted(present - declared)}"


@pytest.mark.os_agnostic
def test_every_relative_link_in_a_german_page_resolves() -> None:
    """A German page that links into nothing is worse than one nobody translated."""
    broken: list[str] = []
    checked = 0
    for page in sorted(GERMAN.glob("*.md")):
        for target in _relative_links(page.read_text(encoding="utf-8")):
            checked += 1
            if not (page.parent / target.split("#", 1)[0]).exists():
                broken.append(f"de/{page.name} -> {target}")

    assert checked, "the control: no relative links were found in any German page"
    assert not broken, f"broken links: {broken}"
    # The second control: the checker must be able to report a miss at all.
    assert not (GERMAN / "does-not-exist.md").exists(), "the control path unexpectedly exists"


@pytest.mark.os_agnostic
def test_no_german_page_links_to_an_english_page_that_has_a_german_twin() -> None:
    """Following a German page must keep the reader in German.

    The directory layout makes this the default - a bare name resolves inside
    ``de/`` - so a violation is always an explicit ``../``, which is exactly what
    somebody writes when they copy a line over from the English page.
    """
    twins = set(TRANSLATED)
    escaped: list[str] = []
    for page in sorted(GERMAN.glob("*.md")):
        for target in _relative_links(page.read_text(encoding="utf-8")):
            document = target.split("#", 1)[0]
            if document.startswith("../") and Path(document).name in twins and document != f"../{page.name}":
                escaped.append(f"de/{page.name} -> {target}")

    assert not escaped, f"German pages linking back to English pages that have a German version: {escaped}"


@pytest.mark.os_agnostic
def test_every_page_carries_the_language_switcher() -> None:
    """The switcher is the only way between the two sets, and it is hand-written."""
    missing: list[str] = []
    for name in TRANSLATED:
        english = (ROOT / name).read_text(encoding="utf-8")
        german = (GERMAN / name).read_text(encoding="utf-8")
        if f"**English** | [Deutsch](de/{name})" not in english:
            missing.append(name)
        if f"[English](../{name}) | **Deutsch**" not in german:
            missing.append(f"de/{name}")

    assert TRANSLATED, "the control: nothing is translated, so this asserted nothing"
    assert not missing, f"no language switcher in: {missing}"


@pytest.mark.os_agnostic
def test_every_anchor_a_german_link_carries_names_a_heading_that_exists() -> None:
    """A translated heading silently breaks the anchor that pointed at it."""
    broken: list[str] = []
    for page in sorted(GERMAN.glob("*.md")):
        for target in _relative_links(page.read_text(encoding="utf-8")):
            document, _, fragment = target.partition("#")
            if not fragment:
                continue
            destination = page.parent / document
            if destination.exists() and fragment.lower() not in _headings(destination):
                broken.append(f"de/{page.name} -> {target}")

    assert not broken, f"anchors naming no heading: {broken}"


@pytest.mark.os_agnostic
def test_the_german_pages_are_not_stale() -> None:
    """The English half moved and the German half was not read against it."""
    stale = [name for name in MANIFEST.drifted() if name not in PENDING]

    assert not stale, (
        f"English documents changed since translation: {stale}. Read the German page against the English, "
        "then: python scripts/translation_manifest.py --refresh <document>"
    )


@pytest.mark.os_agnostic
def test_the_manifest_records_every_translated_document() -> None:
    """The non-vacuity control for the staleness test, which an empty manifest passes."""
    recorded = MANIFEST.recorded()
    unrecorded = [name for name in TRANSLATED if name not in recorded]

    assert TRANSLATED, "the control: nothing is translated, so this asserted nothing"
    assert not unrecorded, f"translated but absent from de/TRANSLATIONS.toml: {unrecorded}"
