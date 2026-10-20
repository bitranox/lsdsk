"""When lsdsk offers to drop a desktop shortcut.

The offer is a pure decision over three facts: whether someone is at a terminal to
answer, whether the ``display.offer_desktop_shortcut`` key still permits it, and
whether the shortcut is already there. It fires only when all three line up; any
one of them is reason enough to stay silent. Keeping the decision pure and here
lets every platform's adapter share one answer, and lets this truth table stand
without a terminal, a config file or a filesystem behind it.
"""

from __future__ import annotations

import pytest

from lsdsk.domain.shortcut import should_offer


@pytest.mark.parametrize(
    ("at_terminal", "offer_enabled", "shortcut_present", "expected"),
    [
        (True, True, False, True),  # all three line up: offer
        (False, True, False, False),  # not at a terminal: nobody can answer
        (True, False, False, False),  # the config key turned the offer off
        (True, True, True, False),  # the shortcut is already there
        (False, False, True, False),  # nothing lines up
    ],
)
def test_the_offer_fires_only_when_all_three_conditions_line_up(
    at_terminal: bool,
    offer_enabled: bool,
    shortcut_present: bool,
    expected: bool,
) -> None:
    """``should_offer`` is the AND of at-a-terminal, enabled and not-already-present."""
    assert (
        should_offer(
            at_terminal=at_terminal,
            offer_enabled=offer_enabled,
            shortcut_present=shortcut_present,
        )
        is expected
    )
