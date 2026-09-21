"""Which English text each German page was translated from, and whether it moved.

A translated page is a claim about a document that is edited somewhere else, so
it goes stale in silence: the English sentence changes, the German one does not,
and a reader is shown behaviour the tool no longer has with nothing anywhere
saying so.  This records the SHA-256 of the English text each German page was
written from, and the gate reads it back to name the halves that have parted.

Usage::

    python scripts/translation_manifest.py --check
    python scripts/translation_manifest.py --refresh SECURITY.md
    python scripts/translation_manifest.py --refresh-all

``--refresh`` records that the German page has been read against the CURRENT
English text.  That is the whole content of the claim: refreshing without
reading turns the guard into a rubber stamp, which is the one way it fails while
staying green.

System Role:
    Development tooling.  Not imported by the package, and not part of its API.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Final

from rich.console import Console

__all__ = [
    "EXCLUDED",
    "GERMAN",
    "MANIFEST",
    "ROOT",
    "TRANSLATED",
    "digest_of",
    "drifted",
    "main",
    "recorded",
    "render_manifest",
    "tracked_root_documents",
]

#: Printing goes through rich rather than ``print``, which the lint rules refuse.
SAY: Final = Console()

ROOT: Final = Path(__file__).resolve().parent.parent
GERMAN: Final = ROOT / "de"
MANIFEST: Final = GERMAN / "TRANSLATIONS.toml"

#: The documents that carry a German twin.  This tuple is the declaration, and
#: the manifest holds only hashes for it, so a page added to ``de/`` without
#: being declared here fails rather than being silently unguarded.
TRANSLATED: Final[tuple[str, ...]] = (
    "COMMANDS.md",
    "CONFIG.md",
    "CONTRIBUTING.md",
    "DEVELOPMENT.md",
    "FINDINGS.md",
    "INSTALL.md",
    "PAGES.md",
    "README.md",
    "REPORT.md",
    "SECURITY.md",
    "WHY.md",
    "ai-stance.md",
    "ai-transparency.md",
)

#: A tracked root document deliberately left in English, with the reason.  Every
#: tracked root document is in exactly one of these two, so a NEW one has to be
#: placed in one of them rather than quietly going untranslated.
EXCLUDED: Final[dict[str, str]] = {
    "CHANGELOG.md": (
        "half of all documentation by volume, and it regrows in English at every "
        "release, so a German copy would need re-translating per release forever"
    ),
}


def tracked_root_documents(root: Path) -> frozenset[str]:
    """The root-level Markdown documents git tracks.

    The completeness check is about the documents a CLONE has, and that is
    exactly the set git tracks.  Reading the filesystem instead and subtracting
    a hand-written list of developer-only files keeps the same fact twice: the
    list goes stale the moment a new gitignored document appears at the root,
    and the failure then names the new document rather than the stale list.
    That is not hypothetical - a rotated ``handover.prev.md`` reddened this
    gate 75 seconds after the run that had just passed.

    Args:
        root: The repository to ask.  Passed rather than defaulted so a test
            can point it at a throwaway repository and exercise the real git.

    Returns:
        Every tracked ``*.md`` sitting directly at ``root``, by bare name.

    Raises:
        RuntimeError: If git is absent or cannot answer.  An empty answer would
            otherwise be indistinguishable from a repository holding no
            documents at all, which is the shape that passes while checking
            nothing.

    Examples:
        >>> "README.md" in tracked_root_documents(ROOT)
        True
        >>> any("/" in name for name in tracked_root_documents(ROOT))
        False
    """
    git = shutil.which("git")
    if git is None:
        msg = "git is not on PATH, so the tracked root documents cannot be listed"
        raise RuntimeError(msg)
    listing = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [git, "-C", str(root), "-c", "core.quotePath=false", "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if listing.returncode != 0:
        msg = f"git could not list the tracked files of {root}: {listing.stderr.strip()}"
        raise RuntimeError(msg)
    # -z because the default quotes any non-ASCII path ("a/\303\244.md"), which
    # would silently drop a document rather than reporting it.
    names = (entry for entry in listing.stdout.split("\0") if entry)
    return frozenset(name for name in names if "/" not in name and name.endswith(".md"))


def digest_of(path: Path) -> str:
    """Return the SHA-256 of one document's bytes.

    Args:
        path: The file to hash.

    Returns:
        The hex digest, or an empty string when the file is not there.
    """
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded() -> dict[str, str]:
    """Return the digest each German page was translated from, by document name.

    Returns:
        A name to hex-digest mapping, empty when no manifest has been written.
    """
    if not MANIFEST.is_file():
        return {}
    parsed = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    sources = parsed.get("sources", {})
    return {str(name): str(value) for name, value in sources.items()}


def drifted() -> list[str]:
    """Return every document whose English text has moved since it was translated.

    A document with no entry at all counts as drifted rather than as passing:
    an absent claim and a broken one both mean the German page is unvouched for.

    Returns:
        The document names, in declaration order.
    """
    entries = recorded()
    return [name for name in TRANSLATED if entries.get(name) != digest_of(ROOT / name)]


def render_manifest(entries: dict[str, str]) -> str:
    """Render the manifest, sorted, with the note a reader of it needs.

    Args:
        entries: A document name to hex-digest mapping.

    Returns:
        The file's whole text.
    """
    header = (
        "# The English text each German page under de/ was translated from.\n"
        "#\n"
        "# Generated, never hand-edited.  A mismatch means the English half moved\n"
        "# and the German one has not been read against it since:\n"
        "#\n"
        "#     python scripts/translation_manifest.py --check\n"
        "#     python scripts/translation_manifest.py --refresh <document>\n"
        "\n[sources]\n"
    )
    rows = "".join(f'"{name}" = "{entries[name]}"\n' for name in sorted(entries))
    return header + rows


def _refresh(names: tuple[str, ...]) -> int:
    """Re-record the English digest for each named document.

    Args:
        names: The documents to refresh.

    Returns:
        A process exit code.
    """
    unknown = [name for name in names if name not in TRANSLATED]
    if unknown:
        SAY.print(f"not translated documents: {', '.join(unknown)}", style="red")
        return 2
    entries = recorded()
    for name in names:
        entries[name] = digest_of(ROOT / name)
    GERMAN.mkdir(exist_ok=True)
    MANIFEST.write_text(render_manifest(entries), encoding="utf-8")
    SAY.print(f"recorded {len(names)} of {len(TRANSLATED)} documents")
    return 0


def _check() -> int:
    """Report every document whose German page is unvouched for.

    Returns:
        A process exit code, 0 when nothing has drifted.
    """
    stale = drifted()
    if not stale:
        SAY.print(f"all {len(TRANSLATED)} German pages match the English they were written from")
        return 0
    SAY.print(f"{len(stale)} English document(s) moved since translation:", style="red")
    for name in stale:
        SAY.print(f"  {name}  ->  de/{name}")
    SAY.print("read the German page against it, then: python scripts/translation_manifest.py --refresh <document>")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Check the manifest, or re-record part of it.

    Args:
        argv: Command-line arguments, or ``None`` to read ``sys.argv``.

    Returns:
        A process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report documents whose English half moved")
    parser.add_argument("--refresh", metavar="DOCUMENT", help="re-record one document after reading its German page")
    parser.add_argument("--refresh-all", action="store_true", help="re-record every document")
    namespace = parser.parse_args(argv)
    if namespace.refresh_all:
        return _refresh(TRANSLATED)
    if namespace.refresh:
        return _refresh((str(namespace.refresh),))
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
