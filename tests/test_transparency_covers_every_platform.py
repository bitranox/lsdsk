"""The transparency page accounts for every platform this tool reads hardware on.

README's first sentence calls lsdsk a storage diagnostic for Linux and Windows,
and this page promises to say what was verified on real hardware and what was
not. A page that answers for one of the two is worse than one that answers for
neither: its checked list reads as the whole answer, so a reader on the platform
it omits is told nothing while believing they were told everything.

It goes missing silently, which is why this is a test rather than a habit. The
Windows bullet was removed as a side effect of a translation round trip, in a
commit whose own message says it was the only place that said so, and nothing
failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lsdsk.domain.enums import Platform

ROOT = Path(__file__).resolve().parents[1]

#: Each page of this document and the heading its verification section opens
#: with. Both languages, because the loss happened in the translation of one
#: into the other and a guard on the English alone would not have seen it.
PAGES: dict[str, str] = {
    "ai-transparency.md": "## What's been checked, and what hasn't",
    "de/ai-transparency.md": "## Was geprüft ist, und was nicht",
}


def _verification_section(page: str, heading: str) -> str:
    """The text from that heading to the next one, or the end of the page."""
    text = (ROOT / page).read_text(encoding="utf-8")
    start = text.index(heading)
    rest = text.find("\n## ", start + len(heading))
    return text[start:] if rest == -1 else text[start:rest]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("page", sorted(PAGES))
def test_the_verification_section_names_every_platform_lsdsk_reads(page: str) -> None:
    """Every platform with a reader is accounted for, checked or not.

    The expected set is :class:`Platform`, which is the closed set the capture
    format and the replay dispatch already share, so adding a reader for a third
    platform fails this until the page says what was done about it. Asking the
    page instead of a list written here is what keeps that true.
    """
    section = _verification_section(page, PAGES[page])
    assert len(section.splitlines()) > 5, f"{page}: the section read as {len(section)} characters, so the heading moved"

    unaccounted = [member.name.capitalize() for member in Platform if member.name.lower() not in section.lower()]

    assert not unaccounted, f"{page} says nothing about {unaccounted} while lsdsk has a reader for it"
