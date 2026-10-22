"""When lsdsk offers to drop a desktop shortcut.

The offer is a pure decision over four facts: whether someone is at a terminal to
answer, whether the ``display.offer_desktop_shortcut`` key still permits it,
whether the account has a desktop to put a shortcut on at all, and whether the
shortcut is already there. It fires only when all four line up; any one of them
is reason enough to stay silent. Keeping the decision pure and here lets every
platform's adapter share one answer, and lets this truth table stand without a
terminal, a config file or a filesystem behind it.
"""

from __future__ import annotations

import pytest

from lsdsk.domain.shortcut import should_offer


@pytest.mark.parametrize(
    ("at_terminal", "offer_enabled", "desktop_present", "shortcut_present", "expected"),
    [
        (True, True, True, False, True),  # all four line up: offer
        (False, True, True, False, False),  # not at a terminal: nobody can answer
        (True, False, True, False, False),  # the config key turned the offer off
        (True, True, False, False, False),  # a headless server: no desktop to put it on
        (True, True, True, True, False),  # the shortcut is already there
        (False, False, False, True, False),  # nothing lines up
    ],
)
def test_the_offer_fires_only_when_all_four_conditions_line_up(
    at_terminal: bool,
    offer_enabled: bool,
    desktop_present: bool,
    shortcut_present: bool,
    expected: bool,
) -> None:
    """``should_offer`` is the AND of at-a-terminal, enabled, a desktop, and not-already-present."""
    assert (
        should_offer(
            at_terminal=at_terminal,
            offer_enabled=offer_enabled,
            desktop_present=desktop_present,
            shortcut_present=shortcut_present,
        )
        is expected
    )
