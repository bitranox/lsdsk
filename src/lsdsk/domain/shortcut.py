"""Whether to offer a desktop shortcut, as a pure decision.

The interactive launch path asks this before it draws anything, and every
platform's shortcut adapter shares the one answer. It reads nothing itself: the
three facts are passed in, so the rule is testable without a terminal, a config
file or a filesystem behind it.
"""

from __future__ import annotations

from typing import Final

#: The cooldown the desktop launcher pins, the same figure as lsdsk's own
#: ``[tool.uv].exclude-newer``. uv holds back every release younger than this
#: across the whole dependency tree, which is the supply-chain protection the
#: shortcut's consent text describes.
UVX_COOLDOWN: Final = "7 days"

#: lsdsk plus every package lsdsk owns in its dependency closure. A ``uvx`` run
#: started from the user's Desktop does not read lsdsk's published ``[tool.uv]``
#: (uv discovers config from the cwd and the user config, not from the installed
#: package), so the cooldown AND the exemptions that keep these uncooled must go
#: on the command line. Held equal to pyproject's ``exclude-newer-package = false``
#: entries by ``test_the_uvx_launch_command`` so the two cannot drift.
OWNED_CLOSURE: Final = (
    "lsdsk",
    "lib-cli-exit-tools",
    "lib-layered-config",
    "lib-log-rich",
    "pwshpy",
    "fake-winreg",
    "lib-registry",
)


def uvx_launch_args(uvx: str) -> tuple[str, ...]:
    """The argv a desktop shortcut runs to start lsdsk through ``uvx``.

    The resolved ``uvx`` executable leads, the cooldown and the owned-closure
    exemptions follow, and ``lsdsk@latest`` closes it. Pure: the caller resolves
    ``uvx`` to an absolute path and the platform adapter splits this into a
    target and its arguments however that platform's shortcut needs.

    Args:
        uvx: The absolute path to the ``uvx`` executable.

    Returns:
        The whole command, executable first.

    Example:
        >>> args = uvx_launch_args("/usr/bin/uvx")
        >>> args[0], args[1], args[2], args[-1]
        ('/usr/bin/uvx', '--exclude-newer', '7 days', 'lsdsk@latest')
    """
    args = [uvx, "--exclude-newer", UVX_COOLDOWN]
    for package in OWNED_CLOSURE:
        args += ["--exclude-newer-package", f"{package}=false"]
    args.append("lsdsk@latest")
    return tuple(args)


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


__all__ = ["OWNED_CLOSURE", "UVX_COOLDOWN", "should_offer", "uvx_launch_args"]
