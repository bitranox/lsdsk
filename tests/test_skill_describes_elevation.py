"""The skill describes what a non-elevated Windows run loses, and the elevation that recovers it.

An agent reads ``skills/lsdsk/SKILL.md`` to decide what the tool does. A skill
still saying "no subprocesses" steers it away from the one process lsdsk now
starts, and one saying link speeds read unprivileged sends it hunting a fault
behind a dash that is only a missing privilege. Checked against the file's text
rather than by asking an agent: the machine that runs these tests carries the
same facts in its own context, so an agent would answer correctly from that and
prove nothing about the skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lsdsk.adapters.hw.elevation import RELAUNCH_MARKER
from lsdsk.adapters.render import report

SKILL = Path(__file__).parents[1] / "skills" / "lsdsk" / "SKILL.md"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_the_skill_no_longer_says_lsdsk_starts_no_process() -> None:
    assert "no subprocesses" not in _skill()


@pytest.mark.os_agnostic
def test_the_skill_says_what_a_refused_identify_costs_on_windows() -> None:
    """Named the way the tool itself names it, so the two cannot drift apart."""
    text = _skill()

    assert report.SATA_LINK_NEEDING_IDENTIFY in text
    assert "IDENTIFY" in text


@pytest.mark.os_agnostic
def test_the_skill_describes_the_elevation_and_how_to_turn_it_off() -> None:
    text = _skill()

    assert "NOT ELEVATED" in text, "the line the interactive view draws when it stayed unelevated"
    assert "auto_elevate" in text
    assert "--no-elevate" in text


@pytest.mark.os_agnostic
def test_the_skill_names_the_relaunch_marker_only_as_internal() -> None:
    """The relaunch marker is named once, as internal.

    Named, because an agent meets it on the elevated copy's command line; marked
    internal in the same sentence, because passing it only stops the asking.
    """
    sentence = next((line for line in _skill().splitlines() if RELAUNCH_MARKER in line), "")

    assert sentence, f"{RELAUNCH_MARKER} is accepted by the CLI and named nowhere in the skill"
    assert "internal" in sentence, sentence


def _elevation_paragraph() -> str:
    """The skill's paragraph about the view asking for elevation, found by the line it must draw.

    Its whitespace is collapsed, so a phrase the prose wraps across two lines is still one phrase.
    """
    paragraphs = _skill().split("\n\n")
    found = next((p for p in paragraphs if "NOT ELEVATED" in p and "--no-elevate" in p), "")
    return " ".join(found.split())


@pytest.mark.os_agnostic
def test_the_skill_says_linux_and_macos_ask_for_root_through_sudo() -> None:
    """A skill saying the view never asks off Windows tells an agent a Linux user will never see a password prompt."""
    paragraph = _elevation_paragraph()

    assert paragraph, "no paragraph describes the elevation"
    assert "off Windows" not in paragraph, paragraph
    assert "sudo" in paragraph, paragraph
    assert "same terminal" in paragraph, "where the root copy runs, which differs from Windows' new window"


@pytest.mark.os_agnostic
def test_the_skill_says_a_container_is_not_asked() -> None:
    """Root reads nothing more where no device node exists, so the view does not ask for a password there."""
    paragraph = _elevation_paragraph()

    assert "container" in paragraph, paragraph


@pytest.mark.os_agnostic
def test_the_skill_does_not_place_the_one_process_lsdsk_starts_on_windows_only() -> None:
    assert "the interactive view on Windows relaunches" not in _skill()
