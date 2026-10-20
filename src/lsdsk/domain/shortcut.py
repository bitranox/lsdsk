"""Whether to offer a desktop shortcut, as a pure decision.

The interactive launch path asks this before it draws anything, and every
platform's shortcut adapter shares the one answer. It reads nothing itself: the
three facts are passed in, so the rule is testable without a terminal, a config
file or a filesystem behind it.
"""

from __future__ import annotations


def should_offer(
    *,
    at_terminal: bool,
    offer_enabled: bool,
    shortcut_present: bool,
) -> bool:
    """Decide whether to offer to create a desktop shortcut.

    The offer fires only when someone can answer it, the configuration still
    permits it, and it has not already been done. Any one of those being false
    is reason enough to stay silent.

    Args:
        at_terminal: Whether an interactive terminal is attached to answer.
        offer_enabled: Whether ``display.offer_desktop_shortcut`` still permits it.
        shortcut_present: Whether the platform's shortcut already exists.

    Returns:
        ``True`` when the offer should be shown, ``False`` otherwise.

    Example:
        >>> should_offer(at_terminal=True, offer_enabled=True, shortcut_present=False)
        True
        >>> should_offer(at_terminal=True, offer_enabled=True, shortcut_present=True)
        False
    """
    return at_terminal and offer_enabled and not shortcut_present


__all__ = ["should_offer"]
